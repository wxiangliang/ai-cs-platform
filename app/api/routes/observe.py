"""观测查询 API（Stage 29 批 1）：会话记录与决策日志的页面化查询。

全部**只读 + admin scope**（观测面跨用户，不能挂 chat scope）：
- GET /api/observe/sessions                     会话列表（用户/状态过滤 + 分页）
- GET /api/observe/sessions/{id}/messages       消息流（admin 视角，免 user_id）
- GET /api/observe/sessions/{id}/decisions      逐轮决策日志（意图/来源/margin/
                                                graph_trace/retrieval/experiment 全量）
- GET /api/observe/sessions/{id}/tool-calls     工具调用审计

脱敏纪律：决策日志与工具调用的敏感字段**落库前已脱敏**（Stage 13），
本路由原样透出，不新增脱敏逻辑也不得绕过。零 migration，纯查询现有表。
"""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_admin, resolve_tenant_id
from app.core.exceptions import AppException
from app.core.responses import success_response
from app.db.session import get_db_session
from app.models.chat_decision_log import ChatDecisionLog
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.chat_tool_call import ChatToolCall
from app.repositories.chat_decision_log_repository import chat_decision_log_repository
from app.repositories.chat_message_repository import chat_message_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.repositories.chat_tool_call_repository import chat_tool_call_repository

router = APIRouter(prefix="/api/observe", tags=["observe"])


async def _owned_session(
    db: AsyncSession, tenant_id: str, session_id: str
) -> ChatSession:
    """会话归属校验：不存在或跨租户统一 404（不暴露存在性）。"""
    record = await chat_session_repository.get_by_id(db, session_id)
    if record is None or record.tenant_id != tenant_id:
        raise AppException(
            message="会话不存在", error_code="SESSION_NOT_FOUND", status_code=404
        )
    return record


def _session_item(s: ChatSession) -> dict[str, Any]:
    return {
        "session_id": s.id,
        "user_id": s.user_id,
        "channel": s.channel,
        "status": s.status,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def _message_item(m: ChatMessage) -> dict[str, Any]:
    return {
        "message_id": m.id,
        "role": m.role,
        "content": m.content,
        "intent": m.intent,
        "status": m.status,
        "trace_id": m.trace_id,
        "created_at": m.created_at.isoformat(),
    }


def _decision_item(d: ChatDecisionLog) -> dict[str, Any]:
    """逐轮决策证据全量透出（分析面价值所在：Stage 26/27 的 margin/
    pending_fill/meta_shadow/守护记录都在这几个 JSON 里）。"""
    return {
        "decision_id": d.id,
        "message_id": d.message_id,
        "created_at": d.created_at.isoformat(),
        "original_text": d.original_text,
        "normalized_text": d.normalized_text,
        "intent_result": d.intent_result_json,
        "slots": d.slot_result_json,
        "selected_skill": d.selected_skill,
        "status": d.status,
        "decision_source": d.decision_source,
        "graph_trace": d.graph_trace_json,
        "retrieval": d.retrieval_json,
        "experiment": d.experiment_json,
        "latency": d.latency_json,
        "error": d.error_json,
    }


def _tool_call_item(t: ChatToolCall) -> dict[str, Any]:
    return {
        "call_id": t.id,
        "task_id": t.task_id,
        "tool_id": t.tool_id,
        "ok": t.ok,
        "error_code": t.error_code,
        "latency_ms": t.latency_ms,
        "request": t.request_json,
        "response": t.response_json,
        "created_at": t.created_at.isoformat(),
    }


@router.get("/sessions")
async def list_sessions(
    auth: AuthContext | None = Depends(require_admin),
    tenant_id: str | None = Query(default=None, max_length=64),
    user_id: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """会话列表（更新时间倒序；limit/offset 简版分页，量大后换游标）。"""
    tid = resolve_tenant_id(auth, tenant_id)
    rows = await chat_session_repository.list_by_tenant(
        db, tid, user_id=user_id, status=status, limit=limit, offset=offset
    )
    return success_response(
        data={
            "sessions": [_session_item(s) for s in rows],
            "has_more": len(rows) == limit,
        }
    )


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    session_id: str,
    auth: AuthContext | None = Depends(require_admin),
    tenant_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """会话消息流（admin 视角免 user_id，时间正序）。"""
    tid = resolve_tenant_id(auth, tenant_id)
    await _owned_session(db, tid, session_id)
    rows = await chat_message_repository.list_by_session_id(db, tid, session_id, limit=limit)
    return success_response(data={"messages": [_message_item(m) for m in rows]})


@router.get("/sessions/{session_id}/decisions")
async def session_decisions(
    session_id: str,
    auth: AuthContext | None = Depends(require_admin),
    tenant_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """逐轮决策日志（时间正序；替代 replay_trace.py 的页面化入口）。"""
    tid = resolve_tenant_id(auth, tenant_id)
    await _owned_session(db, tid, session_id)
    rows = await chat_decision_log_repository.list_by_session_id(
        db, tid, session_id, limit=limit
    )
    return success_response(data={"decisions": [_decision_item(d) for d in rows]})


@router.get("/sessions/{session_id}/tool-calls")
async def session_tool_calls(
    session_id: str,
    auth: AuthContext | None = Depends(require_admin),
    tenant_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """工具调用审计（时间正序；含 mock/MCP 与诊断 agent 的调用）。"""
    tid = resolve_tenant_id(auth, tenant_id)
    await _owned_session(db, tid, session_id)
    rows = await chat_tool_call_repository.list_by_session_id(
        db, tid, session_id, limit=limit
    )
    return success_response(data={"tool_calls": [_tool_call_item(t) for t in rows]})


@router.get("/journeys/{user_id}")
async def get_journey(
    user_id: str,
    auth: AuthContext | None = Depends(require_admin),
    tenant_id: str | None = Query(default=None, max_length=64),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """客户旅程（Stage 38）：阶段 + 风险标记 + 近 20 条转移史。"""
    tid = resolve_tenant_id(auth, tenant_id)
    from app.repositories.customer_journey_repository import customer_journey_repository

    row = await customer_journey_repository.get_by_user(db, tid, user_id)
    if row is None:
        return success_response(data={"user_id": user_id, "stage": "NEW",
                                      "at_risk": False, "signals": []})
    return success_response(data={
        "user_id": user_id, "stage": row.stage, "at_risk": row.at_risk,
        "signals": row.signals_json or [],
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    })
