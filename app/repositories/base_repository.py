"""通用 Repository 基类。

封装最常用的 create / get_by_id / update 方法，减少各表 Repository 的重复代码。
泛型参数 ModelType 绑定到具体 ORM 模型。
"""

from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

# 绑定到 ORM 模型基类，便于类型提示
ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """通用数据访问基类。

    子类通过 `model` 指定对应的 ORM 模型类。
    所有方法接收外部传入的 AsyncSession，不自行管理事务边界。
    """

    def __init__(self, model: type[ModelType]) -> None:
        self.model = model

    async def create(self, session: AsyncSession, **fields: Any) -> ModelType:
        """新建一条记录。

        flush 之后即可拿到数据库生成的主键与默认值（created_at 等），
        但不在此处 commit，交由上层统一提交。
        """
        instance = self.model(**fields)
        session.add(instance)
        await session.flush()
        return instance

    async def get_by_id(self, session: AsyncSession, id_: Any) -> ModelType | None:
        """根据主键查询单条记录，不存在返回 None。"""
        return await session.get(self.model, id_)

    async def update(self, session: AsyncSession, instance: ModelType, **fields: Any) -> ModelType:
        """更新给定实例的字段。

        仅修改传入的字段，flush 触发 UPDATE。对带乐观锁的模型，
        flush 时若版本冲突会抛 StaleDataError。
        """
        for key, value in fields.items():
            setattr(instance, key, value)
        await session.flush()
        return instance

    async def _first(self, session: AsyncSession, stmt: Any) -> ModelType | None:
        """执行查询并返回第一条结果（内部辅助方法）。"""
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _all(self, session: AsyncSession, stmt: Any) -> list[ModelType]:
        """执行查询并返回全部结果（内部辅助方法）。"""
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # 暴露 select 给子类构造查询时使用
    _select = staticmethod(select)
