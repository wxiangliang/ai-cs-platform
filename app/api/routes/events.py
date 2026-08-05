"""业务事件入口（Stage 36，管理面 webhook）。

只做接参与调用 event_service；幂等/退订/静默/重查全部在服务层收口。
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_admin, resolve_tenant_id
from app.core.responses import success_response
from app.db.session import get_db_session
from app.services.event_service import process_event

router = APIRouter(prefix="/api/events", tags=["events"])


class EventRequest(BaseModel):
    """业务事件（Outbox/回调推送）。"""

    tenant_id: str | None = Field(default=None, max_length=64, description="鉴权开启后忽略")
    event_id: str = Field(..., min_length=1, max_length=128, description="幂等键")
    event_type: str = Field(..., min_length=1, max_length=64)
    user_id: str = Field(..., min_length=1, max_length=64)
    entity_type: str | None = Field(default=None, max_length=32)
    entity_id: str | None = Field(default=None, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("")
async def ingest_event(
    body: EventRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """接收一条业务事件；返回处理结局（delivered/duplicate/... 全部可观测）。"""
    tenant_id = resolve_tenant_id(auth, body.tenant_id)
    outcome = await process_event(db, tenant_id=tenant_id, event=body.model_dump())
    if outcome.get("status") == "delivered":
        await db.commit()
    return success_response(data=outcome)
