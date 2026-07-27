"""Stage 17 语义缓存（同问直答）。

对**可缓存轮次**（FAQ/RAG 生成、闲聊等无副作用回答）用查询 embedding 找历史
高相似问答，相似度 ≥ 阈值直接返回缓存答案，省一次 LLM/检索调用。

红线：
- 只缓存无副作用的回答；价格/库存/订单等事实（tool/action/product）**永不缓存**
  （可能过期造成资损）——按 answer_source 白名单控制；
- **个性化回答永不缓存**：生成时注入了用户长期记忆事实的回答（entry.personalized）
  可能复述个人信息，缓存是租户级共享的，写入即跨用户泄漏；
- 严格租户隔离（每租户独立 key）+ TTL；
- 降级：缓存后端/embedding 故障一律 **fail-open**，直接走正常链路。

存储（v1，Redis + 现有 embedding）：每租户一个封顶列表，元素为
{emb, reply, source, citations, query}；查询逐条算 cosine 取最优。
真实语义 embedding 换即用（EMBEDDING_PROVIDER 切换），无需改本模块。
"""

from __future__ import annotations

import asyncio
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


def _best_match(raw_entries: list, emb: list[float]) -> tuple[dict[str, Any] | None, float]:
    """在全部缓存条目中找最相似问答（同步 CPU 密集，调用方放线程池执行）。

    延迟修复：原实现是事件循环里的纯 Python 双层循环（200 条 × 512 维 ≈ 10 万次
    浮点乘加，期间所有并发请求被阻塞）。改为 numpy 一次矩阵点积 + to_thread。
    维度不一致的条目跳过（EMBEDDING_DIM 变更后的旧条目自然失效）。
    """
    import numpy as np

    entries: list[dict[str, Any]] = []
    vecs: list[list[float]] = []
    for raw in raw_entries:
        try:
            entry = json.loads(raw)
        except (ValueError, TypeError):
            continue
        vec = entry.get("emb") or []
        if len(vec) != len(emb):
            continue
        entries.append(entry)
        vecs.append(vec)
    if not entries:
        return None, 0.0
    matrix = np.asarray(vecs, dtype=np.float32)
    q = np.asarray(emb, dtype=np.float32)
    q_norm = float(np.linalg.norm(q))
    if q_norm == 0.0:
        return None, 0.0
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0.0] = 1e-9
    scores = (matrix @ q) / (norms * q_norm)
    idx = int(np.argmax(scores))
    return entries[idx], float(scores[idx])


class SemanticCacheProvider(Protocol):
    """语义缓存协议：命中查询、写入、按租户失效。

    emb：调用方已算好的查询向量（embedding 去重，见 rag_answer 节点），
    不传则实现自行计算。
    """

    async def lookup(
        self, tenant_id: str, query: str, emb: list[float] | None = None
    ) -> dict[str, Any] | None: ...

    async def store(
        self, tenant_id: str, query: str, entry: dict[str, Any], emb: list[float] | None = None
    ) -> None: ...

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

    async def lookup(
        self, tenant_id: str, query: str, emb: list[float] | None = None
    ) -> dict[str, Any] | None:
        """查最相似的历史问答，≥ 阈值返回 {reply, source, citations, score}，否则 None。"""
        if not settings.SEMANTIC_CACHE_ENABLED or not query or not tenant_id:
            return None
        try:
            if emb is None:
                emb = await self._embed(query)
            if emb is None:
                return None
            from app.cache.redis_client import get_redis_client

            raw_entries = await get_redis_client().lrange(self._key(tenant_id), 0, -1)
            if not raw_entries:
                count_semantic_cache("miss")
                return None
            # CPU 密集比对放线程池，不阻塞事件循环
            best, best_score = await asyncio.to_thread(_best_match, raw_entries, emb)
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

    async def store(
        self, tenant_id: str, query: str, entry: dict[str, Any], emb: list[float] | None = None
    ) -> None:
        """写入一条可缓存问答（source 不在白名单、或个性化回答则跳过）。故障静默。"""
        if not settings.SEMANTIC_CACHE_ENABLED or not query or not tenant_id:
            return
        # 红线：source 白名单 + 个性化回答（注入过用户记忆）不进租户共享缓存
        if not is_cacheable_source(entry.get("source")) or entry.get("personalized"):
            return
        try:
            if emb is None:
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
            # 三条命令合一次往返（pipeline）；封顶保留最近 N 条并刷新 TTL
            # （缓存新鲜度靠 TTL + publish 失效双保险）
            async with r.pipeline(transaction=False) as pipe:
                pipe.lpush(key, payload)
                pipe.ltrim(key, 0, max(0, settings.SEMANTIC_CACHE_MAX_PER_TENANT - 1))
                pipe.expire(key, settings.SEMANTIC_CACHE_TTL)
                await pipe.execute()
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
