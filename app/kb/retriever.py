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

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import jieba
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.metrics import observe_kb_stage
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
    # 模型输出中实际引用的 chunk id（引用溯源，answerer 解析回填）
    cited: list[str] = field(default_factory=list)

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
            "cited": self.cited,
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
        self,
        session: AsyncSession,
        tenant_id: str,
        query: str,
        trace: RetrievalTrace,
        query_vec: list[float] | None = None,
    ) -> FaqAnswer | None:
        """FAQ 精确层：达到阈值直接返回标准答案，否则 None。

        query_vec：调用方已算好的查询向量（embedding 去重——缓存查询/FAQ/文档层
        共用一次 embedding，见 answerer/rag_answer），不传则自行计算。
        """
        backend = get_vector_backend()
        query = normalize_query(query).text  # 繁简归一，命中简体 FAQ 库
        if query_vec is None:
            t0 = time.perf_counter()
            query_vec = (await embedding_client.embed([query]))[0]
            observe_kb_stage("embed", time.perf_counter() - t0)
        t0 = time.perf_counter()
        hits = await backend.search_faqs(tenant_id, query_vec, top_k=3)
        observe_kb_stage("vector", time.perf_counter() - t0)

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
        self,
        session: AsyncSession,
        tenant_id: str,
        query: str,
        trace: RetrievalTrace,
        query_vec: list[float] | None = None,
    ) -> list[Hit]:
        """文档层检索管道 v2：归一化 → 动态加权多路宽召回 → RRF → rerank →
        水合 + 章节上下文 → 歧义检测。

        返回的 Hit.score 是向量原始余弦分（关键词路无向量分记 0，
        拒答判断只看向量路 top1，见 answerer）；
        Hit.extra["section_context"] 为父子分块聚合的章节全文（生成用）。
        query_vec：调用方已算好的查询向量（embedding 去重），不传则自行计算。
        """
        # —— 1. Query 归一化 ——
        nq = normalize_query(query)
        trace.query_type = nq.query_type
        trace.expanded_terms = nq.expanded_terms
        weights = _WEIGHTS_BY_TYPE[nq.query_type]
        recall_k = effective("RAG_RECALL_TOP_K")

        backend = get_vector_backend()
        if query_vec is None:
            t0 = time.perf_counter()
            query_vec = (await embedding_client.embed([nq.text]))[0]
            observe_kb_stage("embed", time.perf_counter() - t0)

        # —— 2. 多路宽召回（并行）：向量路（Milvus）与关键词路（PG）互不依赖。
        # AsyncSession 非并发安全，但只有关键词路使用它，向量路走 Milvus 客户端，
        # 两路可安全 gather——总耗时从两者之和降为两者最大值
        keywords = _extract_keywords(nq.text) + nq.expanded_terms + nq.model_codes

        async def _vector_recall() -> list[Hit]:
            t0 = time.perf_counter()
            hits = await backend.search_chunks(tenant_id, query_vec, top_k=recall_k)
            observe_kb_stage("vector", time.perf_counter() - t0)
            return hits

        async def _keyword_recall() -> list:
            t0 = time.perf_counter()
            scored = await kb_chunk_repository.search_by_keywords(
                session, tenant_id, list(dict.fromkeys(keywords)), limit=recall_k
            )
            observe_kb_stage("keyword", time.perf_counter() - t0)
            return scored

        vector_hits, keyword_scored = await asyncio.gather(_vector_recall(), _keyword_recall())
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

        # —— 4. 候选水合 + 生效过滤（必须在 rerank/截断之前）——
        # 向量路（Milvus）不带发布状态，未发布/已归档文档的 chunk 会进召回候选；
        # 若截断到 RAG_TOP_K 之后才过滤，死文档会挤占名额且无回填，
        # 极端情况（top 全来自已归档文档）会把有可用内容的查询误判为拒答。
        # chunk 与 document 各批量取一次，后续水合复用不再重查
        chunks = await kb_chunk_repository.get_by_ids(session, tenant_id, [h.id for h in fused])
        chunk_map = {c.id: c for c in chunks}
        doc_ids = list(dict.fromkeys(c.document_id for c in chunks))
        docs = await kb_document_repository.get_by_ids(session, tenant_id, doc_ids)
        # 生效判据（Stage 16）：已发布过（published_version 非空）且未 archived。
        # 编辑已发布文档时 status 会回到 draft 但 published_version 不变→线上仍服务旧版本
        live_titles = {
            d.id: d.title
            for d in docs
            if d.published_version is not None and d.status != "archived"
        }
        candidates: list[Hit] = []
        for h in fused:
            chunk = chunk_map.get(h.id)
            if chunk is None:
                continue  # 后端残留索引（PG 已删），跳过
            if chunk.document_id not in live_titles:
                continue  # 未生效文档不参与精排，不挤占 top_k
            h.content = chunk.content
            candidates.append(h)

        # —— 5. Rerank 精排（off 时为 RRF 序截断）→ 水合标题 + 章节上下文 ——
        t0 = time.perf_counter()
        top = await rerank_hits(nq.text, candidates, settings.RAG_TOP_K)
        observe_kb_stage("rerank", time.perf_counter() - t0)
        # 记录实际行为而非配置：rerank_score 只在真正执行重排时写入——
        # 配置=local 但模型加载失败/推理异常会静默降级为截断，此前 trace 仍记
        # True，会把「重排从未生效」误读成「重排无收益」（A/B 评估失真）
        trace.reranked = any(h.extra.get("rerank_score") is not None for h in top)

        # 章节上下文批量预取（去 N+1）：top 命中的 (doc, section) 一次查询取回
        section_keys: list[tuple[str, str]] = []
        for h in top:
            hit_chunk = chunk_map[h.id]
            if hit_chunk.section_path:
                sec_key = (hit_chunk.document_id, hit_chunk.section_path)
                if sec_key not in section_keys:
                    section_keys.append(sec_key)
        t0 = time.perf_counter()
        sections = await kb_chunk_repository.list_sections(session, tenant_id, section_keys)
        if section_keys:
            observe_kb_stage("sections", time.perf_counter() - t0)

        hydrated: list[Hit] = []
        for hit in top:
            chunk = chunk_map[hit.id]
            doc_id = chunk.document_id
            hit.title = live_titles[doc_id]
            hit.document_id = doc_id
            hit.score = vector_score_map.get(hit.id, 0.0)
            # 标记关键词路来源：纯关键词命中的向量分为 0（未进向量召回），
            # 拒答判断/摘录选块不能只看向量分把它们误当无关（v2 精确查询关键词提权本意）
            hit.extra["from_keyword"] = hit.id in keyword_ids
            # 父子分块：命中子块 → 聚合同章节兄弟块作为生成上下文（字符上限截断）。
            # 行级去重：表格块自带引导句（=章节段落前缀）、各块都有标题路径前缀，
            # 直接拼接会重复，按行去重后再拼
            if chunk.section_path:
                siblings = sections.get((doc_id, chunk.section_path), [])
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

        # —— 6. 歧义检测：向量分 top1/top2 分差过小且来自不同文档 ——
        # 判据与分数同源（观测失真修复）：最终排序可能来自 rerank/RRF 关键词加权
        # （纠正向量序正是它们的本职），「按最终名次取前二、再比向量分」是两个
        # 体系混用——重排后前二的向量分差大不代表无歧义。歧义的本质是语义层面
        # 存在两份接近的异文档候选，故在生效候选集上按向量分取前二判定
        vec_ranked = sorted(
            (h for h in candidates if vector_score_map.get(h.id, 0.0) > 0),
            key=lambda h: vector_score_map[h.id],
            reverse=True,
        )[:2]
        if len(vec_ranked) == 2:
            doc_a = chunk_map[vec_ranked[0].id].document_id
            doc_b = chunk_map[vec_ranked[1].id].document_id
            score_a = vector_score_map[vec_ranked[0].id]
            score_b = vector_score_map[vec_ranked[1].id]
            if doc_a != doc_b and score_a - score_b < settings.RAG_AMBIGUITY_DELTA:
                trace.ambiguous = True
        return hydrated


# 模块级单例
kb_retriever = KbRetriever()
