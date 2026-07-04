"""知识库摄取服务（KbIngestService）。

两个入口：
- upsert_document：纯文本/Markdown 内容 → 结构感知切分（chunk_blocks）；
- upsert_document_file：文件上传 → 解析器链（MinerU/Docling/内置）→ Block IR → 结构切分。

流程：解析 → 切分 → embedding（批量）→ 写 PG（事实来源）→ 写向量后端。
一致性策略：PG 成功后写后端；后端写失败不回滚 PG，标记 needs_reindex=true
并告警，由 reindex 命令兜底重建（检索允许秒级滞后）。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.kb.backends.factory import get_vector_backend
from app.kb.chunker import Chunk, chunk_blocks, clean_text
from app.kb.embedding import embedding_client
from app.kb.parsing.base import markdown_to_blocks
from app.kb.parsing.router import parse_document
from app.kb.types import ChunkIndexItem, FaqIndexItem
from app.repositories.faq_entry_repository import faq_entry_repository
from app.repositories.kb_chunk_repository import kb_chunk_repository
from app.repositories.kb_document_repository import kb_document_repository

logger = get_logger(__name__)


class KbIngestService:
    """知识库写入口（管理 API 与 reindex 共用）。"""

    async def upsert_document(
        self,
        session: AsyncSession,
        tenant_id: str,
        title: str,
        raw_content: str,
        source_type: str = "policy",
        document_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        file_name: str | None = None,
        parser: str | None = None,
    ) -> dict[str, Any]:
        """创建/更新文档（文本入口）：Markdown 结构解析 → 结构感知切分。

        更新采用「整篇重建分块」策略，避免半新半旧。
        """
        # 文本内容统一走 Markdown 结构解析（纯文本没有标题时退化为段落切分，行为兼容 v1）
        blocks = markdown_to_blocks(clean_text(raw_content))
        chunks = chunk_blocks(blocks)
        if not chunks:
            raise ValueError("文档内容为空，无法分块")
        return await self._persist_document(
            session, tenant_id, title, raw_content, source_type,
            chunks, document_id=document_id, metadata=metadata,
            file_name=file_name, parser=parser or "builtin_markdown",
        )

    async def upsert_document_file(
        self,
        session: AsyncSession,
        tenant_id: str,
        file_name: str,
        content: bytes,
        title: str | None = None,
        source_type: str = "policy",
        document_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建/更新文档（文件入口）：解析器链 → Block IR → 结构感知切分。

        解析失败直接抛错（DocumentParseError），不入库半成品。
        """
        parsed = await parse_document(file_name, content)
        chunks = chunk_blocks(parsed.blocks)
        if not chunks:
            raise ValueError("文件解析后无有效内容，无法分块")
        # raw_content 存解析后的 Markdown 全文（重建分块依据，不存二进制）
        return await self._persist_document(
            session, tenant_id, title or file_name, parsed.to_markdown(), source_type,
            chunks, document_id=document_id, metadata=metadata,
            file_name=file_name, parser=parsed.parser,
        )

    async def _persist_document(
        self,
        session: AsyncSession,
        tenant_id: str,
        title: str,
        raw_content: str,
        source_type: str,
        chunks: list[Chunk],
        *,
        document_id: str | None,
        metadata: dict[str, Any] | None,
        file_name: str | None,
        parser: str | None,
    ) -> dict[str, Any]:
        """落库：文档 + 分块（含结构元数据）+ 向量后端索引。"""
        # 1. 文档记录
        if document_id:
            doc = await kb_document_repository.get_by_id_and_tenant(
                session, tenant_id, document_id
            )
            if doc is None:
                raise ValueError("文档不存在")
            await kb_document_repository.update(
                session, doc, title=title, raw_content=raw_content,
                source_type=source_type, metadata_json=metadata, status="published",
                file_name=file_name, parser=parser,
            )
            # 旧分块整篇删除（PG + 向量后端）
            await kb_chunk_repository.delete_by_document(session, tenant_id, doc.id)
        else:
            doc = await kb_document_repository.create(
                session, tenant_id=tenant_id, title=title, raw_content=raw_content,
                source_type=source_type, metadata_json=metadata, status="published",
                file_name=file_name, parser=parser,
            )

        # 2. 批量 embedding
        vectors = await embedding_client.embed([c.text for c in chunks])

        # 3. 写 PG（事实来源，与文档同事务），chunk 带结构元数据；
        # section_path = 标题路径连接串（父子分块的聚合键，Stage 06-04）
        index_items: list[ChunkIndexItem] = []
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            heading_path = (chunk.metadata or {}).get("heading_path") or []
            record = await kb_chunk_repository.create(
                session,
                tenant_id=tenant_id,
                document_id=doc.id,
                chunk_index=i,
                content=chunk.text,
                section_path=(" > ".join(heading_path))[:512] or None,
                embedding_json=vec,
                metadata_json=chunk.metadata or None,
            )
            index_items.append(
                ChunkIndexItem(
                    chunk_id=record.id, tenant_id=tenant_id,
                    document_id=doc.id, embedding=vec,
                )
            )

        # 4. 写向量后端：失败不回滚 PG，标记待重建
        backend = get_vector_backend()
        try:
            await backend.delete_document(tenant_id, doc.id)
            await backend.index_chunks(tenant_id, index_items)
            doc.needs_reindex = False
        except Exception:  # noqa: BLE001 - 后端故障由 reindex 兜底
            logger.exception("vector backend index failed, mark needs_reindex: doc=%s", doc.id)
            doc.needs_reindex = True
        await session.flush()
        return {
            "document_id": doc.id,
            "chunks": len(index_items),
            "parser": parser,
            "needs_reindex": doc.needs_reindex,
        }

    async def disable_document(
        self, session: AsyncSession, tenant_id: str, document_id: str
    ) -> None:
        """软停用文档：PG 标记 disabled + 向量后端删除索引（PG 分块保留供审计）。"""
        doc = await kb_document_repository.get_by_id_and_tenant(session, tenant_id, document_id)
        if doc is None:
            raise ValueError("文档不存在")
        doc.status = "disabled"
        await session.flush()
        try:
            await get_vector_backend().delete_document(tenant_id, document_id)
        except Exception:  # noqa: BLE001
            logger.exception("vector backend delete failed: doc=%s", document_id)
            doc.needs_reindex = True
            await session.flush()

    async def upsert_faq(
        self,
        session: AsyncSession,
        tenant_id: str,
        question: str,
        answer: str,
        category: str | None = None,
        faq_id: str | None = None,
    ) -> dict[str, Any]:
        """创建/更新 FAQ 标准问答。"""
        vec = (await embedding_client.embed([question]))[0]
        if faq_id:
            faq = await faq_entry_repository.get_by_id(session, faq_id)
            if faq is None or faq.tenant_id != tenant_id:
                raise ValueError("FAQ 不存在")
            await faq_entry_repository.update(
                session, faq, question=question, answer=answer,
                category=category, question_embedding_json=vec, status="active",
            )
        else:
            faq = await faq_entry_repository.create(
                session, tenant_id=tenant_id, question=question, answer=answer,
                category=category, question_embedding_json=vec,
            )
        try:
            await get_vector_backend().index_faqs(
                tenant_id, [FaqIndexItem(faq_id=faq.id, tenant_id=tenant_id, embedding=vec)]
            )
            faq.needs_reindex = False
        except Exception:  # noqa: BLE001 - 向量写失败不回滚 PG，标记待重建（Stage 13）
            logger.exception("vector backend faq index failed, mark needs_reindex: faq=%s", faq.id)
            faq.needs_reindex = True
        return {"faq_id": faq.id, "needs_reindex": faq.needs_reindex}


# 模块级单例
kb_ingest_service = KbIngestService()
