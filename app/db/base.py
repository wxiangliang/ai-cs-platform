"""SQLAlchemy Declarative Base 与通用 Mixin。

提供后续业务 Model 复用的基类与公共字段 Mixin。
Alembic autogenerate 依赖此处的 Base.metadata 收集表结构，
因此文件末尾会导入 app.models 包，确保所有模型都被注册到 metadata。
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。

    后续业务 Model 统一继承 Base；Base.metadata 用于 Alembic 迁移收集表信息。
    """


class UUIDPkMixin:
    """字符串 UUID 主键 Mixin。

    使用应用层生成的 UUID 字符串作为主键，便于跨表引用（如 session_id、message_id）
    且不依赖数据库自增序列，方便后续分库 / 多租户扩展。
    """

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
        comment="主键，UUID 字符串",
    )


class IntPkMixin:
    """自增主键 Mixin（BigInteger），供需要数值主键的表按需使用。"""

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键 ID",
    )


class TenantMixin:
    """多租户 Mixin。

    所有业务表从第一天起就携带 tenant_id，便于后续多租户隔离。
    这里不单独建单列索引，由各表的复合索引（tenant_id + ...）覆盖。
    """

    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="租户 ID",
    )


class CreatedAtMixin:
    """仅创建时间 Mixin。

    用于只需要记录创建时间、不需要更新时间的表（如消息、决策日志）。
    时间由数据库 func.now() 生成，避免应用与数据库时钟不一致。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )


class TimestampMixin(CreatedAtMixin):
    """创建时间 + 更新时间 Mixin。

    - created_at：继承自 CreatedAtMixin
    - updated_at：记录最后更新时间，更新时由数据库自动刷新
    """

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间",
    )


# 在文件末尾导入所有 ORM 模型，确保 Alembic autogenerate 能通过 Base.metadata
# 收集到全部表。放在末尾是为了先定义 Base，避免与 models 模块形成循环导入。
from app import models  # noqa: E402,F401
