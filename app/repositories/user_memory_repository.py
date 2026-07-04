"""user_memory 数据访问层。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_memory import UserMemory
from app.repositories.base_repository import BaseRepository


class UserMemoryRepository(BaseRepository[UserMemory]):
    """用户长期记忆 Repository。"""

    def __init__(self) -> None:
        super().__init__(UserMemory)

    async def list_recent(
        self, session: AsyncSession, tenant_id: str, user_id: str, limit: int = 5
    ) -> list[UserMemory]:
        """取用户最近的生效记忆（更新时间倒序）。"""
        stmt = (
            select(UserMemory)
            .where(
                UserMemory.tenant_id == tenant_id,
                UserMemory.user_id == user_id,
                UserMemory.status == "active",
            )
            .order_by(UserMemory.updated_at.desc())
            .limit(limit)
        )
        return await self._all(session, stmt)

    async def exists_content(
        self, session: AsyncSession, tenant_id: str, user_id: str, content: str
    ) -> bool:
        """完全相同内容是否已存在（写入去重）。"""
        stmt = select(UserMemory.id).where(
            UserMemory.tenant_id == tenant_id,
            UserMemory.user_id == user_id,
            UserMemory.content == content,
            UserMemory.status == "active",
        )
        return (await session.execute(stmt)).first() is not None


# 模块级单例
user_memory_repository = UserMemoryRepository()
