"""知识库公共数据结构。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChunkIndexItem:
    """写入向量后端的分块索引项。"""

    chunk_id: str
    tenant_id: str
    document_id: str
    embedding: list[float]


@dataclass
class FaqIndexItem:
    """写入向量后端的 FAQ 索引项。"""

    faq_id: str
    tenant_id: str
    embedding: list[float]


@dataclass
class Hit:
    """检索命中项（后端无关的统一结构）。

    score 为余弦相似度（越大越相关，实践中 0~1），
    各后端必须在内部完成归一，上层阈值判断才可跨后端互换。
    """

    id: str  # chunk_id 或 faq_id
    score: float
    source_backend: str
    document_id: str | None = None
    # 以下字段由 PG 侧水合（后端只存 id + 向量，内容以 PG 为准）
    title: str | None = None
    content: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
