"""Stage 32 选品顾问/商品对比回归测试。

锁定四件事：
1. 槽位理解：预算归一化（千/万/k/w、以内/不超过）、品类词表（长词优先）、
   对比两项捕获与噪声剥离；
2. 硬约束红线：预算内+有货才进候选、无命中/服务故障宁缺勿编不伪造；
3. 回复组装：价格升序、trade-off、对比引导；对比找不齐如实说明；
4. 路由与注册表契约：DONE 才进 product_answer、required_slots 升级生效。
"""

import pytest

from app.chat.graph.nodes.product_answer import (
    ADVISOR_INTENTS,
    _compare,
    _parse_budget_yuan,
    _recommend,
)
from app.chat.intent.types import IntentLabel
from app.chat.skills.registry import skill_registry
from app.chat.slots.extractor import slot_extractor
from app.product.provider import ProductInfo


# ---------------- 槽位理解 ----------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("预算3000帮我推荐个空调", 3000),
        ("3000以内", 3000),
        ("不超过2000元", 2000),
        ("预算大概2千", 2000),
        ("1万以内的笔记本", 10000),
        ("500左右", 500),
        ("预算3k", 3000),
        ("帮我推荐一款风扇", None),  # 无预算表达
        ("订单号12345678", None),  # 订单号不是预算
    ],
)
def test_budget_extraction(text, expected):
    assert slot_extractor.extract(text).get("budget") == expected


def test_category_extraction_longest_first():
    assert slot_extractor.extract("推荐个空气净化器")["category"] == "空气净化器"
    assert slot_extractor.extract("想买台风扇")["category"] == "风扇"
    assert "category" not in slot_extractor.extract("帮我推荐一款好用的")


def test_one_shot_recommend_slots():
    """一次性表达两槽位同轮抽满（免追问直接出候选的前提）。"""
    slots = slot_extractor.extract("预算300以内帮我推荐个风扇")
    assert slots["budget"] == 300 and slots["category"] == "风扇"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("帮我对比凉风X1和凉风X2", "凉风X1|凉风X2"),
        ("凉风X1 vs 凉风X2哪个好", "凉风X1|凉风X2"),
        ("对比一下凉风X1跟凉风X2的区别", "凉风X1|凉风X2"),
    ],
)
def test_compare_items_extraction(text, expected):
    assert slot_extractor.extract(text).get("compare_items") == expected


def test_parse_budget_yuan():
    assert _parse_budget_yuan(300) == 300
    assert _parse_budget_yuan("300") == 300
    assert _parse_budget_yuan("") is None
    assert _parse_budget_yuan(None) is None
    assert _parse_budget_yuan("三百") is None


# ---------------- 推荐回复（fake provider） ----------------


def _p(name, price_cents, stock=5, attrs=None, desc=None):
    return ProductInfo(
        id=name, name=name, price_cents=price_cents, stock=stock,
        category="风扇", attrs=attrs or {}, description=desc,
    )


class _FakeProvider:
    def __init__(self, products=None, error=False):
        self._products = products or []
        self._error = error

    async def advise(self, session, tenant_id, category, budget_max_cents, limit=4):
        if self._error:
            raise RuntimeError("db down")
        return self._products

    async def search(self, session, tenant_id, query, limit=5):
        if self._error:
            raise RuntimeError("db down")
        return [p for p in self._products if query in p.name][:limit]


def _state():
    return {"tenant_id": "t1", "locale": None, "normalized_text": ""}


async def test_recommend_reply_sorted_with_tradeoff(monkeypatch):
    import app.chat.graph.nodes.product_answer as node

    products = [_p("凉风mini", 19900, desc="桌面小风扇"), _p("凉风Pro", 29900)]
    monkeypatch.setattr(node, "product_provider", _FakeProvider(products))
    out = await _recommend(None, "t1", _state(), {"category": "风扇", "budget": 300})
    assert "找到 2 款" in out["reply"]
    assert "凉风mini" in out["reply"] and "199.00 元" in out["reply"]
    assert "价格最低" in out["reply"] and "对比" in out["reply"]  # trade-off + 引导
    assert out["answer_source"] == "product_db"
    assert out["retrieval"]["advisor"]["budget_yuan"] == 300


async def test_recommend_no_hit_honest(monkeypatch):
    import app.chat.graph.nodes.product_answer as node

    monkeypatch.setattr(node, "product_provider", _FakeProvider([]))
    out = await _recommend(None, "t1", _state(), {"category": "风扇", "budget": 50})
    assert "暂时没有" in out["reply"] and "调整预算" in out["reply"]


async def test_recommend_provider_error_no_fabrication(monkeypatch):
    """商品服务故障时不伪造候选（包 stage-32 验收）。"""
    import app.chat.graph.nodes.product_answer as node

    monkeypatch.setattr(node, "product_provider", _FakeProvider(error=True))
    out = await _recommend(None, "t1", _state(), {"category": "风扇", "budget": 300})
    assert "暂时不可用" in out["reply"]
    assert out["retrieval"]["degraded"] is True


# ---------------- 对比回复 ----------------


async def test_compare_reply_both_found(monkeypatch):
    import app.chat.graph.nodes.product_answer as node

    products = [_p("凉风X1", 259900, attrs={"匹数": "1.5"}), _p("凉风X2", 299900)]
    monkeypatch.setattr(node, "product_provider", _FakeProvider(products))
    out = await _compare(None, "t1", _state(), {"compare_items": "凉风X1|凉风X2"})
    assert "凉风X1" in out["reply"] and "凉风X2" in out["reply"]
    assert "2599.00 元" in out["reply"]
    assert "以商品页为准" in out["reply"]


async def test_compare_missing_honest(monkeypatch):
    import app.chat.graph.nodes.product_answer as node

    monkeypatch.setattr(node, "product_provider", _FakeProvider([_p("凉风X1", 259900)]))
    out = await _compare(None, "t1", _state(), {"compare_items": "凉风X1|不存在的Y9"})
    assert "不存在的Y9" in out["reply"] and "没有在商品库中找到" in out["reply"]


# ---------------- 路由与注册表契约 ----------------


def test_advisor_intents_and_registry_contract():
    assert ADVISOR_INTENTS == {IntentLabel.PRODUCT_RECOMMEND, IntentLabel.PRODUCT_COMPARE}
    recommend = skill_registry.get(IntentLabel.PRODUCT_RECOMMEND)
    assert recommend.required_slots == ["category", "budget"]
    assert "collect" in recommend.templates
    compare = skill_registry.get(IntentLabel.PRODUCT_COMPARE)
    assert compare.required_slots == ["compare_items"]
    assert "collect" in compare.templates


def test_builder_routes_advisor_only_when_done():
    """补槽轮不进商品节点（R 矩阵纪律）；DONE 才路由 product_answer。"""
    from app.chat.graph.builder import _route_after_skill
    from app.chat.state.types import TurnStatus

    done = {
        "intent_result": {"pred_label": IntentLabel.PRODUCT_RECOMMEND},
        "status": TurnStatus.DONE,
    }
    collecting = {
        "intent_result": {"pred_label": IntentLabel.PRODUCT_RECOMMEND},
        "status": TurnStatus.NEEDS_SLOT,
    }
    assert _route_after_skill(done) == "product_answer"
    assert _route_after_skill(collecting) != "product_answer"
