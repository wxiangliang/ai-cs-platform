"""实时通知服务（Stage 15）：WebSocket 枢纽 + Redis Pub/Sub 跨进程广播 + 主动消息。

架构：
- 频道两类：`sess:{tenant}:{session_id}`（用户侧）、`agents:{tenant}`（坐席侧）；
- 发布统一走 Redis Pub/Sub（多 worker 下任意进程发布、所有进程的本地连接都收到）；
  Redis 不可达时回落**本进程直投**（单 worker 部署仍可用，fail-open）；
- 每进程一个 pubsub 监听任务（psubscribe ws:*），按频道分发给本地连接；
- 无离线消息队列：断线即弃，客户端重连后拉历史补齐（需求 2.1 明确不做）。
"""

import asyncio
import contextlib
import json
from typing import Any

from fastapi import WebSocket

from app.core.logging import get_logger

logger = get_logger(__name__)

_CHANNEL_PREFIX = "ws:"


def session_channel(tenant_id: str, session_id: str) -> str:
    """用户侧频道名。"""
    return f"sess:{tenant_id}:{session_id}"


def agents_channel(tenant_id: str) -> str:
    """坐席侧频道名（租户级队列页广播）。"""
    return f"agents:{tenant_id}"


class WsHub:
    """WebSocket 连接枢纽（进程内单例）。"""

    def __init__(self) -> None:
        self._local: dict[str, set[WebSocket]] = {}
        self._listener: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        # after-commit 广播任务的强引用集合（防 GC 提前取消）
        self._pending_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    async def connect(self, channel: str, ws: WebSocket) -> None:
        """注册连接并确保监听任务已启动。"""
        async with self._lock:
            self._local.setdefault(channel, set()).add(ws)
            if self._listener is None or self._listener.done():
                self._listener = asyncio.create_task(self._listen())

    async def disconnect(self, channel: str, ws: WebSocket) -> None:
        """注销连接（断线即弃，无离线队列）。"""
        async with self._lock:
            conns = self._local.get(channel)
            if conns is not None:
                conns.discard(ws)
                if not conns:
                    self._local.pop(channel, None)

    async def shutdown(self) -> None:
        """应用关停：取消监听任务。"""
        if self._listener is not None and not self._listener.done():
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener

    # ------------------------------------------------------------------
    # 发布与分发
    # ------------------------------------------------------------------
    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        """发布事件：Redis Pub/Sub 跨进程广播；Redis 故障回落本进程直投。"""
        payload = json.dumps(event, ensure_ascii=False)
        try:
            from app.cache.redis_client import get_redis_client

            await get_redis_client().publish(_CHANNEL_PREFIX + channel, payload)
        except Exception:  # noqa: BLE001 - 单 worker 部署仍可用
            logger.exception("ws publish via redis failed, local fallback")
            await self._deliver_local(channel, payload)

    def publish_after_commit(
        self, session: Any, channel: str, event: dict[str, Any]
    ) -> None:
        """事务提交成功后才广播（修复：避免事件先于数据可见 / 回滚后发幽灵事件）。

        挂到本请求 DB 会话的 after_commit 事件：坐席/用户收到 ticket_created、
        agent_reply 等事件时，对应行必然已落库；事务回滚则事件不发出。
        无会话上下文时退回即时发布（保持兼容）。
        """
        import sqlalchemy as sa

        sync_session = getattr(session, "sync_session", None)
        if sync_session is None:
            self._schedule(channel, event)
            return
        info = sync_session.info
        pending: list[tuple[str, dict[str, Any]]] = info.setdefault("_ws_pending", [])
        pending.append((channel, event))
        if not info.get("_ws_hooked"):
            info["_ws_hooked"] = True
            sa.event.listen(sync_session, "after_commit", self._drain_after_commit)

    def _drain_after_commit(self, sync_session: Any) -> None:
        """after_commit 同步回调：把本次事务累积的事件调度为后台广播任务。"""
        pending = sync_session.info.get("_ws_pending") or []
        sync_session.info["_ws_pending"] = []
        for channel, event in pending:
            self._schedule(channel, event)

    def _schedule(self, channel: str, event: dict[str, Any]) -> None:
        """调度一次 publish 为后台任务（持强引用防 GC）。"""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.publish(channel, event))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _deliver_local(self, channel: str, payload: str) -> None:
        """把事件投给本进程内订阅该频道的连接；死连接顺手清理。"""
        conns = list(self._local.get(channel) or ())
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 - 死连接：移除不报错
                await self.disconnect(channel, ws)

    async def _listen(self) -> None:
        """进程级 pubsub 监听循环：psubscribe ws:* 分发到本地连接。

        Redis 断连时指数退避重连；任务被取消时干净退出。
        """
        backoff = 1.0
        while True:
            try:
                from app.cache.redis_client import get_redis_client

                pubsub = get_redis_client().pubsub()
                await pubsub.psubscribe(_CHANNEL_PREFIX + "*")
                backoff = 1.0
                async for message in pubsub.listen():
                    if message.get("type") != "pmessage":
                        continue
                    raw_channel = message.get("channel", "")
                    channel = (
                        raw_channel.decode() if isinstance(raw_channel, bytes) else raw_channel
                    ).removeprefix(_CHANNEL_PREFIX)
                    data = message.get("data", "")
                    payload = data.decode() if isinstance(data, bytes) else str(data)
                    await self._deliver_local(channel, payload)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Redis 断连重试
                logger.exception("ws pubsub listener error, retry in %.0fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


# 模块级单例
ws_hub = WsHub()
