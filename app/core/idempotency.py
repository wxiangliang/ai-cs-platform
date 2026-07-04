"""发消息幂等（Stage 08，Stage 13 加固）：Idempotency-Key + Redis 响应缓存。

v2（Stage 13 整改）：
1. **在途占位锁**：处理前 SET NX 占位——同 key 两个请求并发到达时，
   后到的直接 409（防双跑），此前两个都 miss 缓存会都进决策链；
2. **请求体指纹**：占位与缓存都带 body 指纹——同 key 不同 body 是客户端 bug，
   返回 422 明确报错，此前会静默返回旧结果；
3. **after-commit 写缓存**：路由在事务提交成功后才 cache_response
   （见 routes/chat.py）——此前提交前写缓存，提交失败会缓存"假成功"。

Redis 故障时幂等失效但请求照常处理（与限流同样的非单点原则）。
"""

import hashlib
import json
from typing import Any

from app.cache.redis_client import get_redis_client
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import get_logger

logger = get_logger(__name__)

# 在途占位的过期时间（秒）：覆盖最慢的一轮处理，进程崩溃后自动释放
_LOCK_TTL = 60


def _key(tenant_id: str, session_id: str, idem_key: str) -> str:
    return f"idem:{tenant_id}:{session_id}:{idem_key}"


def _lock_key(tenant_id: str, session_id: str, idem_key: str) -> str:
    return f"idem:lock:{tenant_id}:{session_id}:{idem_key}"


def body_fingerprint(body: bytes | str) -> str:
    """请求体指纹（sha256 前 16 字节十六进制，防同 key 异 body 静默复用）。"""
    raw = body.encode() if isinstance(body, str) else body
    return hashlib.sha256(raw).hexdigest()[:32]


async def get_cached_response(
    tenant_id: str, session_id: str, idem_key: str, fingerprint: str
) -> dict[str, Any] | None:
    """查幂等缓存；命中但指纹不符 → 422（客户端复用了 key）；故障视为未命中。"""
    try:
        raw = await get_redis_client().get(_key(tenant_id, session_id, idem_key))
    except Exception:  # noqa: BLE001
        logger.exception("idempotency cache read failed, treating as miss")
        return None
    if not raw:
        return None
    entry = json.loads(raw)
    if entry.get("fp") and entry["fp"] != fingerprint:
        raise AppException(
            message="Idempotency-Key 已被不同请求体使用",
            error_code="IDEMPOTENCY_KEY_REUSED",
            status_code=422,
        )
    return entry.get("response")


async def acquire_processing_lock(
    tenant_id: str, session_id: str, idem_key: str, fingerprint: str
) -> None:
    """在途占位（SET NX）：同 key 并发第二个请求 409；Redis 故障放行。"""
    try:
        ok = await get_redis_client().set(
            _lock_key(tenant_id, session_id, idem_key), fingerprint, nx=True, ex=_LOCK_TTL
        )
    except Exception:  # noqa: BLE001
        logger.exception("idempotency lock failed, allowing request")
        return
    if not ok:
        raise AppException(
            message="相同 Idempotency-Key 的请求正在处理中，请稍后重试",
            error_code="REQUEST_IN_FLIGHT",
            status_code=409,
        )


# compare-and-delete Lua：仅当锁值等于本请求指纹时才删（防删他人锁）
_RELEASE_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


async def release_processing_lock(
    tenant_id: str, session_id: str, idem_key: str, fingerprint: str
) -> None:
    """释放在途占位（compare-and-delete，防删他人锁）。

    请求处理超过 _LOCK_TTL 时锁会自动过期、可能被别的请求重新占用；
    无条件 delete 会误删他人的锁——只删值等于本请求指纹的锁。
    """
    try:
        await get_redis_client().eval(
            _RELEASE_LUA, 1, _lock_key(tenant_id, session_id, idem_key), fingerprint
        )
    except Exception:  # noqa: BLE001
        logger.exception("idempotency lock release failed")


async def cache_response(
    tenant_id: str,
    session_id: str,
    idem_key: str,
    response: dict[str, Any],
    fingerprint: str,
) -> None:
    """写幂等缓存（**必须在事务提交成功后调用**）；超限不缓存；故障只告警。"""
    try:
        raw = json.dumps({"fp": fingerprint, "response": response}, ensure_ascii=False)
        if len(raw.encode()) > settings.IDEMPOTENCY_MAX_BODY_BYTES:
            logger.warning("idempotency response too large, skip caching")
            return
        await get_redis_client().set(
            _key(tenant_id, session_id, idem_key), raw, ex=settings.IDEMPOTENCY_TTL
        )
    except Exception:  # noqa: BLE001
        logger.exception("idempotency cache write failed")
