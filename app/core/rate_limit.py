"""限流（Stage 08）：Redis 滑动窗口。

- 租户级 + 会话级两道（配置见 settings）；超限返回 429 + Retry-After；
- **Redis 不可用时放行并告警**——限流是保护措施，不能成为可用性单点；
- key 带 tenant 前缀，窗口用 ZSET（成员=纳秒时间戳，分数=秒时间戳）。
"""

import time
import uuid

from app.cache.redis_client import get_redis_client
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import get_logger

logger = get_logger(__name__)

_WINDOW_SECONDS = 60


class RateLimitExceeded(AppException):
    """限流异常：附带 Retry-After 秒数。"""

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            message="请求过于频繁，请稍后重试", error_code="RATE_LIMITED", status_code=429
        )
        self.retry_after = retry_after


async def _check_window(key: str, limit: int, scope: str) -> None:
    """滑动窗口计数；超限抛 RateLimitExceeded；Redis 故障放行。"""
    now = time.time()
    try:
        redis = get_redis_client()
        # 先判后写（Stage 13）：被拒请求不写入窗口——否则持续重试会让窗口
        # 一直满员，客户端在窗口期内永远无法自然恢复（自我饥饿）
        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, 0, now - _WINDOW_SECONDS)
        pipe.zcard(key)
        _, count = await pipe.execute()
        if int(count) < limit:
            pipe = redis.pipeline()
            pipe.zadd(key, {f"{now}:{uuid.uuid4().hex[:8]}": now})
            pipe.expire(key, _WINDOW_SECONDS + 5)
            await pipe.execute()
            return
    except Exception:  # noqa: BLE001 - 限流不做可用性单点
        logger.exception("rate limit check failed (redis down?), allowing request")
        return
    # 已达上限：计数并拒绝（本次请求未写入窗口）
    from app.core.metrics import count_rate_limited

    count_rate_limited(scope)
    # 最早一条过期还需的秒数作为 Retry-After 的保守估计
    raise RateLimitExceeded(retry_after=_WINDOW_SECONDS)


async def check_chat_rate_limit(tenant_id: str, session_id: str | None = None) -> None:
    """聊天面限流：租户级必查，会话级在有 session 时加查。"""
    await _check_window(
        f"rl:tenant:{tenant_id}", settings.RATE_LIMIT_TENANT_PER_MINUTE, scope="tenant"
    )
    if session_id:
        await _check_window(
            f"rl:session:{tenant_id}:{session_id}",
            settings.RATE_LIMIT_SESSION_PER_MINUTE,
            scope="session",
        )
