"""体验闭环单元测试（Stage 15）：CSAT / 会话生命周期 / 排队位置 / WS 枢纽。"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.cache.redis_client import close_redis, init_redis
from app.chat.csat import parse_csat_score
from app.chat.graph.nodes.load_session_state import load_session_state
from app.chat.graph.nodes.save_turn import save_turn
from app.db.session import AsyncSessionLocal, dispose_engine
from app.repositories.chat_dialog_state_repository import chat_dialog_state_repository
from app.repositories.chat_handoff_ticket_repository import chat_handoff_ticket_repository
from app.repositories.chat_message_repository import chat_message_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.services.handoff_service import REASON_USER_REQUEST, handoff_service
from app.services.notify_service import WsHub, session_channel

TENANT = "exp-t"


# ---------------------------------------------------------------------------
# CSAT 解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text_in,expect", [
    ("5", 5), ("1", 1), ("4分", 4), ("打3", 3), ("满意", 5), ("不满意", 2),
    ("很不满意", 1), ("一般", 3), ("好评！", 5),
    ("我要退款", None), ("6", None), ("5号发货吗", None), ("还行吧还行", None),
])
def test_parse_csat_score(text_in, expect):
    assert parse_csat_score(text_in) == expect


# ---------------------------------------------------------------------------
# CSAT 闭环：resolve 发询问 → 评分捕获 → 落库清标记；非评分一次性失效
# ---------------------------------------------------------------------------


@pytest.fixture
async def _env():
    await init_redis()
    user_id = f"u-{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as session:
        record = await chat_session_repository.create(
            session, tenant_id=TENANT, user_id=user_id, channel="web"
        )
        sid = record.id
        await session.commit()
    yield user_id, sid
    await close_redis()
    await dispose_engine()


async def test_csat_full_loop(_env):
    user_id, sid = _env
    async with AsyncSessionLocal() as session:
        # 1. 建单 + resolve → CSAT 询问消息 + csat_pending 标记
        tid, _ = await handoff_service.ensure_ticket(
            session, tenant_id=TENANT, session_id=sid, user_id=user_id,
            reason=REASON_USER_REQUEST,
        )
        assert await handoff_service.resolve(session, TENANT, tid) is True
        await session.commit()
        msgs = await chat_message_repository.list_history_page(session, TENANT, sid, limit=5)
        assert any((m.metadata_json or {}).get("csat_request") for m in msgs)
        ds = await chat_dialog_state_repository.get_by_session_id(session, TENANT, sid)
        assert ds.context_stacks_json.get("csat_pending") == "handoff_resolve"

        # 2. 评分回复 → load_session_state 短路捕获
        config = {"configurable": {"db_session": session}}
        state = {"tenant_id": TENANT, "session_id": sid, "user_id": user_id, "message": "5"}
        loaded = await load_session_state(state, config)
        assert loaded["blocked"] is True
        assert loaded["csat_capture"] == {"score": 5, "trigger": "handoff_resolve"}

        # 3. save_turn 落库 + 清标记
        full_state = {**state, **loaded, "reply": "感谢您的评价！", "status": "DONE"}
        await save_turn(full_state, config)
        await session.commit()
        row = (await session.execute(
            text("SELECT score, trigger FROM chat_csat WHERE session_id=:s"), {"s": sid}
        )).first()
        assert row is not None and row.score == 5 and row.trigger == "handoff_resolve"
        ds = await chat_dialog_state_repository.get_by_session_id(session, TENANT, sid)
        assert "csat_pending" not in (ds.context_stacks_json or {})


async def test_csat_non_score_reply_one_shot(_env):
    """csat_pending 轮回复业务问题：不捕获、照常进主链路、标记一次性清除。"""
    user_id, sid = _env
    async with AsyncSessionLocal() as session:
        await chat_dialog_state_repository.upsert_by_session_id(
            session, TENANT, sid, state="IDLE",
            context_stacks_json={"csat_pending": "handoff_resolve"},
        )
        config = {"configurable": {"db_session": session}}
        state = {"tenant_id": TENANT, "session_id": sid, "user_id": user_id,
                 "message": "我要查订单 SO12345678"}
        loaded = await load_session_state(state, config)
        assert "csat_capture" not in loaded and not loaded.get("blocked")
        # 正常轮 save_turn：清 csat_pending
        full_state = {**state, **loaded, "reply": "好的", "status": "DONE",
                      "new_state": "IDLE",
                      "intent_result": {"final_intent": "ORDER.QUERY_STATUS"}}
        await save_turn(full_state, config)
        await session.commit()
        ds = await chat_dialog_state_repository.get_by_session_id(session, TENANT, sid)
        assert "csat_pending" not in (ds.context_stacks_json or {})


# ---------------------------------------------------------------------------
# 会话生命周期
# ---------------------------------------------------------------------------


async def test_closed_session_reopens_on_message(_env):
    user_id, sid = _env
    async with AsyncSessionLocal() as session:
        await chat_session_repository.update_status(session, TENANT, sid, "closed")
        await session.commit()
        config = {"configurable": {"db_session": session}}
        loaded = await load_session_state(
            {"tenant_id": TENANT, "session_id": sid, "user_id": user_id, "message": "在吗"},
            config,
        )
        await session.commit()
        assert not loaded.get("blocked")  # 正常进决策链
        record = await chat_session_repository.get_by_id(session, sid)
        assert record.status == "active"  # 已重开


async def test_close_idle_and_stale(monkeypatch, _env):
    """空闲会话关闭（+CSAT 询问）与超时工单 CLOSED（会话归还）。"""
    import scripts.close_idle_sessions as cli

    user_id, sid = _env
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    async with AsyncSessionLocal() as session:
        # 老消息（空闲判定依据）
        await session.execute(text(
            "INSERT INTO chat_message (id, tenant_id, session_id, role, content, created_at) "
            "VALUES (:i, :t, :s, 'user', '旧消息', :ts)"
        ), {"i": str(uuid.uuid4()), "t": TENANT, "s": sid, "ts": old})
        # 超时 ASSIGNED 工单（另一个会话）
        rec2 = await chat_session_repository.create(
            session, tenant_id=TENANT, user_id=user_id, channel="web"
        )
        sid2 = rec2.id
        await chat_session_repository.update_status(session, TENANT, sid2, "handoff")
        ticket = await chat_handoff_ticket_repository.create(
            session, tenant_id=TENANT, session_id=sid2, user_id=user_id,
            reason=REASON_USER_REQUEST, status="ASSIGNED", assignee="agent-a",
        )
        tid = ticket.id
        await session.commit()
        await session.execute(text(
            "UPDATE chat_handoff_ticket SET updated_at=:ts WHERE id=:i"
        ), {"ts": old, "i": tid})
        await session.commit()

    closed_sessions = await cli.close_idle_sessions()
    closed_tickets = await cli.close_stale_tickets()
    assert closed_sessions >= 1 and closed_tickets >= 1

    async with AsyncSessionLocal() as session:
        record = await chat_session_repository.get_by_id(session, sid)
        assert record.status == "closed"
        ds = await chat_dialog_state_repository.get_by_session_id(session, TENANT, sid)
        assert ds.context_stacks_json.get("csat_pending") == "session_close"
        row = (await session.execute(text(
            "SELECT status FROM chat_handoff_ticket WHERE id=:i"), {"i": tid})).first()
        assert row.status == "CLOSED"
        rec2 = await chat_session_repository.get_by_id(session, sid2)
        assert rec2.status == "active"  # 会话已归还


# ---------------------------------------------------------------------------
# 排队位置
# ---------------------------------------------------------------------------


async def test_queue_position(_env):
    user_id, sid = _env
    async with AsyncSessionLocal() as session:
        # 先造一张更早的 PENDING 工单（其他会话）
        rec0 = await chat_session_repository.create(
            session, tenant_id=TENANT, user_id=user_id, channel="web"
        )
        await chat_handoff_ticket_repository.create(
            session, tenant_id=TENANT, session_id=rec0.id, user_id=user_id,
            reason=REASON_USER_REQUEST, status="PENDING",
        )
        tid, _ = await handoff_service.ensure_ticket(
            session, tenant_id=TENANT, session_id=sid, user_id=user_id,
            reason=REASON_USER_REQUEST,
        )
        position = await handoff_service.queue_position(session, TENANT, tid)
        assert position == 2
        await session.rollback()


# ---------------------------------------------------------------------------
# WS 枢纽：注册/注销 + Pub/Sub 投递
# ---------------------------------------------------------------------------


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.dead = False

    async def send_text(self, payload: str) -> None:
        if self.dead:
            raise RuntimeError("connection closed")
        self.sent.append(payload)


async def test_ws_hub_pubsub_roundtrip(_env):
    """publish 经 Redis Pub/Sub 投达本地连接；死连接被清理。"""
    _, sid = _env
    hub = WsHub()
    ws_ok, ws_dead = _FakeWs(), _FakeWs()
    ws_dead.dead = True
    channel = session_channel(TENANT, sid)
    await hub.connect(channel, ws_ok)  # type: ignore[arg-type]
    await hub.connect(channel, ws_dead)  # type: ignore[arg-type]
    await asyncio.sleep(0.2)  # 等 psubscribe 就绪
    await hub.publish(channel, {"type": "agent_reply", "content": "您好"})
    for _ in range(20):
        if ws_ok.sent:
            break
        await asyncio.sleep(0.05)
    assert ws_ok.sent and json.loads(ws_ok.sent[0])["type"] == "agent_reply"
    assert channel in hub._local and ws_dead not in hub._local[channel]  # 死连接已清
    await hub.shutdown()
