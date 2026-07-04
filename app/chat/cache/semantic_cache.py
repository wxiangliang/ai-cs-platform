"""Stage 17 语义缓存（同问直答）。

对**可缓存轮次**（FAQ/RAG 生成、闲聊等无副作用回答）用查询 embedding 找历史
高相似问答，相似度 ≥ 阈值直接返回缓存答案，省一次 LLM/检索调用。

红线：
- 只缓存无副作用的回答；价格/库存/订单等事实（tool/action/product）**永不缓存**
  （可能过期造成资损）——按 answer_source 白名单控制；
- 严格租户隔离（每租户独立 key）+ TTL；
- 降级：缓存后端/embedding 故障一律 **fail-open**，直接走正常链路。

存储（v1，Redis + 现有 embedding）：每租户一个封顶列表，元素为
{emb, reply, source, citations, query}；查询逐条算 cosine 取最优。
真实语义 embedding 换即用（EMBEDDING_PROVIDER 切换），无需改本模块。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import count_semantic_cache

logger = get_logger(__name__)

# 可缓存的回答来源白名单：无副作用的知识/闲聊类；
# 事实类（tool/action/product/refused/degraded）一律不缓存
_CACHEABLE_SOURCES = frozenset({"faq", "rag_llm", "rag_extract", "chitchat"})


def is_cacheable_source(source: str | None) -> bool:
    """回答来源是否允许进语义缓存（白名单）。"""
    return bool(source) and source in _CACHEABLE_SOURCES


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；维度不一致或零向量返回 0。"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticCacheProvider(Protocol):
    """语义缓存协议：命中查询、写入、按租户失效。"""

    async def lookup(self, tenant_id: str, query: str) -> dict[str, Any] | None: ...

    async def store(self, tenant_id: str, query: str, entry: dict[str, Any]) -> None: ...

    async def invalidate(self, tenant_id: str) -> None: ...


class RedisSemanticCache:
    """Redis 实现：每租户封顶列表 + Python 端 cosine 比对。"""

    @staticmethod
    def _key(tenant_id: str) -> str:
        return f"semcache:{tenant_id}"

    @staticmethod
    async def _embed(text: str) -> list[float] | None:
        """取查询向量（复用知识库 embedding client）；故障返回 None。"""
        try:
            from app.kb.embedding import embedding_client

            vecs = await embedding_client.embed([text])
            return [float(x) for x in vecs[0]] if vecs else None
        except Exception:  # noqa: BLE001 - embedding 故障 fail-open
            logger.warning("semantic cache embed failed", exc_info=True)
            return None

    async def lookup(self, tenant_id: str, query: str) -> dict[str, Any] | None:
        """查最相似的历史问答，≥ 阈值返回 {reply, source, citations, score}，否则 None。"""
        if not settings.SEMANTIC_CACHE_ENABLED or not query or not tenant_id:
            return None
        try:
            emb = await self._embed(query)
            if emb is None:
                return None
            from app.cache.redis_client import get_redis_client

            raw_entries = await get_redis_client().lrange(self._key(tenant_id), 0, -1)
            best_score = 0.0
            best: dict[str, Any] | None = None
            for raw in raw_entries:
                try:
                    entry = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                score = _cosine(emb, entry.get("emb") or [])
                if score > best_score:
                    best_score = score
                    best = entry
            if best is not None and best_score >= settings.SEMANTIC_CACHE_THRESHOLD:
                count_semantic_cache("hit")
                return {
                    "reply": best.get("reply", ""),
                    "source": best.get("source", ""),
                    "citations": best.get("citations") or [],
                    "score": best_score,
                }
            count_semantic_cache("miss")
            return None
        except Exception:  # noqa: BLE001 - 缓存后端故障 fail-open，走正常链路
            logger.warning("semantic cache lookup failed, fail-open", exc_info=True)
            return None

    async def store(self, tenant_id: str, query: str, entry: dict[str, Any]) -> None:
        """写入一条可缓存问答（source 不在白名单则跳过）。故障静默。"""
        if not settings.SEMANTIC_CACHE_ENABLED or not query or not tenant_id:
            return
        if not is_cacheable_source(entry.get("source")):
            return
        try:
            emb = await self._embed(query)
            if emb is None:
                return
            from app.cache.redis_client import get_redis_client

            payload = json.dumps(
                {
                    "emb": emb,
                    "reply": entry.get("reply", ""),
                    "source": entry.get("source", ""),
                    "citations": entry.get("citations") or [],
                    "query": query,
                },
                ensure_ascii=False,
            )
            r = get_redis_client()
            key = self._key(tenant_id)
            await r.lpush(key, payload)
            # 封顶保留最近 N 条，并刷新 TTL（缓存新鲜度靠 TTL + publish 失效双保险）
            await r.ltrim(key, 0, max(0, settings.SEMANTIC_CACHE_MAX_PER_TENANT - 1))
            await r.expire(key, settings.SEMANTIC_CACHE_TTL)
            count_semantic_cache("store")
        except Exception:  # noqa: BLE001 - 写缓存失败不影响回答
            logger.warning("semantic cache store failed", exc_info=True)

    async def invalidate(self, tenant_id: str) -> None:
        """清空某租户全部缓存（知识库 publish 后调用，避免答旧内容）。故障静默。"""
        if not tenant_id:
            return
        try:
            from app.cache.redis_client import get_redis_client

            await get_redis_client().delete(self._key(tenant_id))
        except Exception:  # noqa: BLE001 - 失效失败靠 TTL 兜底
            logger.warning("semantic cache invalidate failed", exc_info=True)


@lru_cache(maxsize=1)
def get_semantic_cache() -> SemanticCacheProvider:
    """语义缓存单例（进程内复用）。"""
    return RedisSemanticCache()
