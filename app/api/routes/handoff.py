"""人工接管坐席 API（管理面，Stage 07）。

只做接参与调用 HandoffService / Repository，不写业务逻辑。
全部接口走 require_admin（鉴权开启需 admin scope；开发模式沿用 KB_ADMIN_TOKEN 过渡）。
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_admin, resolve_tenant_id
from app.core.exceptions import AppException
from app.core.responses import success_response
from app.db.session import get_db_session
from app.models.chat_handoff_ticket import ChatHandoffTicket
from app.repositories.chat_handoff_ticket_repository import chat_handoff_ticket_repository
from app.services.handoff_service import handoff_service

router = APIRouter(prefix="/api/handoff", tags=["handoff"])


class ClaimRequest(BaseModel):
    """认领工单请求。"""

    tenant_id: str | None = Field(default=None, max_length=64, description="鉴权开启后忽略")
    assignee: str = Field(..., min_length=1, max_length=64, description="坐席标识")


class ReplyRequest(BaseModel):
    """坐席回复请求。"""

    tenant_id: str | None = Field(default=None, max_length=64, description="鉴权开启后忽略")
    content: str = Field(..., min_length=1, max_length=4000)


class ResolveRequest(BaseModel):
    """解决归还请求。"""

    tenant_id: str | None = Field(default=None, max_length=64, description="鉴权开启后忽略")


def _ticket_brief(t: ChatHandoffTicket) -> dict:
    """列表项（不含上下文包，保持队列响应轻量）。"""
    return {
        "ticket_id": t.id,
        "session_id": t.session_id,
        "user_id": t.user_id,
        "reason": t.reason,
        "source_intent": t.source_intent,
        "status": t.status,
        "assignee": t.assignee,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
    }


@router.get("/tickets")
async def list_tickets(
    auth: AuthContext | None = Depends(require_admin),
    tenant_id: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=16, description="PENDING/ASSIGNED/RESOLVED/CLOSED"),
    limit: int = Query(default=20, ge=1, le=100),
    before: str | None = Query(default=None, description="游标：上一页最后一条 ticket_id"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """工单队列（created_at 倒序游标分页）。"""
    tid = resolve_tenant_id(auth, tenant_id)
    rows = await chat_handoff_ticket_repository.list_page(
        db, tid, status=status, limit=limit, before_id=before
    )
    return success_response(
        data={
            "tickets": [_ticket_brief(t) for t in rows],
            "next_before": rows[-1].id if len(rows) == limit else None,
        }
    )


@router.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    auth: AuthContext | None = Depends(require_admin),
    tenant_id: str | None = Query(default=None, max_length=64),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """工单详情（含上下文移交包，坐席打开即懂上下文）。"""
    tid = resolve_tenant_id(auth, tenant_id)
    ticket = await chat_handoff_ticket_repository.get_owned(db, tid, ticket_id)
    if ticket is None:
        raise AppException(message="工单不存在", error_code="TICKET_NOT_FOUND", status_code=404)
    return success_response(data={**_ticket_brief(ticket), "context": ticket.context_json or {}})


@router.post("/tickets/{ticket_id}/claim")
async def claim_ticket(
    ticket_id: str,
    body: ClaimRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """认领工单：仅 PENDING 可认领，并发抢单只有一人成功。"""
    tid = resolve_tenant_id(auth, body.tenant_id)
    ticket = await chat_handoff_ticket_repository.get_owned(db, tid, ticket_id)
    if ticket is None:
        raise AppException(message="工单不存在", error_code="TICKET_NOT_FOUND", status_code=404)
    ok = await handoff_service.claim(db, tid, ticket_id, body.assignee)
    if not ok:
        raise AppException(
            message="工单已被认领或已关闭", error_code="TICKET_NOT_CLAIMABLE", status_code=409
        )
    return success_response(data={"ticket_id": ticket_id, "assignee": body.assignee})


@router.post("/tickets/{ticket_id}/reply")
async def reply_ticket(
    ticket_id: str,
    body: ReplyRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """坐席回复：以 role=agent 写入会话消息（用户拉历史可见）。"""
    tid = resolve_tenant_id(auth, body.tenant_id)
    message_id = await handoff_service.reply(db, tid, ticket_id, body.content)
    if message_id is None:
        raise AppException(
            message="工单不存在或已关闭", error_code="TICKET_NOT_OPEN", status_code=404
        )
    return success_response(data={"ticket_id": ticket_id, "message_id": message_id})


@router.post("/tickets/{ticket_id}/resolve")
async def resolve_ticket(
    ticket_id: str,
    body: ResolveRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """解决归还：工单 RESOLVED、会话恢复 active、状态机复位（bot 恢复应答）。"""
    tid = resolve_tenant_id(auth, body.tenant_id)
    ok = await handoff_service.resolve(db, tid, ticket_id)
    if not ok:
        raise AppException(
            message="工单不存在或已关闭", error_code="TICKET_NOT_OPEN", status_code=404
        )
    return success_response(data={"ticket_id": ticket_id, "status": "RESOLVED"})
