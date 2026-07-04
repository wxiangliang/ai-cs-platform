"""Chat API 路由。

只做参数接收、鉴权/限流/幂等接入与调用 ChatService，不写业务逻辑、
不直接操作数据库，返回统一响应结构。

Stage 08：tenant_id 由 resolve_tenant_id 确定（鉴权开启=凭证，开发模式=请求体）；
发消息接口带租户+会话两级限流与 Idempotency-Key 幂等。
"""

import json

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel, Field

from app.core.auth import AuthContext, require_admin, require_chat, resolve_tenant_id
from app.core.exceptions import AppException
from app.core.idempotency import (
    acquire_processing_lock,
    body_fingerprint,
    cache_response,
    get_cached_response,
    release_processing_lock,
)
from app.core.logging import get_logger
from app.core.rate_limit import check_chat_rate_limit
from app.core.responses import success_response
from app.db.session import AsyncSessionLocal, get_db_session
from app.repositories.chat_message_repository import chat_message_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.schemas.chat import ChatMessageRequest, FeedbackRequest, SessionCreateRequest
from app.services.chat_service import chat_service
from app.services.notify_service import session_channel, ws_hub

logger = get_logger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# SSE 分片大小（字符）：小到有打字感，大到不产生海量事件
_STREAM_CHUNK_CHARS = 24


@router.post("/sessions")
async def create_session(
    body: SessionCreateRequest,
    auth: AuthContext | None = Depends(require_chat),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """创建会话，session_id 由服务端生成（Stage 04-01）。"""
    tenant_id = resolve_tenant_id(auth, body.tenant_id)
    await check_chat_rate_limit(tenant_id)
    data = await chat_service.create_session(db, body, tenant_id)
    return success_response(data=data.model_dump())


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: ChatMessageRequest,
    auth: AuthContext | None = Depends(require_chat),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """发送一条聊天消息，返回 AI 回复与本轮决策结果。

    事务由 get_db_session 依赖统一提交 / 回滚；
    带 Idempotency-Key 时重复请求返回缓存响应，不重复处理（Stage 08）。
    """
    tenant_id = resolve_tenant_id(auth, body.tenant_id)
    await check_chat_rate_limit(tenant_id, session_id)

    # 幂等（Stage 13 加固）：指纹校验（同 key 异 body → 422）+ 在途占位（并发双跑 → 409）
    fp = body_fingerprint(body.model_dump_json()) if idempotency_key else ""
    if idempotency_key:
        cached = await get_cached_response(tenant_id, session_id, idempotency_key, fp)
        if cached is not None:
            return cached
        await acquire_processing_lock(tenant_id, session_id, idempotency_key, fp)

    try:
        data = await chat_service.process_message(db, session_id, body, tenant_id)
        response = success_response(data=data.model_dump(), trace_id=data.trace_id)
        if idempotency_key:
            # 显式提交后才写幂等缓存：提交失败绝不缓存"假成功"
            # （依赖层的 commit 对已提交事务是空操作，语义不变）
            await db.commit()
            await cache_response(tenant_id, session_id, idempotency_key, response, fp)
        return response
    finally:
        if idempotency_key:
            await release_processing_lock(tenant_id, session_id, idempotency_key, fp)


@router.post("/sessions/{session_id}/messages/stream")
async def send_message_stream(
    session_id: str,
    body: ChatMessageRequest,
    auth: AuthContext | None = Depends(require_chat),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StreamingResponse:
    """发送聊天消息（SSE 流式，事件协议见 chat_api.md 第 7 节）。

    事件序列：meta（trace 元信息）→ delta*（回复分片）→ done（完整结果）；
    失败发 error 事件后关闭。当前版本主链路完整执行后分片下发
    （生成端接入 LLM 流式后仅需替换分片来源，协议不变）。
    注意：流式响应生命周期长于依赖作用域，DB 会话在生成器内自管理。
    幂等（对齐普通路径）：带 Idempotency-Key 时命中缓存直接以 done 事件回放，
    在途占位防并发双跑——SSE 断线重连是常态，不做幂等会重复落库与状态流转。
    """
    tenant_id = resolve_tenant_id(auth, body.tenant_id)
    await check_chat_rate_limit(tenant_id, session_id)
    fp = body_fingerprint(body.model_dump_json()) if idempotency_key else ""

    async def _event(name: str, data: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def _generate():
        # 幂等命中/并发占位：在生成器内处理，冲突转 error 事件（流已建立无法改状态码）
        if idempotency_key:
            try:
                cached = await get_cached_response(tenant_id, session_id, idempotency_key, fp)
                if cached is not None:
                    yield await _event("done", cached.get("data") or {})
                    return
                await acquire_processing_lock(tenant_id, session_id, idempotency_key, fp)
            except AppException as exc:
                yield await _event("error", {"code": exc.error_code, "message": exc.message})
                return
        try:
            try:
                async with AsyncSessionLocal() as db:
                    data = await chat_service.process_message(db, session_id, body, tenant_id)
                    await db.commit()
            except AppException as exc:
                yield await _event("error", {"code": exc.error_code, "message": exc.message})
                return
            except Exception:  # noqa: BLE001 - 流内异常不能抛回框架，转 error 事件
                logger.exception("stream message failed")
                yield await _event("error", {"code": "INTERNAL_ERROR", "message": "服务内部错误"})
                return

            payload = data.model_dump()
            if idempotency_key:
                # 提交成功后才缓存（对齐普通路径，data 字段与 REST 响应同形）
                await cache_response(
                    tenant_id, session_id, idempotency_key, {"data": payload}, fp
                )
            yield await _event("meta", {"session_id": session_id, "trace_id": data.trace_id})
            reply = payload.get("reply") or ""
            for i in range(0, len(reply), _STREAM_CHUNK_CHARS):
                yield await _event("delta", {"text": reply[i : i + _STREAM_CHUNK_CHARS]})
            yield await _event("done", payload)
        finally:
            if idempotency_key:
                await release_processing_lock(tenant_id, session_id, idempotency_key, fp)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class NotifyRequest(BaseModel):
    """主动消息请求（业务系统回调入口，Stage 15）。"""

    tenant_id: str | None = Field(default=None, max_length=64, description="鉴权开启后忽略")
    content: str = Field(..., min_length=1, max_length=2000)
    category: str = Field(default="notice", max_length=32, description="如 logistics_alert/order_done")


@router.post("/sessions/{session_id}/notify")
async def notify_session(
    session_id: str,
    body: NotifyRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """主动消息（Stage 15，管理面）：业务系统回调写入会话 + WS 实时推送。

    本阶段只做入口与推送，事件源对接（物流异常等）属真实系统联调。
    """
    tenant_id = resolve_tenant_id(auth, body.tenant_id)
    record = await chat_session_repository.get_by_id(db, session_id)
    if record is None or record.tenant_id != tenant_id:
        raise AppException(message="会话不存在", error_code="SESSION_NOT_FOUND", status_code=404)
    message = await chat_message_repository.create(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        role="assistant",
        content=body.content,
        status="DONE",
        metadata_json={"proactive": True, "category": body.category},
    )
    ws_hub.publish_after_commit(
        db,
        session_channel(tenant_id, session_id),
        {"type": "proactive", "message_id": message.id,
         "category": body.category, "content": body.content},
    )
    return success_response(data={"message_id": message.id})


@router.post("/sessions/{session_id}/feedback")
async def submit_feedback(
    session_id: str,
    body: FeedbackRequest,
    auth: AuthContext | None = Depends(require_chat),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """用户对 AI 回复点赞/点踩（Stage 09）。down 评价进入数据回流待审导出。"""
    tenant_id = resolve_tenant_id(auth, body.tenant_id)
    await check_chat_rate_limit(tenant_id, session_id)
    data = await chat_service.submit_feedback(db, session_id, body, tenant_id)
    return success_response(data=data)


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    auth: AuthContext | None = Depends(require_chat),
    tenant_id: str | None = Query(default=None, description="租户 ID（鉴权开启后忽略）"),
    user_id: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    before: str | None = Query(default=None, description="游标：取该消息 ID 之前的更早消息"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """历史消息分页（created_at 倒序），校验会话归属（Stage 04-01）。"""
    effective_tenant = resolve_tenant_id(auth, tenant_id)
    items = await chat_service.list_history(
        db, effective_tenant, user_id, session_id, limit=limit, before_id=before
    )
    return success_response(data={"messages": [i.model_dump() for i in items]})
