"""人工接管与工单单元测试（Stage 07，DB 相关走真实 PG）。"""

import uuid

import pytest

from app.chat.graph.nodes.load_session_state import load_session_state
from app.chat.graph.nodes.save_turn import save_turn
from app.chat.intent.types import IntentLabel
from app.chat.state.types import DialogStateValue, TurnStatus
from app.core.config import settings
from app.db.session import AsyncSessionLocal, dispose_engine
from app.repositories.chat_dialog_state_repository import chat_dialog_state_repository
from app.repositories.chat_handoff_ticket_repository import chat_handoff_ticket_repository
from app.repositories.chat_message_repository import chat_message_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.services.handoff_service import (
    REASON_PAYMENT_ISSUE,
    REASON_USER_REQUEST,
    handoff_service,
)

TENANT = "ho-t"


@pytest.fixture
async def _session_record():
    """造一个会话，测试结束释放引擎。"""
    user_id = f"ho-u-{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as session:
        record = await chat_session_repository.create(
            session, tenant_id=TENANT, user_id=user_id, channel="web"
        )
        sid = record.id
        await session.commit()
    yield user_id, sid
    await dispose_engine()


async def test_ensure_ticket_idempotent(_session_record):
    """同会话未关闭工单不重复建单。"""
    user_id, sid = _session_record
    async with AsyncSessionLocal() as session:
        tid1, created1 = await handoff_service.ensure_ticket(
            session, tenant_id=TENANT, session_id=sid, user_id=user_id,
            reason=REASON_USER_REQUEST, source_intent="META.TRANSFER_HUMAN",
        )
        tid2, created2 = await handoff_service.ensure_ticket(
            session, tenant_id=TENANT, session_id=sid, user_id=user_id,
            reason=REASON_PAYMENT_ISSUE,
        )
        await session.commit()
    assert created1 is True and created2 is False
    assert tid1 == tid2


async def test_claim_only_once(_session_record):
    """PENDING 才可认领：第二次认领失败（并发抢单安全）。"""
    user_id, sid = _session_record
    async with AsyncSessionLocal() as session:
        tid, _ = await handoff_service.ensure_ticket(
            session, tenant_id=TENANT, session_id=sid, user_id=user_id,
            reason=REASON_USER_REQUEST,
        )
        ok1 = await handoff_service.claim(session, TENANT, tid, "agent-a")
        ok2 = await handoff_service.claim(session, TENANT, tid, "agent-b")
        await session.commit()
        ticket = await chat_handoff_ticket_repository.get_owned(session, TENANT, tid)
        assert ok1 is True and ok2 is False
        assert ticket is not None and ticket.assignee == "agent-a"


async def test_reply_writes_agent_message(_session_record):
    """坐席回复以 role=agent 写入会话消息。"""
    user_id, sid = _session_record
    async with AsyncSessionLocal() as session:
        tid, _ = await handoff_service.ensure_ticket(
            session, tenant_id=TENANT, session_id=sid, user_id=user_id,
            reason=REASON_USER_REQUEST,
        )
        msg_id = await handoff_service.reply(session, TENANT, tid, "您好，人工为您服务")
        await session.commit()
        assert msg_id is not None
        rows = await chat_message_repository.list_history_page(session, TENANT, sid, limit=5)
        agent_rows = [m for m in rows if m.role == "agent"]
        assert agent_rows and agent_rows[0].content == "您好，人工为您服务"


async def test_resolve_restores_session(_session_record):
    """解决归还：工单 RESOLVED、会话 active、状态机复位 IDLE。"""
    user_id, sid = _session_record
    async with AsyncSessionLocal() as session:
        tid, _ = await handoff_service.ensure_ticket(
            session, tenant_id=TENANT, session_id=sid, user_id=user_id,
            reason=REASON_USER_REQUEST,
        )
        await chat_session_repository.update_status(session, TENANT, sid, "handoff")
        ok = await handoff_service.resolve(session, TENANT, tid)
        await session.commit()
        assert ok is True
        ticket = await chat_handoff_ticket_repository.get_owned(session, TENANT, tid)
        assert ticket is not None and ticket.status == "RESOLVED"
        record = await chat_session_repository.get_by_id(session, sid)
        assert record is not None and record.status == "active"
        ds = await chat_dialog_state_repository.get_by_session_id(session, TENANT, sid)
        assert ds is not None and ds.state == "IDLE" and ds.active_task_json is None
        # 已解决后再次 resolve 幂等失败
        assert await handoff_service.resolve(session, TENANT, tid) is False


async def test_bot_silent_short_circuit(_session_record):
    """会话 handoff 期间，load_session_state 直接静默短路（不进决策链）。"""
    user_id, sid = _session_record
    async with AsyncSessionLocal() as session:
        await chat_session_repository.update_status(session, TENANT, sid, "handoff")
        await session.commit()
        state = {
            "tenant_id": TENANT, "session_id": sid, "user_id": user_id,
            "message": "在吗", "channel": "web",
        }
        result = await load_session_state(state, {"configurable": {"db_session": session}})
    assert result["blocked"] is True and result["handoff_silent"] is True
    assert result["status"] == TurnStatus.HANDOFF_SILENT
    assert result["current_state"] == DialogStateValue.HANDOFF


async def test_unknown_streak_creates_ticket(_session_record):
    """连续 HANDOFF_UNKNOWN_STREAK 次兜底 → 自动建单并追加转人工提示。"""
    user_id, sid = _session_record

    def _state(streak_stacks):
        return {
            "tenant_id": TENANT, "session_id": sid, "user_id": user_id,
            "message": "呜哩哇啦", "reply": "抱歉没听懂",
            "status": TurnStatus.FALLBACK,
            "intent_result": {"final_intent": IntentLabel.META_UNKNOWN},
            "new_state": DialogStateValue.IDLE,
            "context_stacks": streak_stacks,
        }

    async with AsyncSessionLocal() as session:
        config = {"configurable": {"db_session": session}}
        # 第 1 次兜底：计数=1，不建单
        r1 = await save_turn(_state({}), config)
        assert "转人工" not in r1["reply"]
        ds = await chat_dialog_state_repository.get_by_session_id(session, TENANT, sid)
        assert (ds.context_stacks_json or {}).get("unknown_streak") == 1
        # 第 2 次兜底：达到阈值（默认 2）→ 建单 + 回复附提示
        assert settings.HANDOFF_UNKNOWN_STREAK == 2
        r2 = await save_turn(_state(dict(ds.context_stacks_json or {})), config)
        await session.commit()
        assert "转人工" in r2["reply"]
        ticket = await chat_handoff_ticket_repository.get_open_by_session(session, TENANT, sid)
        assert ticket is not None and ticket.reason == "REPEATED_UNKNOWN"
        # 会话不置 handoff：用户仍可继续与 bot 交互
        record = await chat_session_repository.get_by_id(session, sid)
        assert record is not None and record.status == "active"
        # AI 消息落库内容与返回 reply 一致（回复定稿在落库前）
        rows = await chat_message_repository.list_history_page(session, TENANT, sid, limit=10)
        ai_rows = [m for m in rows if m.role == "assistant"]
        assert any("转人工" in m.content for m in ai_rows)


async def test_tool_failure_creates_skill_rule_ticket(monkeypatch, _session_record):
    """工具全部失败且技能声明 requires_human_if → 自动建 SKILL_RULE 工单。"""
    from app.chat.graph.nodes import tool_invoke as ti_mod
    from app.chat.tools.base import ToolResult

    user_id, sid = _session_record

    class _FailingProvider:
        name = "fail"

        async def invoke(self, tool_id, params, *, tenant_id):
            return ToolResult(ok=False, error_code="TOOL_TIMEOUT", latency_ms=1.0)

    monkeypatch.setattr(ti_mod, "get_tool_provider", lambda: _FailingProvider())
    state = {
        "tenant_id": TENANT, "session_id": sid, "user_id": user_id,
        "status": TurnStatus.DONE, "normalized_text": "查订单",
        "intent_result": {"final_intent": IntentLabel.ORDER_QUERY_STATUS},
        "slots": {"order_id": "SO999"},
    }
    async with AsyncSessionLocal() as session:
        result = await ti_mod.tool_invoke(state, {"configurable": {"db_session": session}})
        await session.commit()
        ticket = await chat_handoff_ticket_repository.get_open_by_session(session, TENANT, sid)
    assert ticket is not None and ticket.reason == "SKILL_RULE"
    assert result["retrieval"].get("handoff", {}).get("reason") == "SKILL_RULE"


async def test_action_failure_creates_execution_failed_ticket(monkeypatch, _session_record):
    """写操作执行失败 → 自动建 EXECUTION_FAILED 工单（「人工跟进」不落空）。"""
    from unittest.mock import AsyncMock

    from app.chat.graph.nodes import action_execute as ae_mod

    user_id, sid = _session_record
    monkeypatch.setattr(
        ae_mod.action_executor, "execute", AsyncMock(side_effect=RuntimeError("boom"))
    )
    state = {
        "tenant_id": TENANT, "session_id": sid, "user_id": user_id,
        "status": TurnStatus.CONFIRMED,
        "finished_task": {"intent": IntentLabel.AFTERSALE_REFUND,
                          "collected_slots": {"order_id": "SO1"}},
    }
    async with AsyncSessionLocal() as session:
        result = await ae_mod.action_execute(state, {"configurable": {"db_session": session}})
        await session.commit()
        ticket = await chat_handoff_ticket_repository.get_open_by_session(session, TENANT, sid)
    assert ticket is not None and ticket.reason == "EXECUTION_FAILED"
    assert "人工客服" in result["reply"]


async def test_user_request_handoff_silences_session(_session_record):
    """new_state=HANDOFF → 会话置 handoff + 建单 + 回复附工单号。"""
    user_id, sid = _session_record
    state = {
        "tenant_id": TENANT, "session_id": sid, "user_id": user_id,
        "message": "转人工", "reply": "好的，正在为您转接人工客服。",
        "status": TurnStatus.HANDOFF,
        "intent_result": {"final_intent": "META.TRANSFER_HUMAN"},
        "new_state": DialogStateValue.HANDOFF,
        "context_stacks": {},
    }
    async with AsyncSessionLocal() as session:
        result = await save_turn(state, {"configurable": {"db_session": session}})
        await session.commit()
        assert "工单号" in result["reply"]
        record = await chat_session_repository.get_by_id(session, sid)
        assert record is not None and record.status == "handoff"
        ticket = await chat_handoff_ticket_repository.get_open_by_session(session, TENANT, sid)
        assert ticket is not None and ticket.reason == "USER_REQUEST"
