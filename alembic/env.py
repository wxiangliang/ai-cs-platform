"""Alembic 迁移环境配置（支持 SQLAlchemy async）。

关键点：
- DATABASE_URL 从应用配置 settings 读取，与运行时保持一致，不在 ini 中写死；
- 使用 async engine 执行迁移；
- target_metadata 指向 app.db.base.Base.metadata，autogenerate 据此对比表结构
  （模型统一在 app/db/base.py 末尾导入，无需在本文件重复导入）。
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.base import Base

# Alembic Config 对象
config = context.config

# 将运行时的 DATABASE_URL 注入 Alembic，保证迁移与应用使用同一数据库。
# set_main_option 走 configparser 插值语法，密码中含 % 时必须转义为 %%
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

# 初始化日志
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate 的目标元数据
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅根据 URL 生成 SQL，不实际连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """在给定连接上执行迁移（供 async 调用包裹）。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线模式：使用 async engine 连接数据库并执行迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        # 迁移是一次性短任务，使用 NullPool 不维护长连接池
        poolclass=NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
