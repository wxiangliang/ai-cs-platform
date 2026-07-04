"""向量后端工厂：按 KB_BACKEND 配置构建后端实例（进程内单例）。"""

from functools import lru_cache

from app.core.config import settings
from app.core.logging import get_logger
from app.kb.backends.base import VectorStoreBackend

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_vector_backend() -> VectorStoreBackend:
    """返回当前配置的向量后端。

    当前实现 milvus；pgvector / elasticsearch 为规划中的可选后端
    （见 stage-06 需求文档），新增后端时在此登记。
    """
    backend = settings.KB_BACKEND.lower()
    if backend == "milvus":
        from app.kb.backends.milvus_backend import MilvusBackend

        return MilvusBackend()
    raise ValueError(
        f"不支持的 KB_BACKEND={settings.KB_BACKEND}，当前可选：milvus"
    )
