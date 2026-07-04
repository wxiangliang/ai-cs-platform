"""WebSocket 实时通道（Stage 15）。

- `WS /api/chat/sessions/{session_id}/ws`：用户侧——坐席回复（role=agent）、
  主动消息、会话归还事件实时推送；bot 回复仍走 HTTP/SSE，本通道只做服务端主动推送；
- `WS /api/handoff/ws`：坐席侧——新工单、接管期间的用户新消息实时推送。

鉴权：
- 鉴权开启：两端都读 `Authorization: Bearer` 头（服务端对服务端，可携带）；
  坐席端需 admin scope，用户端需 chat scope 且校验会话归属；
- 开发模式：用户端 query 传 tenant_id/user_id；坐席端 query 传
  admin_token=KB_ADMIN_TOKEN（浏览器 WS 无法带自定义头）。
校验失败以 4403/4404 策略码关闭。事件协议见 chat_api.md 第 8 节。
"""

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.auth import SCOPE_ADMIN, SCOPE_CHAT, verify_bearer_token
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.repositories.chat_session_repository import chat_session_repository
from app.services.notify_service import agents_channel, session_channel, ws_hub

logger = get_logger(__name__)

router = APIRouter(tags=["ws"])

# WebSocket 关闭策略码（4000+ 为应用自定义段）
_WS_FORBIDDEN = 4403
_WS_NOT_FOUND = 4404


async def _keep_until_disconnect(ws: WebSocket, channel: str) -> None:
    """注册连接并挂住直到客户端断开（收到的消息一律忽略——本通道只推不收）。"""
    await ws_hub.connect(channel, ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_hub.disconnect(channel, ws)


@router.websocket("/api/chat/sessions/{session_id}/ws")
async def session_ws(
    ws: WebSocket,
    session_id: str,
    tenant_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
) -> None:
    """用户侧实时通道（校验会话归属，越权统一 4404 不暴露存在性）。"""
    await ws.accept()
    try:
        async with AsyncSessionLocal() as db:
            auth = await verify_bearer_token(db, ws.headers.get("authorization"))
            if auth is not None:
                if SCOPE_CHAT not in auth.scopes:
                    await ws.close(code=_WS_FORBIDDEN)
                    return
                effective_tenant = auth.tenant_id
            else:
                if not tenant_id or not user_id:
                    await ws.close(code=_WS_FORBIDDEN)
                    return
                effective_tenant = tenant_id
            record = await chat_session_repository.get_by_id(db, session_id)
            if (
                record is None
                or record.tenant_id != effective_tenant
                or (user_id and record.user_id != user_id)
            ):
                await ws.close(code=_WS_NOT_FOUND)
                return
    except AppException:
        await ws.close(code=_WS_FORBIDDEN)
        return
    await _keep_until_disconnect(ws, session_channel(effective_tenant, session_id))


@router.websocket("/api/handoff/ws")
async def handoff_ws(
    ws: WebSocket,
    tenant_id: str | None = Query(default=None),
    admin_token: str | None = Query(default=None),
) -> None:
    """坐席侧实时通道（admin scope / 开发模式 admin_token 查询参数）。"""
    await ws.accept()
    try:
        async with AsyncSessionLocal() as db:
            auth = await verify_bearer_token(db, ws.headers.get("authorization"))
        if auth is not None:
            if SCOPE_ADMIN not in auth.scopes:
                await ws.close(code=_WS_FORBIDDEN)
                return
            effective_tenant = auth.tenant_id
        else:
            # 开发模式：与管理面 HTTP 同一 token 口径（Stage 13：空 token 不放行）
            if not settings.KB_ADMIN_TOKEN or admin_token != settings.KB_ADMIN_TOKEN:
                await ws.close(code=_WS_FORBIDDEN)
                return
            if not tenant_id:
                await ws.close(code=_WS_FORBIDDEN)
                return
            effective_tenant = tenant_id
    except AppException:
        await ws.close(code=_WS_FORBIDDEN)
        return
    await _keep_until_disconnect(ws, agents_channel(effective_tenant))
