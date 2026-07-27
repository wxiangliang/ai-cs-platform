"""检索层生效过滤回归（不依赖 Milvus/PG：向量后端与仓储全部打桩）。

锁定修复：未发布/已归档文档的 chunk 必须在 rerank/截断**之前**被过滤——
否则死文档挤占 RAG_TOP_K 名额且无回填，极端情况（top 全来自已归档文档）
会把有可用内容的查询误判为拒答。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.config import settings
from app.kb.retriever import RetrievalTrace, kb_retriever
from app.kb.types import Hit
from app.repositories.kb_chunk_repository import kb_chunk_repository
from app.repositories.kb_document_repository import kb_document_repository


class _FakeBackend:
    """假向量后端：返回预置命中（Milvus 索引里不带发布状态，与线上一致）。"""

    def __init__(self, hits: list[Hit]) -> None:
        self._hits = hits

    async def search_chunks(self, tenant_id, query_vec, top_k):
        return list(self._hits)


def _chunk(cid: str, doc_id: str):
    return SimpleNamespace(id=cid, document_id=doc_id, content=f"内容{cid}", section_path=None)


async def test_archived_doc_chunks_filtered_before_truncation(monkeypatch):
    """向量召回前 6 名全来自未生效文档 → 已发布文档的候选仍应回填进结果。"""
    vector_hits = [
        Hit(id=f"c{i}", score=0.9 - i * 0.01, source_backend="milvus") for i in range(8)
    ]
    chunks = [_chunk(f"c{i}", "doc-bad" if i < 6 else "doc-good") for i in range(8)]
    docs = [
        # 从未发布过的草稿（published_version 为空）→ 不生效
        SimpleNamespace(id="doc-bad", title="旧政策", published_version=None, status="draft"),
        SimpleNamespace(id="doc-good", title="现行政策", published_version=2, status="published"),
    ]
    monkeypatch.setattr(settings, "RAG_TOP_K", 5)
    monkeypatch.setattr(settings, "RERANKER_PROVIDER", "off")
    monkeypatch.setattr("app.kb.retriever.get_vector_backend", lambda: _FakeBackend(vector_hits))
    monkeypatch.setattr(
        "app.kb.retriever.embedding_client",
        SimpleNamespace(embed=AsyncMock(return_value=[[0.1, 0.2]])),
    )
    monkeypatch.setattr(kb_chunk_repository, "search_by_keywords", AsyncMock(return_value=[]))

    async def _get_chunks(session, tenant_id, ids):
        wanted = set(ids)
        return [c for c in chunks if c.id in wanted]

    monkeypatch.setattr(kb_chunk_repository, "get_by_ids", _get_chunks)
    monkeypatch.setattr(kb_document_repository, "get_by_ids", AsyncMock(return_value=docs))

    trace = RetrievalTrace(query="退货政策", backend="fake")
    hits = await kb_retriever.search_chunks(None, "t1", "退货政策", trace)

    # 修复前：截断后 top5 全是归档文档 chunk，过滤后为空 → 误拒答
    assert hits, "生效过滤必须在截断前完成，已发布文档的候选应回填进结果"
    assert {h.document_id for h in hits} == {"doc-good"}
    assert all(h.title == "现行政策" for h in hits)


async def _run_search(monkeypatch, vector_hits, chunks, docs, reranker="off"):
    """公共装配：打桩后端与仓储，跑一次 search_chunks 返回 (hits, trace)。"""
    monkeypatch.setattr(settings, "RAG_TOP_K", 5)
    monkeypatch.setattr(settings, "RERANKER_PROVIDER", reranker)
    monkeypatch.setattr("app.kb.retriever.get_vector_backend", lambda: _FakeBackend(vector_hits))
    monkeypatch.setattr(
        "app.kb.retriever.embedding_client",
        SimpleNamespace(embed=AsyncMock(return_value=[[0.1, 0.2]])),
    )
    monkeypatch.setattr(kb_chunk_repository, "search_by_keywords", AsyncMock(return_value=[]))

    async def _get_chunks(session, tenant_id, ids):
        wanted = set(ids)
        return [c for c in chunks if c.id in wanted]

    monkeypatch.setattr(kb_chunk_repository, "get_by_ids", _get_chunks)
    monkeypatch.setattr(kb_document_repository, "get_by_ids", AsyncMock(return_value=docs))
    trace = RetrievalTrace(query="退货政策", backend="fake")
    hits = await kb_retriever.search_chunks(None, "t1", "退货政策", trace)
    return hits, trace


async def test_reranked_flag_reflects_actual_behavior(monkeypatch):
    """trace.reranked 记录实际行为：配置=local 但重排未执行（无 rerank_score）→ False。"""
    vector_hits = [Hit(id="c0", score=0.9, source_backend="milvus")]
    chunks = [_chunk("c0", "doc-good")]
    docs = [SimpleNamespace(id="doc-good", title="政策", published_version=1, status="published")]

    # 模拟 local 配置但模型不可用 → rerank_hits 静默降级为截断（不写 rerank_score）
    async def _degraded_rerank(query, hits, top_k):
        return hits[:top_k]

    monkeypatch.setattr("app.kb.retriever.rerank_hits", _degraded_rerank)
    _, trace = await _run_search(monkeypatch, vector_hits, chunks, docs, reranker="local")
    assert trace.reranked is False  # 修复前：按配置记 True，评估失真


async def test_ambiguity_uses_vector_scores_from_candidates(monkeypatch):
    """歧义检测按向量分同源判定：异文档向量分差 < DELTA → ambiguous。"""
    vector_hits = [
        Hit(id="a1", score=0.90, source_backend="milvus"),
        Hit(id="b1", score=0.88, source_backend="milvus"),  # 分差 0.02 < 0.05
    ]
    chunks = [_chunk("a1", "doc-a"), _chunk("b1", "doc-b")]
    docs = [
        SimpleNamespace(id="doc-a", title="A政策", published_version=1, status="published"),
        SimpleNamespace(id="doc-b", title="B政策", published_version=1, status="published"),
    ]
    _, trace = await _run_search(monkeypatch, vector_hits, chunks, docs)
    assert trace.ambiguous is True

    # 同文档接近分数不算歧义
    chunks_same = [_chunk("a1", "doc-a"), _chunk("b1", "doc-a")]
    docs_same = [SimpleNamespace(id="doc-a", title="A政策", published_version=1, status="published")]
    _, trace2 = await _run_search(monkeypatch, vector_hits, chunks_same, docs_same)
    assert trace2.ambiguous is False
