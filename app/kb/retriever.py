"""知识检索编排层（KbRetriever，后端无关）。

检索管道 v2（Stage 06-04，参考 RAGFlow/客服最优 pipeline 设计）：
1. Query 归一化：繁简转换 / 同义词扩展 / 型号识别 → 查询类型判定；
2. FAQ 精确层：问题向量相似度 ≥ FAQ_HIT_THRESHOLD → 直接返回标准答案；
3. 文档层多路宽召回（各 RAG_RECALL_TOP_K）：向量（Milvus）+ 关键词（PG trgm，
   含同义词扩展与型号词）→ **动态加权 RRF**（precise 查询关键词路权重大——
   型号/单号向量不可靠；semantic 查询向量路权重大）；
4. Rerank 精排（可选 CrossEncoder）→ 截到 RAG_TOP_K；
5. 水合 + **父子分块上下文**（命中子块按章节聚合兄弟块）；
6. 歧义检测：top1/top2 分差过小且来自不同文档 → 标记 ambiguous。

拒答阈值判断用向量原始余弦分（RRF/rerank 分只用于排序），
检索过程完整记录到 RetrievalTrace，由调用方落 decision_log.retrieval_json。
"""

from dataclasses import dataclass, field
from typing import Any

import jieba
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.experiments.resolver import effective
from app.core.logging import get_logger
from app.kb.backends.base import rrf_fuse
from app.kb.backends.factory import get_vector_backend
from app.kb.embedding import embedding_client
from app.kb.query_normalize import normalize_query
from app.kb.rerank import rerank_hits
from app.kb.types import Hit
from app.repositories.faq_entry_repository import faq_entry_repository
from app.repositories.kb_chunk_repository import kb_chunk_repository
from app.repositories.kb_document_repository import kb_document_repository

logger = get_logger(__name__)

# 动态权重（RRF 各路贡献）：precise=含型号/单号，关键词路更可靠
_WEIGHTS_BY_TYPE = {
    "precise": {"vector": 0.8, "keyword": 1.5},
    "semantic": {"vector": 1.2, "keyword": 0.8},
}


@dataclass
class RetrievalTrace:
    """一次检索的完整过程（落 decision_log.retrieval_json）。"""

    query: str
    backend: str
    faq_hits: list[dict[str, Any]] = field(default_factory=list)
    chunk_hits: list[dict[str, Any]] = field(default_factory=list)
    refused: bool = False
    degraded: bool = False
    # —— v2 管道观测 ——
    query_type: str = "semantic"  # precise / semantic（动态权重依据）
    expanded_terms: list[str] = field(default_factory=list)
    ambiguous: bool = False  # top1/top2 分差过小且异文档（可能需澄清）
    reranked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "backend": self.backend,
            "faq_hits": self.faq_hits,
            "chunk_hits": self.chunk_hits,
            "refused": self.refused,
            "degraded": self.degraded,
            "query_type": self.query_type,
            "expanded_terms": self.expanded_terms,
            "ambiguous": self.ambiguous,
            "reranked": self.reranked,
        }


@dataclass
class FaqAnswer:
    """FAQ 精确命中结果。"""

    faq_id: str
    question: str
    answer: str
    score: float


def _extract_keywords(query: str, max_terms: int = 8) -> list[str]:
    """jieba 分词提取关键词（≥2 字符，去重保序）。"""
    seen: list[str] = []
    for token in jieba.cut(query):
        token = token.strip()
        if len(token) >= 2 and token not in seen:
            seen.append(token)
    return seen[:max_terms]


class KbRetriever:
    """检索编排：FAQ 精确层 + 文档混合层。"""

    async def search_faq(
        self, session: AsyncSession, tenant_id: str, query: str, trace: RetrievalTrace
    ) -> FaqAnswer | None:
        """FAQ 精确层：达到阈值直接返回标准答案，否则 None。"""
        backend = get_vector_backend()
        query = normalize_query(query).text  # 繁简归一，命中简体 FAQ 库
        query_vec = (await embedding_client.embed([query]))[0]
        hits = await backend.search_faqs(tenant_id, query_vec, top_k=3)

        faqs = await faq_entry_repository.get_by_ids(session, tenant_id, [h.id for h in hits])
        faq_map = {f.id: f for f in faqs}
        for hit in hits:
            faq = faq_map.get(hit.id)
            if faq is None:
                continue  # 后端残留索引（PG 已停用），跳过
            trace.faq_hits.append({"faq_id": hit.id, "score": hit.score, "q": faq.question})
            if hit.score >= effective("FAQ_HIT_THRESHOLD"):
                await faq_entry_repository.increment_hit(session, tenant_id, faq.id)
                return FaqAnswer(
                    faq_id=faq.id, question=faq.question, answer=faq.answer, score=hit.score
                )
        return None

    async def search_chunks(
        self, session: AsyncSession, tenant_id: str, query: str, trace: RetrievalTrace
    ) -> list[Hit]:
        """文档层检索管道 v2：归一化 → 动态加权多路宽召回 → RRF → rerank →
        水合 + 章节上下文 → 歧义检测。

        返回的 Hit.score 是向量原始余弦分（关键词路无向量分记 0，
        拒答判断只看向量路 top1，见 answerer）；
        Hit.extra["section_context"] 为父子分块聚合的章节全文（生成用）。
        """
        # —— 1. Query 归一化 ——
        nq = normalize_query(query)
        trace.query_type = nq.query_type
        trace.expanded_terms = nq.expanded_terms
        weights = _WEIGHTS_BY_TYPE[nq.query_type]
        recall_k = effective("RAG_RECALL_TOP_K")

        backend = get_vector_backend()
        query_vec = (await embedding_client.embed([nq.text]))[0]

        # —— 2. 多路宽召回 ——
        vector_hits = await backend.search_chunks(tenant_id, query_vec, top_k=recall_k)
        # 关键词路：原词 + 同义词扩展 + 型号词（扩展词只进关键词路，不污染向量语义）
        keywords = _extract_keywords(nq.text) + nq.expanded_terms + nq.model_codes
        keyword_scored = await kb_chunk_repository.search_by_keywords(
            session, tenant_id, list(dict.fromkeys(keywords)), limit=recall_k
        )
        keyword_hits = [
            Hit(id=chunk.id, score=0.0, source_backend="pg_keyword",
                document_id=chunk.document_id, content=chunk.content)
            for chunk, _matched in keyword_scored
        ]

        # —— 3. 动态加权 RRF（粗排，保留 rerank 候选宽度）——
        vector_score_map = {h.id: h.score for h in vector_hits}
        keyword_ids = {h.id for h in keyword_hits}
        fused = rrf_fuse(
            [vector_hits, keyword_hits],
            top_k=recall_k,
            weights=[weights["vector"], weights["keyword"]],
        )

        # —— 4. Rerank 精排（off 时为 RRF 序截断）——
        # 精排前先给候选补内容（rerank 需要文本）
        need_content = [h for h in fused if not h.content]
        if need_content:
            chunks = await kb_chunk_repository.get_by_ids(
                session, tenant_id, [h.id for h in need_content]
            )
            content_map = {c.id: c.content for c in chunks}
            for h in need_content:
                h.content = content_map.get(h.id)
        top = await rerank_hits(nq.text, fused, settings.RAG_TOP_K)
        trace.reranked = str(effective("RERANKER_PROVIDER")).lower() == "local"

        # —— 5. 水合标题 + 父子分块章节上下文 ——
        chunks = await kb_chunk_repository.get_by_ids(session, tenant_id, [h.id for h in top])
        chunk_map = {c.id: c for c in chunks}
        doc_titles: dict[str, str] = {}
        hydrated: list[Hit] = []
        for hit in top:
            chunk = chunk_map.get(hit.id)
            if chunk is None:
                continue  # 后端残留索引，跳过
            doc_id = chunk.document_id
            if doc_id not in doc_titles:
                doc = await kb_document_repository.get_by_id_and_tenant(session, tenant_id, doc_id)
                # 生效判据（Stage 16）：已发布过（published_version 非空）且未 archived。
                # 编辑已发布文档时 status 会回到 draft 但 published_version 不变→线上仍服务旧版本
                if doc is None or doc.published_version is None or doc.status == "archived":
                    doc_titles[doc_id] = ""
                else:
                    doc_titles[doc_id] = doc.title
            if not doc_titles[doc_id]:
                continue
            hit.content = chunk.content
            hit.title = doc_titles[doc_id]
            hit.document_id = doc_id
            hit.score = vector_score_map.get(hit.id, 0.0)
            # 标记关键词路来源：纯关键词命中的向量分为 0（未进向量召回），
            # 拒答判断/摘录选块不能只看向量分把它们误当无关（v2 精确查询关键词提权本意）
            hit.extra["from_keyword"] = hit.id in keyword_ids
            # 父子分块：命中子块 → 聚合同章节兄弟块作为生成上下文（字符上限截断）。
            # 行级去重：表格块自带引导句（=章节段落前缀）、各块都有标题路径前缀，
            # 直接拼接会重复，按行去重后再拼
            if chunk.section_path:
                siblings = await kb_chunk_repository.list_section(
                    session, tenant_id, doc_id, chunk.section_path
                )
                if len(siblings) > 1:
                    seen_lines: set[str] = set()
                    lines: list[str] = []
                    for sibling in siblings:
                        for line in (sibling.content or "").split("\n"):
                            key = line.strip()
                            if key and key not in seen_lines:
                                seen_lines.add(key)
                                lines.append(line)
                    hit.extra["section_context"] = "\n".join(lines)[
                        : settings.RAG_SECTION_CONTEXT_CHARS
                    ]
            hydrated.append(hit)
            trace.chunk_hits.append(
                {"chunk_id": hit.id, "doc": hit.title, "vec_score": hit.score,
                 "rrf": hit.extra.get("rrf_score"),
                 "rerank": hit.extra.get("rerank_score"), "src": hit.source_backend}
            )

        # —— 6. 歧义检测：top1/top2 向量分差过小且来自不同文档 ——
        if len(hydrated) >= 2:
            first, second = hydrated[0], hydrated[1]
            if (
                first.document_id != second.document_id
                and abs(first.score - second.score) < settings.RAG_AMBIGUITY_DELTA
                and first.score > 0
            ):
                trace.ambiguous = True
        return hydrated


# 模块级单例
kb_retriever = KbRetriever()
