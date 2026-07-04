"""健康检查接口。

提供两类探针：
- GET /api/health        ：存活探针（liveness），只确认进程在跑；
- GET /api/health/ready  ：就绪探针（readiness），检查 PostgreSQL 与 Redis 是否可用。

route 层只做接入与依赖检查，不写复杂业务逻辑。
"""

from fastapi import APIRouter
from sqlalchemy import text

from app.cache.redis_client import get_redis_client
from app.core.config import settings
from app.core.logging import get_logger
from app.core.responses import success_response
from app.db.session import AsyncSessionLocal

logger = get_logger(__name__)

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
async def health() -> dict:
    """存活探针：仅返回 ok，表示服务进程正常运行。"""
    return success_response(data={"status": "ok"})


@router.get("/ready")
async def ready() -> dict:
    """就绪探针：检查 PostgreSQL 与 Redis 依赖是否可用。

    任一依赖不可用时，对应字段标记为 down，整体 ready=False。
    这里捕获异常并降级返回，而不是抛 500，方便编排系统读取细节。
    """
    db_ok = await _check_db()
    redis_ok = await _check_redis()
    dependencies = {
        "postgres": "up" if db_ok else "down",
        "redis": "up" if redis_ok else "down",
    }
    # 知识库向量后端（可选依赖）：down 只降级 RAG 能力，不影响整体 ready
    if settings.KB_ENABLED:
        kb_ok = await _check_kb_backend()
        dependencies[f"kb_{settings.KB_BACKEND}"] = "up" if kb_ok else "down"

    overall_ready = db_ok and redis_ok

    return success_response(
        data={
            "status": "ok" if overall_ready else "degraded",
            "ready": overall_ready,
            "dependencies": dependencies,
        }
    )


async def _check_db() -> bool:
    """执行一次 SELECT 1 验证数据库连接池可用。"""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Health check: PostgreSQL unavailable")
        return False


async def _check_redis() -> bool:
    """执行一次 PING 验证 Redis 可用。"""
    try:
        client = get_redis_client()
        await client.ping()
        return True
    except Exception:
        logger.exception("Health check: Redis unavailable")
        return False


async def _check_kb_backend() -> bool:
    """检查知识库向量后端（Milvus 等）可用性。"""
    try:
        from app.kb.backends.factory import get_vector_backend

        return await get_vector_backend().health()
    except Exception:
        logger.exception("Health check: KB vector backend unavailable")
        return False
