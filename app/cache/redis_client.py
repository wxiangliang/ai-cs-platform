"""Redis 异步客户端模块。

使用 redis.asyncio 维护一个全局复用的连接池客户端：
- 应用启动时 init_redis() 初始化并探活；
- 应用关闭时 close_redis() 优雅释放；
- 业务通过 get_redis_client() 获取共享客户端，不要每次自行 new。
"""

from redis.asyncio import Redis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 全局 Redis 客户端，模块级单例，由 init_redis() 赋值。
# redis.asyncio.Redis 内部自带连接池，多协程共享同一个客户端即可复用连接。
_redis_client: Redis | None = None


async def init_redis() -> Redis:
    """初始化全局 Redis 客户端（应用 startup 时调用）。

    设置 socket 超时与连接超时，保证所有外部访问都有超时保护，
    并在初始化时执行一次 PING 探活，确保连接可用。
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    _redis_client = Redis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
        socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT,
        health_check_interval=30,
    )
    # 启动时探活，连接失败应尽早暴露问题
    await _redis_client.ping()
    logger.info("Redis client initialized.")
    return _redis_client


async def close_redis() -> None:
    """关闭全局 Redis 客户端（应用 shutdown 时调用）。

    释放底层连接池，避免连接泄漏。
    """
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis client closed.")


def get_redis_client() -> Redis:
    """获取全局 Redis 客户端，可用作 FastAPI 依赖。

    若尚未初始化则抛出异常，提醒必须在应用启动流程中先调用 init_redis()。
    """
    if _redis_client is None:
        raise RuntimeError("Redis client 尚未初始化，请确认应用 startup 已调用 init_redis()")
    return _redis_client
