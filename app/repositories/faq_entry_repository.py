"""faq_entry 数据访问层。"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.faq_entry import FaqEntry
from app.repositories.base_repository import BaseRepository


class FaqEntryRepository(BaseRepository[FaqEntry]):
    """FAQ 标准问答 Repository。"""

    def __init__(self) -> None:
        super().__init__(FaqEntry)

    async def get_by_ids(
        self, session: AsyncSession, tenant_id: str, faq_ids: list[str]
    ) -> list[FaqEntry]:
        """按 id 批量取 FAQ（只取生效条目，向量命中后的水合）。"""
        if not faq_ids:
            return []
        stmt = select(FaqEntry).where(
            FaqEntry.tenant_id == tenant_id,
            FaqEntry.id.in_(faq_ids),
            FaqEntry.status == "active",
        )
        return await self._all(session, stmt)


    async def list_by_tenant(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FaqEntry]:
        """运营列表（Stage 29 批 3）：全状态，更新时间倒序。"""
        stmt = (
            select(FaqEntry)
            .where(FaqEntry.tenant_id == tenant_id)
            .order_by(FaqEntry.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return await self._all(session, stmt)

    async def list_active(
        self, session: AsyncSession, tenant_id: str, limit: int = 2000
    ) -> list[FaqEntry]:
        """列出租户全部生效 FAQ（reindex 用）。"""
        stmt = (
            select(FaqEntry)
            .where(FaqEntry.tenant_id == tenant_id, FaqEntry.status == "active")
            .limit(limit)
        )
        return await self._all(session, stmt)

    async def increment_hit(self, session: AsyncSession, tenant_id: str, faq_id: str) -> None:
        """命中计数 +1（运营观测冷热问题，原子更新避免读改写竞态）。"""
        await session.execute(
            update(FaqEntry)
            .where(FaqEntry.tenant_id == tenant_id, FaqEntry.id == faq_id)
            .values(hit_count=FaqEntry.hit_count + 1)
        )


# 模块级单例
faq_entry_repository = FaqEntryRepository()
