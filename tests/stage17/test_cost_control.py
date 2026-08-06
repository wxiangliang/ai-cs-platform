"""Stage 17 LLM 成本控制单测：分级路由 / token 预算熔断 / 语义缓存。

真实 Redis + hash embedding（确定性）；LLM 不真实调用（只测收口逻辑与降级）。
"""

import uuid

import pytest

from app.cache.redis_client import close_redis, init_redis
from app.chat.cache.semantic_cache import (
    RedisSemanticCache,
    _cosine,
    get_semantic_cache,
    is_cacheable_source,
)
from app.chat.llm import budget
from app.chat.llm.budget import (
    account_llm_result,
    extract_token_usage,
    get_current_tenant,
    is_over_budget,
    record_usage,
    set_current_tenant,
)
from app.chat.llm.factory import _model_for_purpose
from app.core.config import settings


@pytest.fixture
async def _redis():
    await init_redis()
    yield
    await close_redis()


def _tenant() -> str:
    return f"t-{uuid.uuid4().hex[:8]}"


class _FakeResult:
    """模拟 LangChain 返回消息，仅带 usage 元数据。"""

    def __init__(self, usage_metadata=None, response_metadata=None):
        self.usage_metadata = usage_metadata
        self.response_metadata = response_metadata or {}
        self.content = "hello"


# ---------------- 2.3 模型分级路由 ----------------


def test_tier_default_falls_back_to_chat_model(monkeypatch):
    """未配置 fast/smart → 全部回落 CHAT_MODEL（零回归）。"""
    monkeypatch.setattr(settings, "CHAT_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(settings, "CHAT_MODEL_FAST", "")
    monkeypatch.setattr(settings, "CHAT_MODEL_SMART", "")
    assert _model_for_purpose("classify") == "gpt-4o-mini"
    assert _model_for_purpose("generate") == "gpt-4o-mini"


def test_tier_routing_fast_and_smart(monkeypatch):
    """classify→fast，generate→smart。"""
    monkeypatch.setattr(settings, "CHAT_MODEL", "base")
    monkeypatch.setattr(settings, "CHAT_MODEL_FAST", "small")
    monkeypatch.setattr(settings, "CHAT_MODEL_SMART", "large")
    assert _model_for_purpose("classify") == "small"
    assert _model_for_purpose("generate") == "large"


def test_tier_classify_falls_back_to_smart_when_no_fast(monkeypatch):
    """只配 smart 未配 fast → classify 也回落 smart。"""
    monkeypatch.setattr(settings, "CHAT_MODEL", "base")
    monkeypatch.setattr(settings, "CHAT_MODEL_FAST", "")
    monkeypatch.setattr(settings, "CHAT_MODEL_SMART", "large")
    assert _model_for_purpose("classify") == "large"
    assert _model_for_purpose("generate") == "large"


# ---------------- 2.2 token 预算与熔断 ----------------


def test_extract_token_usage_variants():
    assert extract_token_usage(_FakeResult(usage_metadata={"total_tokens": 42})) == 42
    assert (
        extract_token_usage(
            _FakeResult(usage_metadata={"input_tokens": 10, "output_tokens": 5})
        )
        == 15
    )
    assert (
        extract_token_usage(
            _FakeResult(response_metadata={"token_usage": {"total_tokens": 7}})
        )
        == 7
    )
    assert extract_token_usage(_FakeResult()) == 0


def test_tenant_contextvar_roundtrip():
    set_current_tenant("t-abc")
    assert get_current_tenant() == "t-abc"
    set_current_tenant("")
    assert get_current_tenant() == ""


async def test_budget_disabled_is_fail_open(_redis, monkeypatch):
    """未开启预算 → 永不超限（放行）。"""
    monkeypatch.setattr(settings, "LLM_BUDGET_ENABLED", False)
    monkeypatch.setattr(settings, "LLM_BUDGET_DAILY_TOKENS", 1)
    tenant = _tenant()
    await record_usage(tenant, 100)  # 未开启时不累计
    assert await is_over_budget(tenant) is False


async def test_budget_accumulate_and_trip(_redis, monkeypatch):
    """累计到日预算 → 熔断（is_over_budget=True）。"""
    monkeypatch.setattr(settings, "LLM_BUDGET_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_BUDGET_DAILY_TOKENS", 100)
    tenant = _tenant()
    assert await is_over_budget(tenant) is False
    await record_usage(tenant, 60)
    assert await is_over_budget(tenant) is False
    await record_usage(tenant, 50)  # 累计 110 ≥ 100
    assert await is_over_budget(tenant) is True


async def test_budget_no_tenant_or_zero_limit_fail_open(_redis, monkeypatch):
    monkeypatch.setattr(settings, "LLM_BUDGET_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_BUDGET_DAILY_TOKENS", 0)  # 0=不限
    assert await is_over_budget(_tenant()) is False
    monkeypatch.setattr(settings, "LLM_BUDGET_DAILY_TOKENS", 5)
    assert await is_over_budget("") is False  # 无租户


async def test_account_llm_result_records(_redis, monkeypatch):
    """account_llm_result 从结果取 token 并累计到当前租户。"""
    monkeypatch.setattr(settings, "LLM_BUDGET_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_BUDGET_DAILY_TOKENS", 50)
    tenant = _tenant()
    set_current_tenant(tenant)
    await account_llm_result("generate", _FakeResult(usage_metadata={"total_tokens": 80}))
    assert await is_over_budget(tenant) is True
    set_current_tenant("")


async def test_budget_check_fail_open_when_redis_down(monkeypatch):
    """Redis 故障 → is_over_budget fail-open 放行。"""
    monkeypatch.setattr(settings, "LLM_BUDGET_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_BUDGET_DAILY_TOKENS", 1)
    monkeypatch.setattr(
        "app.cache.redis_client.get_redis_client",
        lambda: (_ for _ in ()).throw(RuntimeError("down")),
    )
    assert await is_over_budget(_tenant()) is False


# ---------------- 2.1 语义缓存 ----------------


def test_cosine_basics():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine([], []) == 0.0
    assert _cosine([1.0], [1.0, 2.0]) == 0.0  # 维度不一致


def test_cacheable_source_whitelist():
    assert is_cacheable_source("faq")
    assert is_cacheable_source("rag_llm")
    assert is_cacheable_source("rag_extract")
    assert is_cacheable_source("chitchat")
    assert not is_cacheable_source("product")  # 价格/库存事实禁缓存
    assert not is_cacheable_source("tool")
    assert not is_cacheable_source("action")
    assert not is_cacheable_source("refused")
    assert not is_cacheable_source(None)


async def test_cache_store_then_hit_same_query(_redis, monkeypatch):
    """同一查询存后再查 → 命中（cosine=1.0 ≥ 阈值）。"""
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_THRESHOLD", 0.92)
    cache = RedisSemanticCache()
    tenant, q = _tenant(), "退款多久到账"
    await cache.store(tenant, q, {"reply": "3-5 个工作日", "source": "faq", "citations": []})
    hit = await cache.lookup(tenant, q)
    assert hit is not None
    assert hit["reply"] == "3-5 个工作日"
    assert hit["source"] == "faq"
    assert hit["score"] >= 0.92


async def test_cache_miss_different_query(_redis, monkeypatch):
    """措辞差异大的查询 → 未命中。"""
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_THRESHOLD", 0.92)
    cache = RedisSemanticCache()
    tenant = _tenant()
    await cache.store(tenant, "退款多久到账", {"reply": "x", "source": "faq", "citations": []})
    assert await cache.lookup(tenant, "苹果手机有什么颜色可选") is None


async def test_cache_skips_non_cacheable_source(_redis, monkeypatch):
    """product 等事实来源不写缓存 → 后续查不到。"""
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", True)
    cache = RedisSemanticCache()
    tenant, q = _tenant(), "iPhone 库存还有吗"
    await cache.store(tenant, q, {"reply": "有货", "source": "product", "citations": []})
    assert await cache.lookup(tenant, q) is None


async def test_cache_skips_personalized_entry(_redis, monkeypatch):
    """个性化回答（注入过用户记忆）不写租户共享缓存 → 防跨用户泄漏红线。"""
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", True)
    cache = RedisSemanticCache()
    tenant, q = _tenant(), "退货运费谁承担"
    await cache.store(
        tenant, q,
        {"reply": "您的 iPhone 15 订单可免运费退货", "source": "rag_llm",
         "citations": [], "personalized": True},
    )
    assert await cache.lookup(tenant, q) is None


async def test_rag_answer_personalized_flag():
    """RagAnswer.personalized：仅「LLM 生成 + 注入了长期事实」的回答被标记。

    锁定 rag_answer 节点跳过写缓存的判定依据（跨用户泄漏修复回归）。
    """
    from app.kb.answerer import RagAnswerer
    from app.kb.retriever import kb_retriever
    from app.kb.types import Hit

    answerer = RagAnswerer()
    hit = Hit(
        id="c1", score=0.95, source_backend="milvus",
        document_id="d1", title="退货政策", content="七天无理由",
    )

    async def _no_faq(session, tenant_id, query, trace, query_vec=None):
        return None

    async def _hits(session, tenant_id, query, trace, query_vec=None):
        return [hit]

    async def _gen(query, hits, memory=None, guidelines=None):
        return "生成的回答"

    import unittest.mock as mock

    with (
        mock.patch.object(kb_retriever, "search_faq", _no_faq),
        mock.patch.object(kb_retriever, "search_chunks", _hits),
        mock.patch.object(RagAnswerer, "_generate", staticmethod(_gen)),
    ):
        # 注入了长期事实 → personalized，禁止进共享缓存
        answer, _ = await answerer.answer(
            None, "t1", "退货运费", memory={"long_term_facts": ["用户买过 iPhone 15"]}
        )
        assert answer is not None and answer.personalized is True
        # 无记忆 → 可缓存
        answer, _ = await answerer.answer(None, "t1", "退货运费", memory=None)
        assert answer is not None and answer.personalized is False


async def test_cache_disabled_returns_none(_redis, monkeypatch):
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", False)
    cache = RedisSemanticCache()
    tenant, q = _tenant(), "退款多久到账"
    await cache.store(tenant, q, {"reply": "x", "source": "faq", "citations": []})
    assert await cache.lookup(tenant, q) is None


async def test_cache_invalidate_clears_tenant(_redis, monkeypatch):
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", True)
    cache = RedisSemanticCache()
    tenant, q = _tenant(), "退款多久到账"
    await cache.store(tenant, q, {"reply": "x", "source": "faq", "citations": []})
    assert await cache.lookup(tenant, q) is not None
    await cache.invalidate(tenant)
    assert await cache.lookup(tenant, q) is None


async def test_cache_tenant_isolation(_redis, monkeypatch):
    """A 租户缓存 B 租户查不到（严格隔离）。"""
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", True)
    cache = RedisSemanticCache()
    ta, tb, q = _tenant(), _tenant(), "退款多久到账"
    await cache.store(ta, q, {"reply": "x", "source": "faq", "citations": []})
    assert await cache.lookup(tb, q) is None


async def test_cache_lookup_fail_open_when_redis_down(monkeypatch):
    """缓存后端故障 → lookup 返回 None（fail-open，走正常链路）。"""
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", True)
    monkeypatch.setattr(
        "app.cache.redis_client.get_redis_client",
        lambda: (_ for _ in ()).throw(RuntimeError("down")),
    )
    cache = RedisSemanticCache()
    assert await cache.lookup(_tenant(), "退款多久到账") is None


def test_get_semantic_cache_is_singleton():
    assert get_semantic_cache() is get_semantic_cache()


async def test_cache_char_overlap_hits_with_lower_threshold(_redis, monkeypatch):
    """字面高度重叠的近义问法在较低阈值下命中（真实语义 embedding 可提升泛化）。"""
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_THRESHOLD", 0.5)
    cache = RedisSemanticCache()
    tenant = _tenant()
    await cache.store(
        tenant, "退款多久到账", {"reply": "3-5 天", "source": "faq", "citations": []}
    )
    hit = await cache.lookup(tenant, "退款多久能到账")
    assert hit is not None and hit["reply"] == "3-5 天"


def test_budget_module_exposes_contextvar():
    """契约锚点：budget 模块暴露租户上下文收口。"""
    assert hasattr(budget, "set_current_tenant")
    assert hasattr(budget, "get_current_tenant")
