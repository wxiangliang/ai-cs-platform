"""向量索引重建 CLI。

从 PG（事实来源）全量重建当前向量后端的索引，
用于：后端切换、后端故障恢复（needs_reindex 兜底）、EMBEDDING_DIM 变更。

用法：
    uv run python -m app.kb.reindex --tenant t1
    uv run python -m app.kb.reindex --tenant t1 --recompute-embedding  # 换 embedding 模型/维度后
"""

import argparse
import asyncio

from app.core.logging import get_logger, setup_logging
from app.db.session import AsyncSessionLocal, dispose_engine
from app.kb.backends.factory import get_vector_backend
from app.kb.embedding import embedding_client
from app.kb.types import ChunkIndexItem, FaqIndexItem
from app.repositories.faq_entry_repository import faq_entry_repository
from app.repositories.kb_chunk_repository import kb_chunk_repository
from app.repositories.kb_document_repository import kb_document_repository

logger = get_logger(__name__)


async def reindex_tenant(tenant_id: str, recompute_embedding: bool = False) -> None:
    """重建某租户的全部向量索引（chunk + faq）。"""
    backend = get_vector_backend()
    async with AsyncSessionLocal() as session:
        # —— 分块 ——
        active_docs = {d.id for d in await kb_document_repository.list_active(session, tenant_id)}
        chunks = await kb_chunk_repository.list_by_tenant(session, tenant_id)
        chunks = [c for c in chunks if c.document_id in active_docs]

        if recompute_embedding and chunks:
            vectors = await embedding_client.embed([c.content for c in chunks])
            for chunk, vec in zip(chunks, vectors):
                chunk.embedding_json = vec
            await session.flush()

        items = [
            ChunkIndexItem(
                chunk_id=c.id, tenant_id=tenant_id,
                document_id=c.document_id, embedding=c.embedding_json,
            )
            for c in chunks
            if c.embedding_json
        ]
        await backend.index_chunks(tenant_id, items)
        logger.info("reindexed %s chunks for tenant=%s", len(items), tenant_id)

        # —— FAQ ——
        faqs = await faq_entry_repository.list_active(session, tenant_id)
        if recompute_embedding and faqs:
            vectors = await embedding_client.embed([f.question for f in faqs])
            for faq, vec in zip(faqs, vectors):
                faq.question_embedding_json = vec
            await session.flush()

        faq_items = [
            FaqIndexItem(faq_id=f.id, tenant_id=tenant_id, embedding=f.question_embedding_json)
            for f in faqs
            if f.question_embedding_json
        ]
        await backend.index_faqs(tenant_id, faq_items)
        logger.info("reindexed %s faqs for tenant=%s", len(faq_items), tenant_id)

        # 清除待重建标记（文档 + FAQ）
        for doc_id in active_docs:
            doc = await kb_document_repository.get_by_id_and_tenant(session, tenant_id, doc_id)
            if doc is not None and doc.needs_reindex:
                doc.needs_reindex = False
        for f in faqs:
            if f.needs_reindex:
                f.needs_reindex = False
        await session.commit()


async def _main() -> None:
    parser = argparse.ArgumentParser(description="重建向量后端索引（从 PG 事实来源）")
    parser.add_argument("--tenant", required=True, help="租户 ID")
    parser.add_argument(
        "--recompute-embedding", action="store_true",
        help="重新计算 embedding（换模型/维度后使用）",
    )
    args = parser.parse_args()
    setup_logging()
    try:
        await reindex_tenant(args.tenant, recompute_embedding=args.recompute_embedding)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
