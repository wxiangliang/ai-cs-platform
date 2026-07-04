"""mem0 记忆实现（Mem0MemoryProvider，Stage 10）。

接入 https://github.com/mem0ai/mem0 OSS 本地模式：
- 长期记忆：remember → Memory.add（mem0 内部完成 LLM 抽取/去重/冲突合并），
  get_context → Memory.search（向量相关性检索，query=本轮用户消息）；
- 短期记忆仍走本地消息表（复用 LocalMemoryProvider 的窗口与摘要——
  mem0 定位是长期记忆库，会话内短期上下文本地更快更稳）；
- LLM/Embedding 走 OPENAI_* 配置，向量库配 Milvus（与知识库共用实例，独立 collection）；
- mem0 SDK 为同步接口，统一线程池包装；任何故障降级为本地短期记忆，不打断主链路。
"""

import asyncio
from typing import Any

from app.chat.llm.factory import llm_available
from app.chat.memory.base import MemoryContext
from app.chat.memory.local_provider import local_memory_provider
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Mem0MemoryProvider:
    """mem0 实现（长期记忆托管给 mem0，短期复用本地）。"""

    name = "mem0"

    def __init__(self) -> None:
        self._client: Any = None

    def _build_client(self) -> Any:
        """构建 mem0 Memory 客户端（同步，首次调用时在线程池执行）。"""
        from mem0 import Memory

        config = {
            "llm": {
                "provider": "openai",
                "config": {
                    "model": settings.CHAT_MODEL,
                    "api_key": settings.OPENAI_API_KEY,
                    "openai_base_url": settings.OPENAI_BASE_URL,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": settings.EMBEDDING_MODEL,
                    "api_key": settings.OPENAI_API_KEY,
                    "openai_base_url": settings.OPENAI_BASE_URL,
                    "embedding_dims": settings.EMBEDDING_DIM,
                },
            },
            "vector_store": {
                "provider": "milvus",
                "config": {
                    "url": settings.MILVUS_URI,
                    "token": settings.MILVUS_TOKEN or "",
                    "collection_name": "mem0_memories",
                    "embedding_model_dims": settings.EMBEDDING_DIM,
                },
            },
        }
        return Memory.from_config(config)

    async def _get_client(self) -> Any:
        if self._client is None:
            self._client = await asyncio.to_thread(self._build_client)
        return self._client

    @staticmethod
    def _mem_user(tenant_id: str, user_id: str) -> str:
        """mem0 侧用户标识：租户前缀保证隔离。"""
        return f"{tenant_id}:{user_id}"

    async def get_context(
        self, tenant_id: str, user_id: str, session_id: str, query: str
    ) -> MemoryContext:
        # 短期（窗口+摘要）复用本地实现
        context = await local_memory_provider.get_context(tenant_id, user_id, session_id, query)
        # 长期改由 mem0 相关性检索
        try:
            client = await self._get_client()
            results = await asyncio.to_thread(
                client.search,
                query or "用户信息",
                user_id=self._mem_user(tenant_id, user_id),
                limit=settings.MEMORY_LONG_TERM_MAX,
            )
            items = results.get("results", results) if isinstance(results, dict) else results
            context.long_term_facts = [
                str(item.get("memory", "")) for item in items or [] if item.get("memory")
            ]
        except Exception:  # noqa: BLE001 - mem0 故障降级为本地长期结果
            logger.exception("mem0 search failed, keep local long-term facts")
        return context

    async def remember(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_text: str,
        reply: str,
        intent: str | None,
    ) -> None:
        # 本地摘要照常维护（短期依赖它）；长期抽取交给 mem0
        try:
            await local_memory_provider._maybe_summarize(tenant_id, session_id)
        except Exception:  # noqa: BLE001
            logger.exception("mem0 provider: local summarize failed")
        try:
            client = await self._get_client()
            await asyncio.to_thread(
                client.add,
                [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": reply},
                ],
                user_id=self._mem_user(tenant_id, user_id),
            )
        except Exception:  # noqa: BLE001 - best-effort
            logger.exception("mem0 add failed")


def build_mem0_provider() -> Mem0MemoryProvider | None:
    """构建 mem0 提供方；依赖不可用返回 None（factory 降级 local）。"""
    if not llm_available():
        logger.warning("MEMORY_PROVIDER=mem0 但未配置 OPENAI_API_KEY，降级 local")
        return None
    try:
        import mem0  # noqa: F401
    except ImportError:
        logger.warning("MEMORY_PROVIDER=mem0 但 mem0ai 未安装，降级 local")
        return None
    return Mem0MemoryProvider()
