"""Embedding 客户端。

两个实现（EMBEDDING_PROVIDER 切换）：
- openai：OpenAI 兼容接口（OPENAI_BASE_URL 可指向本地推理服务），带超时；
- hash：本地确定性伪向量（jieba 分词 + 特征哈希 + L2 归一）。
  相似措辞会得到相近向量，可离线跑通/测试整条 RAG 链路，
  但没有真实语义能力，**仅限开发与测试，禁止生产使用**。

向量维度统一为 settings.EMBEDDING_DIM，与 Milvus collection 维度绑定，
变更维度必须重建向量索引（uv run python -m app.kb.reindex）。
"""

import hashlib
import math
from typing import Protocol

import jieba

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmbeddingClient(Protocol):
    """Embedding 客户端协议。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化，返回与输入等长的向量列表。"""
        ...


class HashEmbeddingClient:
    """确定性本地伪向量（开发/测试用）。

    做法：jieba 分词 → 每个词特征哈希到固定维度桶（带符号）→ L2 归一化。
    同一文本恒定同一向量；措辞重叠越多余弦相似度越高。
    """

    def __init__(self, dim: int | None = None) -> None:
        self._dim = dim or settings.EMBEDDING_DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        # 词 + 2-gram 字符特征，兼顾中文短语与错别字容忍
        tokens = [t for t in jieba.cut((text or "").lower()) if t.strip()]
        tokens += [text[i : i + 2] for i in range(max(len(text) - 1, 0))]
        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "little") % self._dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class OpenAIEmbeddingClient:
    """OpenAI 兼容 embedding 接口（生产实现）。"""

    def __init__(self) -> None:
        # 延迟导入：未配置 openai provider 时不引入该依赖路径
        from langchain_openai import OpenAIEmbeddings

        self._client = OpenAIEmbeddings(
            model=settings.EMBEDDING_MODEL,
            api_key=settings.OPENAI_API_KEY,  # type: ignore[arg-type]
            base_url=settings.OPENAI_BASE_URL,
            dimensions=settings.EMBEDDING_DIM,
            timeout=settings.EMBEDDING_TIMEOUT,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._client.aembed_documents(texts)


def get_embedding_client() -> EmbeddingClient:
    """按配置返回 embedding 客户端。"""
    if settings.EMBEDDING_PROVIDER == "openai":
        return OpenAIEmbeddingClient()
    if settings.EMBEDDING_PROVIDER != "hash":
        logger.warning(
            "unknown EMBEDDING_PROVIDER=%s, fallback to hash", settings.EMBEDDING_PROVIDER
        )
    return HashEmbeddingClient()


# 模块级单例（按配置构建一次）
embedding_client: EmbeddingClient = get_embedding_client()
