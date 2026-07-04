"""kb_document_version 数据访问层（Stage 16）。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kb_document_version import KbDocumentVersion
from app.repositories.base_repository import BaseRepository


class KbDocumentVersionRepository(BaseRepository[KbDocumentVersion]):
    """文档版本 Repository（append-only）。"""

    def __init__(self) -> None:
        super().__init__(KbDocumentVersion)

    async def next_version(
        self, session: AsyncSession, tenant_id: str, document_id: str
    ) -> int:
        """该文档的下一个版本号（同文档内自增，1 起）。"""
        stmt = select(func.coalesce(func.max(KbDocumentVersion.version), 0)).where(
            KbDocumentVersion.tenant_id == tenant_id,
            KbDocumentVersion.document_id == document_id,
        )
        return int((await session.execute(stmt)).scalar_one()) + 1

    async def list_by_document(
        self, session: AsyncSession, tenant_id: str, document_id: str, limit: int = 50
    ) -> list[KbDocumentVersion]:
        """版本历史（version 倒序）。"""
        stmt = (
            select(KbDocumentVersion)
            .where(
                KbDocumentVersion.tenant_id == tenant_id,
                KbDocumentVersion.document_id == document_id,
            )
            .order_by(KbDocumentVersion.version.desc())
            .limit(limit)
        )
        return await self._all(session, stmt)

    async def get_version(
        self, session: AsyncSession, tenant_id: str, document_id: str, version: int
    ) -> KbDocumentVersion | None:
        """取指定版本（回滚用）。"""
        stmt = select(KbDocumentVersion).where(
            KbDocumentVersion.tenant_id == tenant_id,
            KbDocumentVersion.document_id == document_id,
            KbDocumentVersion.version == version,
        )
        return await self._first(session, stmt)


# 模块级单例
kb_document_version_repository = KbDocumentVersionRepository()
