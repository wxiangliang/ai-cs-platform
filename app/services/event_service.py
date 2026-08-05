"""事件驱动主动客服（Stage 36，需求见 stage-36 文档）。

业务事件 → 判断是否联系客户 → 主动消息。红线全部结构性落地：
幂等（event_id SETNX，Redis 故障宁可不发）、发送前重查最新事实
（verify_tool 经工具层，失败不发）、退订对服务通知同样生效、
发送依据落消息 metadata（event_id/模板=可追溯）。
"""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.i18n import t
from app.core.logging import get_logger
from app.core.metrics import count_event

logger = get_logger(__name__)

# 事件白名单：event_type → 规则（verify_tool=发送前重查；params_from=
# 重查参数取事件哪个字段；template=i18n 模板 key）
EVENT_RULES: dict[str, dict[str, Any]] = {
    "SHIPMENT_DELAYED": {
        "template": "event.shipment_delayed",
        "verify_tool": "query_logistics_track",
        "verify_param": "order_id",
    },
    "REFUND_STATUS_CHANGED": {
        "template": "event.refund_status",
        "verify_tool": "query_order",
        "verify_param": "order_id",
    },
    "BACK_IN_STOCK": {
        "template": "event.back_in_stock",
        "verify_tool": "query_product",
        "verify_param": "product_name",
    },
    "COUPON_EXPIRING": {"template": "event.coupon_expiring"},
}

_K_EVENT = "event:seen:{tenant}:{event_id}"
_K_OPTOUT = "proactive:optout:{tenant}:{user}"
_K_COOLDOWN = "proactive:cool:{tenant}:{user}"


def in_quiet_hours(now: datetime, spec: str) -> bool:
    """静默时间判定（`22-8` 表示 22:00-次日 08:00；空串=不启用）。"""
    if not spec or "-" not in spec:
        return False
    try:
        start_s, end_s = spec.split("-", 1)
        start, end = int(start_s), int(end_s)
    except ValueError:
        return False
    hour = now.hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # 跨零点


async def process_event(
    db: AsyncSession, *, tenant_id: str, event: dict[str, Any]
) -> dict[str, Any]:
    """处理一条业务事件；恒返回 {status, ...}（全部结局可观测）。"""
    event_type = str(event.get("event_type") or "")
    event_id = str(event.get("event_id") or "")
    user_id = str(event.get("user_id") or "")
    outcome = await _process(db, tenant_id, event_type, event_id, user_id, event)
    count_event(event_type or "UNKNOWN", outcome["status"])
    return outcome


async def _process(
    db: AsyncSession,
    tenant_id: str,
    event_type: str,
    event_id: str,
    user_id: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    if not settings.EVENTS_ENABLED:
        return {"status": "disabled"}
    rule = EVENT_RULES.get(event_type)
    if rule is None or not event_id or not user_id:
        return {"status": "unknown_type"}

    # —— 幂等：同一事件只通知一次（Redis 故障宁可不发=fail-closed）——
    try:
        from app.cache.redis_client import get_redis_client

        redis = get_redis_client()
        fresh = await redis.set(
            _K_EVENT.format(tenant=tenant_id, event_id=event_id),
            "1", nx=True, ex=7 * 86400,
        )
        if not fresh:
            return {"status": "duplicate"}
        # 退订/冷却：用户的「别打扰」对服务通知同样生效
        if await redis.exists(_K_OPTOUT.format(tenant=tenant_id, user=user_id)):
            return {"status": "opted_out"}
        if await redis.exists(_K_COOLDOWN.format(tenant=tenant_id, user=user_id)):
            return {"status": "opted_out"}
    except Exception:  # noqa: BLE001
        logger.warning("event dedupe redis unavailable, suppressed", exc_info=True)
        return {"status": "suppressed_redis"}

    if in_quiet_hours(datetime.now(), settings.EVENTS_QUIET_HOURS):
        return {"status": "quiet_hours"}

    # —— 渠道定位：该用户最近会话（无会话如实记录，不硬发）——
    from app.repositories.chat_session_repository import chat_session_repository

    sessions = await chat_session_repository.list_by_user_id(db, tenant_id, user_id, limit=1)
    if not sessions:
        return {"status": "no_channel"}
    session_id = sessions[0].id

    # —— 发送前重查最新事实（旧事件绝不带过期事实发出）——
    payload = dict(event.get("payload") or {})
    facts: dict[str, Any] = {}
    verify_tool = rule.get("verify_tool")
    if verify_tool:
        try:
            from app.chat.tools.factory import get_tool_provider

            param_key = str(rule.get("verify_param") or "order_id")
            value = str(event.get("entity_id") or payload.get(param_key) or "")
            result = await get_tool_provider().invoke(
                verify_tool, {param_key: value}, tenant_id=tenant_id
            )
            if not result.ok:
                return {"status": "verify_failed"}
            facts = result.data
        except Exception:  # noqa: BLE001 - 查不到最新事实就不发
            logger.warning("event verify failed", exc_info=True)
            return {"status": "verify_failed"}

    # —— 组装消息（i18n 模板；参数=事件载荷 ∪ 重查事实，模板占位键缺失置空）——
    params: dict[str, str] = {
        k: "" for k in ("order_id", "latest", "eta", "status", "product_name", "expire")
    }
    params.update({k: str(v) for k, v in {**payload, **facts}.items()})
    params["entity_id"] = str(event.get("entity_id") or "")
    content = t(rule["template"], None, **params)

    from app.repositories.chat_message_repository import chat_message_repository
    from app.services.notify_service import session_channel, ws_hub

    message = await chat_message_repository.create(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        role="assistant",
        content=content,
        status="DONE",
        metadata_json={
            # 发送依据可追溯（红线 5）
            "proactive": True, "category": "event",
            "event_id": event_id, "event_type": event_type,
            "template": rule["template"],
        },
    )
    ws_hub.publish_after_commit(
        db,
        session_channel(tenant_id, session_id),
        {"type": "proactive", "message_id": message.id,
         "category": "event", "content": content},
    )
    return {"status": "delivered", "session_id": session_id, "message_id": message.id}
