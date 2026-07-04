"""kb_chunk 数据访问层。

除常规 CRUD 外，提供关键词召回（混合检索的文本路）：
jieba 分出的关键词做 ILIKE 匹配，按命中词数排序。
只做数据访问与简单打分，不做业务决策。
"""

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kb_chunk import KbChunk
from app.models.kb_document import KbDocument
from app.repositories.base_repository import BaseRepository


class KbChunkRepository(BaseRepository[KbChunk]):
    """知识库分块 Repository。"""

    def __init__(self) -> None:
        super().__init__(KbChunk)

    async def delete_by_document(
        self, session: AsyncSession, tenant_id: str, document_id: str
    ) -> None:
        """删除某文档全部分块（文档更新时整篇重建）。"""
        await session.execute(
            delete(KbChunk).where(
                KbChunk.tenant_id == tenant_id, KbChunk.document_id == document_id
            )
        )

    async def get_by_ids(
        self, session: AsyncSession, tenant_id: str, chunk_ids: list[str]
    ) -> list[KbChunk]:
        """按 id 批量取分块（向量命中后的内容水合）。"""
        if not chunk_ids:
            return []
        stmt = select(KbChunk).where(
            KbChunk.tenant_id == tenant_id, KbChunk.id.in_(chunk_ids)
        )
        return await self._all(session, stmt)

    async def list_by_tenant(
        self, session: AsyncSession, tenant_id: str, limit: int = 5000
    ) -> list[KbChunk]:
        """列出租户全部分块（reindex 用）。"""
        stmt = select(KbChunk).where(KbChunk.tenant_id == tenant_id).limit(limit)
        return await self._all(session, stmt)

    async def list_section(
        self,
        session: AsyncSession,
        tenant_id: str,
        document_id: str,
        section_path: str,
        limit: int = 10,
    ) -> list[KbChunk]:
        """取同文档同章节的全部分块（父子分块：子块命中后聚合章节上下文）。"""
        stmt = (
            select(KbChunk)
            .where(
                KbChunk.tenant_id == tenant_id,
                KbChunk.document_id == document_id,
                KbChunk.section_path == section_path,
            )
            .order_by(KbChunk.chunk_index.asc())
            .limit(limit)
        )
        return await self._all(session, stmt)

    async def search_by_keywords(
        self,
        session: AsyncSession,
        tenant_id: str,
        keywords: list[str],
        limit: int = 20,
    ) -> list[tuple[KbChunk, int]]:
        """关键词召回：任一关键词 ILIKE 命中即入候选，按命中词数降序。

        只召回线上生效文档下的分块（Stage 16 后生效判据为
        published_version 非空且未 archived；编辑已发布文档时 status 可回到 draft，
        但旧版分块仍应继续服务线上检索）。返回 (chunk, 命中词数)。
        """
        if not keywords:
            return []
        conditions = [KbChunk.content.ilike(f"%{kw}%") for kw in keywords]
        stmt = (
            select(KbChunk)
            .join(KbDocument, KbDocument.id == KbChunk.document_id)
            .where(
                KbChunk.tenant_id == tenant_id,
                KbDocument.tenant_id == tenant_id,
                KbDocument.published_version.isnot(None),
                KbDocument.status != "archived",
                or_(*conditions),
            )
            .limit(limit * 3)  # 取宽一点，Python 层按命中词数重排后再截断
        )
        chunks = await self._all(session, stmt)
        scored = [
            (chunk, sum(1 for kw in keywords if kw.lower() in chunk.content.lower()))
            for chunk in chunks
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]


# 模块级单例
kb_chunk_repository = KbChunkRepository()
