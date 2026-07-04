"""记忆提供方工厂：按 MEMORY_PROVIDER 配置返回实现（进程内单例）。"""

from functools import lru_cache

from app.chat.memory.base import MemoryProvider
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_memory_provider() -> MemoryProvider:
    """local=自研；mem0=接入 mem0（不可用自动降级 local）。off 由调用方拦截。"""
    provider = settings.MEMORY_PROVIDER.lower()
    if provider == "mem0":
        from app.chat.memory.mem0_provider import build_mem0_provider

        mem0_provider = build_mem0_provider()
        if mem0_provider is not None:
            return mem0_provider
        # 依赖缺失/无 Key → 降级 local（已在 build 中告警）
    elif provider != "local":
        logger.warning("unknown MEMORY_PROVIDER=%s, fallback to local", settings.MEMORY_PROVIDER)

    from app.chat.memory.local_provider import local_memory_provider

    return local_memory_provider
