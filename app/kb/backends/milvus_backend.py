"""Milvus 向量检索后端（VectorStoreBackend 实现）。

- 使用 pymilvus AsyncMilvusClient（全 async，带超时）；
- 每类数据一个 collection：kb_chunk_v1 / kb_faq_v1（快速建表模式：
  string 主键 + COSINE 向量 + 动态字段承载 tenant_id / document_id）；
- Milvus 只存 id 与向量，命中内容由上层从 PG 水合；
- tenant_id 在 filter 里强制注入，且做白名单转义防表达式注入。
"""

import asyncio
import re

from app.core.config import settings
from app.core.logging import get_logger
from app.kb.types import ChunkIndexItem, FaqIndexItem, Hit

logger = get_logger(__name__)

CHUNK_COLLECTION = "kb_chunk_v1"
FAQ_COLLECTION = "kb_faq_v1"

# tenant_id / document_id 只允许安全字符，防 Milvus filter 表达式注入
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_\-]")


def _safe(value: str) -> str:
    """过滤到安全字符集（我们的 id 均为 UUID / 业务标识，正常不会被改写）。"""
    return _SAFE_ID_RE.sub("", value or "")


class MilvusBackend:
    """Milvus 实现。client 懒加载单例，collection 首次使用时自动创建。"""

    name = "milvus"

    def __init__(self) -> None:
        self._client = None
        self._sync_client = None  # 同步轻量客户端（存在性检查/健康探活复用）
        self._ready_collections: set[str] = set()
        self._init_lock: asyncio.Lock | None = None
        self._init_lock_loop: asyncio.AbstractEventLoop | None = None

    def _lock(self) -> asyncio.Lock:
        """初始化互斥锁（按事件循环懒建，测试切换 loop 时重建）。"""
        loop = asyncio.get_running_loop()
        if self._init_lock is None or self._init_lock_loop is not loop:
            self._init_lock = asyncio.Lock()
            self._init_lock_loop = loop
        return self._init_lock

    async def _get_client(self):
        """懒加载 AsyncMilvusClient（进程内复用，带超时配置）。

        加锁修复并发竞态：进程启动后首批请求并发到达时，无锁的
        check-then-create 会创建多个客户端实例互相覆盖、泄漏连接。
        """
        if self._client is None:
            async with self._lock():
                if self._client is None:
                    from pymilvus import AsyncMilvusClient

                    self._client = AsyncMilvusClient(
                        uri=settings.MILVUS_URI,
                        token=settings.MILVUS_TOKEN or "",
                        timeout=settings.MILVUS_TIMEOUT,
                    )
        return self._client

    def _get_sync_client(self):
        """同步轻量客户端单例（AsyncMilvusClient 未提供 has_collection/list）。

        修复：此前存在性检查与每次 health() 都新建+关闭一个同步客户端
        （readinessProbe 秒级轮询 = 每秒一次全新 TCP 连接）。
        同步网络调用，调用方必须放 asyncio.to_thread 执行。
        """
        if self._sync_client is None:
            from pymilvus import MilvusClient

            self._sync_client = MilvusClient(
                uri=settings.MILVUS_URI, token=settings.MILVUS_TOKEN or ""
            )
        return self._sync_client

    async def _ensure_collection(self, name: str) -> None:
        """collection 不存在则用快速模式创建（string 主键 + COSINE + 动态字段）。

        加锁：并发首访时只允许一个协程做存在性检查与建表；
        同步存在性检查放线程池，不阻塞事件循环（冷启动首请求修复）。
        """
        if name in self._ready_collections:
            return
        # 客户端获取在锁外：_get_client 内部要拿同一把锁（不可重入，锁内调用会死锁）
        client = await self._get_client()
        async with self._lock():
            if name in self._ready_collections:
                return
            exists = await asyncio.to_thread(
                lambda: self._get_sync_client().has_collection(name)
            )
            if not exists:
                await client.create_collection(
                    collection_name=name,
                    dimension=settings.EMBEDDING_DIM,
                    metric_type="COSINE",
                    id_type="string",
                    max_length=64,
                    auto_id=False,
                )
                logger.info("milvus collection created: %s dim=%s", name, settings.EMBEDDING_DIM)
            self._ready_collections.add(name)

    # ------------------------------------------------------------------
    # 写入 / 删除
    # ------------------------------------------------------------------
    async def index_chunks(self, tenant_id: str, items: list[ChunkIndexItem]) -> None:
        if not items:
            return
        await self._ensure_collection(CHUNK_COLLECTION)
        client = await self._get_client()
        data = [
            {
                "id": item.chunk_id,
                "vector": item.embedding,
                "tenant_id": item.tenant_id,
                "document_id": item.document_id,
            }
            for item in items
        ]
        await client.upsert(collection_name=CHUNK_COLLECTION, data=data)

    async def index_faqs(self, tenant_id: str, items: list[FaqIndexItem]) -> None:
        if not items:
            return
        await self._ensure_collection(FAQ_COLLECTION)
        client = await self._get_client()
        data = [
            {"id": item.faq_id, "vector": item.embedding, "tenant_id": item.tenant_id}
            for item in items
        ]
        await client.upsert(collection_name=FAQ_COLLECTION, data=data)

    async def delete_document(self, tenant_id: str, document_id: str) -> None:
        await self._ensure_collection(CHUNK_COLLECTION)
        client = await self._get_client()
        await client.delete(
            collection_name=CHUNK_COLLECTION,
            filter=f'tenant_id == "{_safe(tenant_id)}" && document_id == "{_safe(document_id)}"',
        )

    async def delete_faq(self, tenant_id: str, faq_id: str) -> None:
        await self._ensure_collection(FAQ_COLLECTION)
        client = await self._get_client()
        await client.delete(
            collection_name=FAQ_COLLECTION,
            filter=f'tenant_id == "{_safe(tenant_id)}" && id == "{_safe(faq_id)}"',
        )

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    async def search_chunks(
        self, tenant_id: str, query_vec: list[float], top_k: int
    ) -> list[Hit]:
        return await self._search(CHUNK_COLLECTION, tenant_id, query_vec, top_k, ["document_id"])

    async def search_faqs(
        self, tenant_id: str, query_vec: list[float], top_k: int
    ) -> list[Hit]:
        return await self._search(FAQ_COLLECTION, tenant_id, query_vec, top_k, [])

    async def _search(
        self,
        collection: str,
        tenant_id: str,
        query_vec: list[float],
        top_k: int,
        output_fields: list[str],
    ) -> list[Hit]:
        await self._ensure_collection(collection)
        client = await self._get_client()
        results = await client.search(
            collection_name=collection,
            data=[query_vec],
            filter=f'tenant_id == "{_safe(tenant_id)}"',
            limit=top_k,
            output_fields=output_fields,
            search_params={"metric_type": "COSINE"},
        )
        hits: list[Hit] = []
        for raw in results[0] if results else []:
            entity = raw.get("entity", {}) or {}
            # COSINE 相似度：越大越相关；截断负值，统一 0~1 语义
            score = max(0.0, float(raw.get("distance", 0.0)))
            hits.append(
                Hit(
                    id=str(raw.get("id")),
                    score=round(score, 4),
                    source_backend=self.name,
                    document_id=entity.get("document_id"),
                )
            )
        return hits

    async def health(self) -> bool:
        """健康检查：列 collection 能通即认为可用。

        复用同步客户端单例并放线程池执行——此前每次探活新建+关闭一个客户端
        且同步阻塞事件循环（k8s readinessProbe 按秒轮询时尤其有害）。
        失败后重置单例，下次探活重建连接（自愈）。
        """
        try:
            await asyncio.to_thread(lambda: self._get_sync_client().list_collections())
            return True
        except Exception:  # noqa: BLE001 - 健康检查失败只返回状态
            self._sync_client = None
            return False
