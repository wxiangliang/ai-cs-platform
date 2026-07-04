"""MCP 工具提供方（Stage 11）：通过 MCP 协议调用外部业务工具服务。

组合式设计：
- MCP 服务声明的工具（首次使用 list_tools 发现并缓存）→ 走 MCP 调用；
- 服务未覆盖的工具（写操作等）→ 回落进程内 mock（对接真实系统后逐步迁移）；
- MCP 调用失败/超时 → 按 `TOOL_MCP_FALLBACK` 处理（Stage 13 整改）：
  - fail（默认，生产语义）：返回 ok=False/UPSTREAM_UNAVAILABLE——
    **绝不用 mock 编造订单事实回给用户**，tool_invoke 走既有失败分支
    （RAG 兜底/「暂时查询不到」话术 + SKILL_RULE 建单）；
  - mock（开发联调）：回落 mock 并打 degraded 标记（旧行为）。
  服务不可达（发现失败）时，曾经发现过的工具按同一策略处理；
  从未发现过的工具（写操作）始终走 mock——mock 写操作本就是它的归属。

每次调用独立建立 streamable-http 会话（无状态、进程重启零影响）；
本地调用开销毫秒级，暂不做连接复用（真实高并发场景再优化）。
"""

import asyncio
import json
import time
from typing import Any

from app.chat.tools.base import ToolResult
from app.chat.tools.mock_provider import mock_tool_provider
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class McpToolProvider:
    """MCP 客户端实现（MCP 覆盖工具走协议调用，其余回落 mock）。"""

    name = "mcp"

    # 发现失败后的重试间隔（秒）：服务恢复后能自动重新接管，不永久降级
    _REDISCOVER_SECONDS = 60

    def __init__(self) -> None:
        self._mcp_tools: set[str] | None = None  # 服务端声明的工具名（懒发现）
        # 最后一次成功发现的工具集：服务临时不可达时据此区分
        # 「MCP 覆盖的读工具」（按 fallback 策略处理）与「从未覆盖的写工具」（走 mock）
        self._last_known_tools: set[str] = set()
        self._discovery_failed = False
        self._discovered_at = 0.0
        self._lock = asyncio.Lock()

    async def _discover_tools(self) -> set[str]:
        """发现 MCP 服务的工具清单。

        失败返回空集（全部回落 mock）并标记 _discovery_failed；
        失败缓存带 TTL——服务恢复后 60 秒内自动重新接管。
        """
        if self._mcp_tools is not None and not (
            self._discovery_failed
            and time.monotonic() - self._discovered_at > self._REDISCOVER_SECONDS
        ):
            return self._mcp_tools
        async with self._lock:
            if self._mcp_tools is not None and not (
                self._discovery_failed
                and time.monotonic() - self._discovered_at > self._REDISCOVER_SECONDS
            ):
                return self._mcp_tools
            try:
                tools = await asyncio.wait_for(
                    self._list_tools_once(), timeout=settings.MCP_TIMEOUT
                )
                self._mcp_tools = tools
                self._last_known_tools = set(tools)
                self._discovery_failed = False
                logger.info("mcp tools discovered: %s", sorted(tools))
            except Exception:  # noqa: BLE001 - 服务不可达 → 全部回落 mock
                logger.exception("mcp tool discovery failed, all tools fallback to mock")
                self._mcp_tools = set()
                self._discovery_failed = True
            self._discovered_at = time.monotonic()
            return self._mcp_tools

    @staticmethod
    async def _list_tools_once() -> set[str]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(settings.MCP_SERVER_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return {t.name for t in result.tools}

    @staticmethod
    async def _call_tool_once(tool_id: str, params: dict[str, Any]) -> dict[str, Any]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(settings.MCP_SERVER_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_id, params)
                if result.isError:
                    raise RuntimeError(f"mcp tool error: {tool_id}")
                # 优先结构化输出；否则解析首个文本块的 JSON
                if result.structuredContent:
                    data = result.structuredContent
                    # FastMCP 把 dict 返回值包在 {"result": ...} 里
                    return data.get("result", data) if isinstance(data, dict) else {}
                for block in result.content:
                    text = getattr(block, "text", None)
                    if text:
                        return json.loads(text)
                return {}

    async def _degraded_result(
        self, tool_id: str, params: dict[str, Any], tenant_id: str, flag: str, start: float
    ) -> ToolResult:
        """MCP 故障时的降级结果（Stage 13 整改：默认 fail，禁 mock 冒充事实）。"""
        if settings.TOOL_MCP_FALLBACK == "mock":
            # 开发联调显式选择：mock 兜底 + degraded 标记（旧行为）
            result = await mock_tool_provider.invoke(tool_id, params, tenant_id=tenant_id)
            result.data["degraded"] = flag
            return result
        # 生产语义：如实报「上游不可用」，绝不编造订单/物流事实。
        # tool_invoke 对全失败已有处理：RAG 兜底/「帮您核实」话术 + SKILL_RULE 建单
        return ToolResult(
            ok=False,
            error_code="UPSTREAM_UNAVAILABLE",
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )

    async def invoke(
        self, tool_id: str, params: dict[str, Any], *, tenant_id: str
    ) -> ToolResult:
        start = time.perf_counter()
        mcp_tools = await self._discover_tools()
        if tool_id not in mcp_tools:
            # 服务不可达期间，「曾经发现过的工具」按降级策略处理（读工具不装能查）；
            # 从未被服务覆盖的工具（写操作）→ 进程内 mock（其正常归属）
            if self._discovery_failed and tool_id in self._last_known_tools:
                return await self._degraded_result(
                    tool_id, params, tenant_id, "mcp_unreachable_fallback_mock", start
                )
            result = await mock_tool_provider.invoke(tool_id, params, tenant_id=tenant_id)
            if self._discovery_failed:
                # 冷启动即不可达（无历史发现集）：无法区分覆盖面，保留 mock 兜底但打标记
                result.data["degraded"] = "mcp_unreachable_fallback_mock"
            return result

        call_params = {**params, "tenant_id": tenant_id}
        # MCP 工具入参只传声明字段（order_id/tenant_id），过滤多余槽位
        call_params = {
            k: str(v) for k, v in call_params.items() if k in ("order_id", "tenant_id")
        }
        try:
            data = await asyncio.wait_for(
                self._call_tool_once(tool_id, call_params), timeout=settings.MCP_TIMEOUT
            )
            return ToolResult(
                ok=True, data=data,
                latency_ms=round((time.perf_counter() - start) * 1000, 2),
            )
        except Exception:  # noqa: BLE001 - MCP 故障按 TOOL_MCP_FALLBACK 降级，主链路不断
            logger.exception("mcp call failed (tool=%s), fallback=%s",
                             tool_id, settings.TOOL_MCP_FALLBACK)
            return await self._degraded_result(
                tool_id, params, tenant_id, "mcp_fallback_mock", start
            )


# 模块级单例
mcp_tool_provider = McpToolProvider()
