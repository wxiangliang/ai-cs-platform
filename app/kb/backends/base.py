"""向量检索后端协议与公共融合函数。

设计约定（见 docs/requirements/stage-06-rag-faq/）：
- PostgreSQL 是知识库唯一事实来源；后端只存「id + 租户/文档标识 + 向量」，
  命中内容由上层从 PG 水合，后端可随时用 reindex 命令全量重建；
- tenant_id 是所有检索方法的必传参数，实现内部强制过滤，禁止调用方可选；
- score 统一为余弦相似度语义（越大越相关），跨后端阈值判断才可互换。
"""

from typing import Protocol

from app.kb.types import ChunkIndexItem, FaqIndexItem, Hit


class VectorStoreBackend(Protocol):
    """向量检索后端协议。当前实现：Milvus；预留 pgvector / elasticsearch。"""

    name: str

    async def index_chunks(self, tenant_id: str, items: list[ChunkIndexItem]) -> None:
        """写入/更新分块向量。"""
        ...

    async def index_faqs(self, tenant_id: str, items: list[FaqIndexItem]) -> None:
        """写入/更新 FAQ 问题向量。"""
        ...

    async def delete_document(self, tenant_id: str, document_id: str) -> None:
        """删除某文档的全部分块向量。"""
        ...

    async def delete_faq(self, tenant_id: str, faq_id: str) -> None:
        """删除单条 FAQ 向量。"""
        ...

    async def search_chunks(
        self, tenant_id: str, query_vec: list[float], top_k: int
    ) -> list[Hit]:
        """分块向量检索。"""
        ...

    async def search_faqs(
        self, tenant_id: str, query_vec: list[float], top_k: int
    ) -> list[Hit]:
        """FAQ 问题向量检索。"""
        ...

    async def health(self) -> bool:
        """后端健康检查（接入 /api/health/ready）。"""
        ...


def rrf_fuse(
    result_lists: list[list[Hit]],
    k: int = 60,
    top_k: int = 5,
    weights: list[float] | None = None,
) -> list[Hit]:
    """Reciprocal Rank Fusion：融合多路召回结果（支持按路加权，v2）。

    RRF 只看名次不看原始分值，天然规避「向量分与关键词分不可比」的问题；
    weights 按 result_lists 顺序对各路贡献加权（精确查询关键词路权重大、
    语义查询向量路权重大——见 retriever 的动态权重策略）。
    融合后 Hit.extra["rrf_score"] 为归一值（仅用于排序展示；
    拒答阈值判断必须用融合前的向量原始相似度，调用方自行保留）。
    """
    if weights is None:
        weights = [1.0] * len(result_lists)
    scores: dict[str, float] = {}
    hit_map: dict[str, Hit] = {}
    for hits, weight in zip(result_lists, weights):
        for rank, hit in enumerate(hits):
            scores[hit.id] = scores.get(hit.id, 0.0) + weight / (k + rank + 1)
            # 保留信息更全的那份命中（优先带 content 的）
            if hit.id not in hit_map or (hit.content and not hit_map[hit.id].content):
                hit_map[hit.id] = hit

    if not scores:
        return []
    max_score = max(scores.values())
    fused = []
    for hit_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]:
        hit = hit_map[hit_id]
        hit.extra["rrf_score"] = round(score / max_score, 4)
        fused.append(hit)
    return fused
