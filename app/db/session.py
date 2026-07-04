"""PostgreSQL 异步数据库会话模块。

使用 SQLAlchemy 2.x async + asyncpg，提供全局共享的连接池 engine、
session 工厂以及 FastAPI 依赖注入用的 get_db_session()。
关键点：engine 全局只创建一次，绝不在每次请求中新建。
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# 全局 async engine：进程内唯一，内部维护连接池。
# pool_pre_ping=True 会在取连接时做一次探活，避免使用已失效的连接。
async_engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,
    # asyncpg 层超时：建连超时 + 单条语句执行超时。
    # DB_POOL_TIMEOUT 只约束「从池里取连接」，SQL 执行本身必须另有超时，
    # 否则慢查询/行锁等待会无限挂起请求（违反「所有外部访问必须有 timeout」约束）
    connect_args={
        "timeout": settings.DB_CONNECT_TIMEOUT,
        "command_timeout": settings.DB_COMMAND_TIMEOUT,
    },
)

# session 工厂：每个请求 / 任务从这里创建独立的 AsyncSession。
# expire_on_commit=False 避免 commit 后访问 ORM 对象触发额外的懒加载查询。
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：提供一个请求级别的数据库会话。

    使用方式：`session: AsyncSession = Depends(get_db_session)`。

    生命周期管理：
    - 正常结束：提交事务 commit；
    - 抛出异常：回滚事务 rollback，并向上抛出；
    - 无论成败：最终关闭 session，归还连接到连接池。
    """
    session = AsyncSessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        # 出现异常时回滚，保证数据库不会处于半提交状态
        await session.rollback()
        raise
    finally:
        # 关闭 session，连接被释放回连接池（并非真正断开物理连接）
        await session.close()


async def dispose_engine() -> None:
    """优雅释放 engine 连接池，应用关闭时调用。"""
    logger.info("Disposing database engine connection pool...")
    await async_engine.dispose()
