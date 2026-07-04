"""Stage 13 第二/三批整改单元测试：吊销/脱敏/弱确认/建单并发/MCP 策略已在各自模块覆盖。"""

import uuid


from app.chat.graph.nodes.confirmation_parse import confirmation_parse
from app.chat.intent.types import IntentLabel
from app.chat.logging.decision_logger import build_log_data
from app.chat.tools.base import mask_sensitive
from app.core import auth as auth_mod
from app.core.auth import AuthContext, _cache_get, _cache_put
from app.core.config import settings
from app.db.session import AsyncSessionLocal, dispose_engine
from app.repositories.chat_handoff_ticket_repository import chat_handoff_ticket_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.services.handoff_service import REASON_USER_REQUEST, handoff_service

TENANT = "hard2-t"


# ---------------------------------------------------------------------------
# 脱敏扩展：地址打码 + decision_log 落库脱敏
# ---------------------------------------------------------------------------


def test_mask_address_field():
    masked = mask_sensitive({"new_address": "浙江省杭州市余杭区文一西路 969 号 8 幢 301 室"})
    assert masked["new_address"].startswith("浙江省杭州市")
    assert "969" not in masked["new_address"] and "＊" in masked["new_address"]
    # 非地址字段不受影响
    assert mask_sensitive({"note": "文一西路 969 号"})["note"] == "文一西路 969 号"


def test_decision_log_masks_pii():
    """决策日志落库前：原文手机号打码、地址槽位打码（回放/回流同口径受益）。"""
    data = build_log_data(
        {
            "tenant_id": "t1",
            "session_id": "s1",
            "message": "帮我改地址，手机 13800138000",
            "normalized_text": "帮我改地址，手机13800138000",
            "slots": {"new_address": "北京市朝阳区望京 SOHO 塔 3-2201", "order_id": "SO1"},
        }
    )
    assert "138****8000" in data.original_text
    assert "2201" not in data.slot_result_json["new_address"]
    assert data.slot_result_json["order_id"] == "SO1"  # 业务必需字段保留


# ---------------------------------------------------------------------------
# 鉴权：LRU 缓存 + 吊销版本
# ---------------------------------------------------------------------------


def test_auth_cache_lru_eviction(monkeypatch):
    """缓存满时逐出最久未用，不再整表清空（防 bcrypt 惊群）。"""
    monkeypatch.setattr(auth_mod, "_CACHE_MAX", 3)
    auth_mod._cache.clear()
    for i in range(3):
        _cache_put(f"d{i}", 0, AuthContext(tenant_id="t", key_id=f"k{i}"))
    _cache_get("d0")  # 触达 d0 → d1 变为最久未用
    _cache_put("d3", 0, AuthContext(tenant_id="t", key_id="k3"))
    assert _cache_get("d0") is not None and _cache_get("d3") is not None
    assert _cache_get("d1") is None  # 被 LRU 逐出
    auth_mod._cache.clear()


async def test_revocation_invalidates_cache(monkeypatch):
    """吊销版本 +1 后，缓存命中被作废（走全量重验）。"""
    from app.cache.redis_client import close_redis, init_redis

    await init_redis()
    try:
        auth_mod._cache.clear()
        key_id = f"ak_test{uuid.uuid4().hex[:6]}"
        ctx = AuthContext(tenant_id="t1", key_id=key_id, scopes=["chat"])
        rev0 = await auth_mod._current_revocation(key_id)
        assert rev0 == 0
        _cache_put("digest-x", rev0, ctx)
        # 未吊销：版本一致，缓存有效
        cached = _cache_get("digest-x")
        assert cached is not None and (await auth_mod._current_revocation(key_id)) == cached[0]
        # 吊销：版本 +1，与缓存版本不一致（get_auth_context 据此作废缓存重验）
        assert await auth_mod.bump_revocation(key_id) is True
        assert (await auth_mod._current_revocation(key_id)) != cached[0]
        auth_mod._cache.clear()
    finally:
        await close_redis()


# ---------------------------------------------------------------------------
# L3 弱确认收紧
# ---------------------------------------------------------------------------


def _confirming_state(text: str, intent: str) -> dict:
    return {
        "normalized_text": text,
        "current_state": "CONFIRMING",
        "active_task": {"intent": intent, "collected_slots": {"order_id": "SO1"}},
        "intent_result": {"pred_label": IntentLabel.META_CONFIRM, "confidence": 1.0,
                          "decision_source": "RULE_CONFIRM_GATE"},
    }


async def test_weak_confirm_on_l3_downgrades():
    """L3（退款）+「好的」→ 降级重确认，不放行执行。"""
    result = await confirmation_parse(_confirming_state("好的", IntentLabel.AFTERSALE_REFUND))
    assert result["intent_result"]["pred_label"] == IntentLabel.META_SLOT_ONLY
    assert result["weak_confirm_recheck"] is True


async def test_strong_confirm_on_l3_passes():
    """L3 +「确认」→ 照常放行（settled 透传）。"""
    result = await confirmation_parse(_confirming_state("确认", IntentLabel.AFTERSALE_REFUND))
    assert "intent_result" not in result  # 透传 = 不改写意图


async def test_weak_confirm_on_low_risk_passes():
    """低风险读意图不收紧：「好的」照常透传。"""
    result = await confirmation_parse(
        _confirming_state("好的", IntentLabel.ORDER_QUERY_STATUS)
    )
    assert "intent_result" not in result


# ---------------------------------------------------------------------------
# 建单并发窗口：IntegrityError 不污染主事务
# ---------------------------------------------------------------------------


async def test_ensure_ticket_race_does_not_poison_session(monkeypatch):
    """强制建单撞唯一索引：SAVEPOINT 回滚 + 回查返回已有工单，主事务仍可用。"""
    user_id = f"u-{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as session:
        record = await chat_session_repository.create(
            session, tenant_id=TENANT, user_id=user_id, channel="web"
        )
        sid = record.id
        existing = await chat_handoff_ticket_repository.create(
            session, tenant_id=TENANT, session_id=sid, user_id=user_id,
            reason=REASON_USER_REQUEST, status="PENDING",
        )
        await session.commit()

        # 模拟并发窗口：幂等预查「看不到」已有工单 → 直接建单 → 撞部分唯一索引
        real_get = chat_handoff_ticket_repository.get_open_by_session
        calls = {"n": 0}

        async def _racy_get(s, t, sess_id):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # 第一次（幂等预查）看不到
            return await real_get(s, t, sess_id)  # 冲突恢复时回查

        monkeypatch.setattr(
            chat_handoff_ticket_repository, "get_open_by_session", _racy_get
        )
        ticket_id, created = await handoff_service.ensure_ticket(
            session, tenant_id=TENANT, session_id=sid, user_id=user_id,
            reason=REASON_USER_REQUEST,
        )
        assert ticket_id == existing.id and created is False
        # 主事务未被 aborted：还能继续写
        msg_ok = await chat_session_repository.get_by_id(session, sid)
        assert msg_ok is not None
        await session.commit()
    await dispose_engine()


def test_prod_gate_allows_kb_disabled_with_hash(monkeypatch):
    """KB 关闭时 hash embedding 不拦（不用 RAG 就没有伪向量问题）。"""
    from app.core.config import Settings

    s = Settings(
        APP_ENV="prod", AUTH_ENABLED=True, KB_ENABLED=False,
        DATABASE_URL="postgresql+asyncpg://svc:strong@db:5432/ai_cs", _env_file=None,
    )
    assert s.EMBEDDING_PROVIDER == "hash"


def test_settings_import_side_effect_guard():
    """settings 单例仍可导入（门禁只在 prod 环境触发）。"""
    assert settings.APP_ENV == "local"
