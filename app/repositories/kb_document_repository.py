"""kb_document 数据访问层。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kb_document import KbDocument
from app.repositories.base_repository import BaseRepository


class KbDocumentRepository(BaseRepository[KbDocument]):
    """知识库文档 Repository。"""

    def __init__(self) -> None:
        super().__init__(KbDocument)

    async def get_by_id_and_tenant(
        self, session: AsyncSession, tenant_id: str, document_id: str
    ) -> KbDocument | None:
        """按 (tenant_id, id) 查询文档（租户隔离强制）。"""
        stmt = select(KbDocument).where(
            KbDocument.tenant_id == tenant_id, KbDocument.id == document_id
        )
        return await self._first(session, stmt)

    async def get_by_ids(
        self, session: AsyncSession, tenant_id: str, document_ids: list[str]
    ) -> list[KbDocument]:
        """批量按 id 查询文档（租户隔离；检索水合用，替代循环单查）。"""
        if not document_ids:
            return []
        stmt = select(KbDocument).where(
            KbDocument.tenant_id == tenant_id, KbDocument.id.in_(document_ids)
        )
        return await self._all(session, stmt)


    async def list_by_tenant(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[KbDocument]:
        """运营列表（Stage 29 批 3）：全状态可查，更新时间倒序。"""
        stmt = select(KbDocument).where(KbDocument.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(KbDocument.status == status)
        stmt = stmt.order_by(KbDocument.updated_at.desc()).limit(limit).offset(offset)
        return await self._all(session, stmt)

    async def list_active(
        self, session: AsyncSession, tenant_id: str, limit: int = 500
    ) -> list[KbDocument]:
        """列出租户下所有生效文档（已发布过且未 archived，reindex 用，Stage 16）。"""
        stmt = (
            select(KbDocument)
            .where(
                KbDocument.tenant_id == tenant_id,
                KbDocument.published_version.isnot(None),
                KbDocument.status != "archived",
            )
            .limit(limit)
        )
        return await self._all(session, stmt)


# 模块级单例
kb_document_repository = KbDocumentRepository()
