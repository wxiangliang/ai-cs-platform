"""Stage 36 事件驱动主动客服回归测试（需 PostgreSQL + Redis）。

锁定红线四件事：
1. 幂等：同 event_id 只通知一次；Redis 故障宁可不发（fail-closed）；
2. 退订生效：用户 opt-out 后事件通知同样不发；
3. 发送前重查最新事实：verify 失败不发（旧事件不带过期事实发出）；
4. 无渠道如实记录；默认关零回归；静默时间纯函数正确。
"""

import uuid
from datetime import datetime

import pytest

from app.core.config import settings
from app.db.session import AsyncSessionLocal, dispose_engine
from app.services.event_service import in_quiet_hours, process_event

TENANT = "t-event-test"


@pytest.fixture
async def env(monkeypatch):
    from app.cache.redis_client import close_redis, init_redis

    try:
        await init_redis()
    except Exception:
        pytest.skip("Redis 不可用")
    monkeypatch.setattr(settings, "EVENTS_ENABLED", True)
    try:
        async with AsyncSessionLocal() as session:
            yield session
            await session.rollback()
    finally:
        await close_redis()
        await dispose_engine()


def _event(**over):
    base = {
        "event_id": f"ev-{uuid.uuid4().hex[:10]}",
        "event_type": "COUPON_EXPIRING",
        "user_id": f"u-{uuid.uuid4().hex[:8]}",
        "payload": {"expire": "2026-08-31"},
    }
    return {**base, **over}


async def _make_session(db, user_id: str) -> str:
    from app.repositories.chat_session_repository import chat_session_repository

    record = await chat_session_repository.create(
        db, tenant_id=TENANT, user_id=user_id, status="active", channel="web"
    )
    return record.id


# ---------------- 默认关与白名单 ----------------


async def test_disabled_by_default(env, monkeypatch):
    monkeypatch.setattr(settings, "EVENTS_ENABLED", False)
    outcome = await process_event(env, tenant_id=TENANT, event=_event())
    assert outcome["status"] == "disabled"


async def test_unknown_type_rejected(env):
    outcome = await process_event(
        env, tenant_id=TENANT, event=_event(event_type="SOMETHING_ELSE")
    )
    assert outcome["status"] == "unknown_type"


# ---------------- 幂等与退订 ----------------


async def test_delivered_then_duplicate(env):
    event = _event()
    session_id = await _make_session(env, event["user_id"])
    first = await process_event(env, tenant_id=TENANT, event=event)
    assert first["status"] == "delivered" and first["session_id"] == session_id

    # 发送依据可追溯（红线 5）
    from app.repositories.chat_message_repository import chat_message_repository

    msg = await chat_message_repository.get_by_id(env, first["message_id"])
    assert msg.metadata_json["event_id"] == event["event_id"]
    assert msg.metadata_json["category"] == "event"
    assert "2026-08-31" in msg.content  # 模板参数注入

    # 同一事件只通知一次
    second = await process_event(env, tenant_id=TENANT, event=event)
    assert second["status"] == "duplicate"


async def test_optout_blocks_event(env):
    event = _event()
    await _make_session(env, event["user_id"])
    from app.cache.redis_client import get_redis_client

    await get_redis_client().set(
        f"proactive:optout:{TENANT}:{event['user_id']}", "1"
    )
    outcome = await process_event(env, tenant_id=TENANT, event=event)
    assert outcome["status"] == "opted_out"


async def test_no_channel_recorded(env):
    outcome = await process_event(env, tenant_id=TENANT, event=_event())
    assert outcome["status"] == "no_channel"  # 无会话不硬发


# ---------------- 发送前重查（fail-closed） ----------------


async def test_verify_failed_suppresses(env, monkeypatch):
    event = _event(
        event_type="SHIPMENT_DELAYED", entity_id="SO123",
        payload={"previous_eta": "2026-08-05"},
    )
    await _make_session(env, event["user_id"])

    class _BrokenProvider:
        async def invoke(self, tool_id, params, tenant_id):
            raise RuntimeError("upstream down")

    import app.chat.tools.factory as factory_mod

    monkeypatch.setattr(factory_mod, "get_tool_provider", lambda: _BrokenProvider())
    outcome = await process_event(env, tenant_id=TENANT, event=event)
    assert outcome["status"] == "verify_failed"  # 查不到最新事实就不发


async def test_shipment_delayed_uses_fresh_facts(env):
    """配送延迟通知内容来自发送前重查（mock 物流轨迹），不是事件旧值。"""
    event = _event(
        event_type="SHIPMENT_DELAYED", entity_id="SO12345678",
        payload={"previous_eta": "旧值不应出现"},
    )
    await _make_session(env, event["user_id"])
    outcome = await process_event(env, tenant_id=TENANT, event=event)
    assert outcome["status"] == "delivered"
    from app.repositories.chat_message_repository import chat_message_repository

    msg = await chat_message_repository.get_by_id(env, outcome["message_id"])
    assert "SO12345678" in msg.content
    assert "转运中心" in msg.content  # mock 重查轨迹（最新事实）进入文案


# ---------------- 静默时间（纯函数） ----------------


def test_quiet_hours_logic():
    assert not in_quiet_hours(datetime(2026, 8, 5, 12), "")  # 未启用
    assert in_quiet_hours(datetime(2026, 8, 5, 23), "22-8")  # 跨零点晚间
    assert in_quiet_hours(datetime(2026, 8, 5, 3), "22-8")
    assert not in_quiet_hours(datetime(2026, 8, 5, 12), "22-8")
    assert in_quiet_hours(datetime(2026, 8, 5, 13), "12-14")  # 同日区间
    assert not in_quiet_hours(datetime(2026, 8, 5, 15), "12-14")
    assert not in_quiet_hours(datetime(2026, 8, 5, 15), "bad-spec")  # 容错
