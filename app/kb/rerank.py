"""重排器（Stage 06-04）：多路召回 → RRF 粗排 → CrossEncoder 精排。

没有 rerank 的 RAG 常见问题是「召回到了但顺序不对」——RRF 只看名次，
CrossEncoder 逐对打分（query, chunk）精度高一个量级。

RERANKER_PROVIDER=off（默认）：RRF 序即终序（离线/低配环境）；
=local：本地 CrossEncoder（RERANK_MODEL，默认 BGE reranker，首次运行下载模型，
CPU 推理放线程池）。加载/推理失败自动降级为不重排，绝不打断检索。
"""

import asyncio
import threading
from typing import Any

from app.core.config import settings
from app.experiments.resolver import effective
from app.core.logging import get_logger
from app.kb.types import Hit

logger = get_logger(__name__)


class LocalCrossEncoderReranker:
    """本地 CrossEncoder 重排（懒加载单例，线程安全）。"""

    name = "local"

    def __init__(self) -> None:
        self._model: Any = None
        self._unavailable = False
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._unavailable:
            return False
        with self._lock:
            if self._model is not None:
                return True
            if self._unavailable:
                return False
            try:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(settings.RERANK_MODEL)
                logger.info("reranker loaded: %s", settings.RERANK_MODEL)
                return True
            except Exception:  # noqa: BLE001 - 加载失败降级不重排
                logger.exception("reranker load failed, rerank disabled")
                self._unavailable = True
                return False

    def _rerank_sync(self, query: str, hits: list[Hit], top_k: int) -> list[Hit]:
        pairs = [(query, (h.content or "")[:512]) for h in hits]
        scores = self._model.predict(pairs)
        ranked = sorted(zip(hits, scores), key=lambda x: float(x[1]), reverse=True)
        result = []
        for hit, score in ranked[:top_k]:
            hit.extra["rerank_score"] = round(float(score), 4)
            result.append(hit)
        return result

    async def rerank(self, query: str, hits: list[Hit], top_k: int) -> list[Hit]:
        """精排；不可用时原序截断。CPU 密集，线程池执行。"""
        if not hits:
            return hits
        loaded = await asyncio.to_thread(self._ensure_loaded)
        if not loaded:
            return hits[:top_k]
        try:
            return await asyncio.to_thread(self._rerank_sync, query, hits, top_k)
        except Exception:  # noqa: BLE001 - 推理失败降级原序
            logger.exception("rerank failed, keep RRF order")
            return hits[:top_k]


# 模块级单例
_local_reranker = LocalCrossEncoderReranker()


async def rerank_hits(query: str, hits: list[Hit], top_k: int) -> list[Hit]:
    """按配置重排：off → RRF 序截断；local → CrossEncoder 精排（失败降级）。"""
    # A/B 实验（Stage 18）：经 effective 读，实验可覆盖 RERANKER_PROVIDER（否则回落 settings）
    if str(effective("RERANKER_PROVIDER")).lower() != "local":
        return hits[:top_k]
    return await _local_reranker.rerank(query, hits, top_k)
