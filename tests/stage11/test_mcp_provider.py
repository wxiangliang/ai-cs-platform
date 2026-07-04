"""McpToolProvider 单元测试（fake 发现/调用，不依赖真实 MCP 服务）。"""

from unittest.mock import AsyncMock

from app.chat.tools.mcp_provider import McpToolProvider
from app.chat.tools import mock_data
from app.core.config import settings


async def test_mcp_covered_tool_goes_protocol(monkeypatch):
    p = McpToolProvider()
    monkeypatch.setattr(p, "_list_tools_once", AsyncMock(return_value={"query_order"}))
    monkeypatch.setattr(
        p, "_call_tool_once", AsyncMock(return_value={"order_id": "A1", "status": "待发货"})
    )
    r = await p.invoke("query_order", {"order_id": "A1"}, tenant_id="t1")
    assert r.ok and r.data["status"] == "待发货"
    p._call_tool_once.assert_awaited_once()


async def test_uncovered_tool_falls_to_mock(monkeypatch):
    """服务未覆盖的工具（写操作）→ 进程内 mock，无降级标记。"""
    p = McpToolProvider()
    monkeypatch.setattr(p, "_list_tools_once", AsyncMock(return_value={"query_order"}))
    r = await p.invoke("create_refund_ticket", {"order_id": "A1"}, tenant_id="t1")
    expect = mock_data.ticket_data("t1", "create_refund_ticket", "A1")
    assert r.ok and r.data["ticket_no"] == expect["ticket_no"]
    assert "degraded" not in r.data


async def test_call_failure_fails_honestly_by_default(monkeypatch):
    """默认 fail 策略（Stage 13）：MCP 调用失败如实报上游不可用，绝不 mock 冒充事实。"""
    p = McpToolProvider()
    monkeypatch.setattr(p, "_list_tools_once", AsyncMock(return_value={"query_order"}))
    monkeypatch.setattr(p, "_call_tool_once", AsyncMock(side_effect=RuntimeError("boom")))
    r = await p.invoke("query_order", {"order_id": "A1"}, tenant_id="t1")
    assert not r.ok and r.error_code == "UPSTREAM_UNAVAILABLE"
    assert not r.data  # 无任何编造数据


async def test_call_failure_falls_to_mock_when_opted_in(monkeypatch):
    """显式 TOOL_MCP_FALLBACK=mock（开发联调）：回落 mock + degraded 标记（旧行为）。"""
    monkeypatch.setattr(settings, "TOOL_MCP_FALLBACK", "mock")
    p = McpToolProvider()
    monkeypatch.setattr(p, "_list_tools_once", AsyncMock(return_value={"query_order"}))
    monkeypatch.setattr(p, "_call_tool_once", AsyncMock(side_effect=RuntimeError("boom")))
    r = await p.invoke("query_order", {"order_id": "A1"}, tenant_id="t1")
    assert r.ok and r.data["degraded"] == "mcp_fallback_mock"
    assert r.data["status"] in ("待发货", "已发货")  # mock 数据兜底


async def test_unreachable_known_tool_fails_honestly(monkeypatch):
    """服务不可达且工具曾被发现过：fail 策略下如实报错（不装能查）。"""
    p = McpToolProvider()
    p._REDISCOVER_SECONDS = 0
    calls = AsyncMock(side_effect=[{"query_order"}, RuntimeError("down")])
    monkeypatch.setattr(p, "_list_tools_once", calls)
    monkeypatch.setattr(p, "_call_tool_once", AsyncMock(return_value={"status": "已发货"}))
    r1 = await p.invoke("query_order", {}, tenant_id="t1")  # 首次发现成功
    assert r1.ok and r1.data["status"] == "已发货"
    p._discovery_failed = True  # 模拟服务转为不可达（触发重发现失败）
    p._mcp_tools = None
    r2 = await p.invoke("query_order", {}, tenant_id="t1")
    assert not r2.ok and r2.error_code == "UPSTREAM_UNAVAILABLE"


async def test_discovery_failure_cold_start_keeps_mock_with_flag(monkeypatch):
    """冷启动即不可达（无历史发现集）：保留 mock 兜底 + degraded 标记（可观测）。"""
    p = McpToolProvider()
    monkeypatch.setattr(p, "_list_tools_once", AsyncMock(side_effect=RuntimeError("down")))
    r = await p.invoke("query_order", {"order_id": "A1"}, tenant_id="t1")
    assert r.ok and r.data["degraded"] == "mcp_unreachable_fallback_mock"


async def test_write_tool_always_mock_never_upstream_error(monkeypatch):
    """写操作从未被 MCP 覆盖：服务不可达也照走 mock（其正常归属），不受 fail 策略影响。"""
    p = McpToolProvider()
    p._REDISCOVER_SECONDS = 0
    calls = AsyncMock(side_effect=[{"query_order"}, RuntimeError("down")])
    monkeypatch.setattr(p, "_list_tools_once", calls)
    monkeypatch.setattr(p, "_call_tool_once", AsyncMock(return_value={"status": "已发货"}))
    await p.invoke("query_order", {}, tenant_id="t1")
    p._discovery_failed = True
    p._mcp_tools = None
    r = await p.invoke("create_refund_ticket", {"order_id": "A1"}, tenant_id="t1")
    assert r.ok and r.data.get("ticket_no")


async def test_discovery_retry_after_ttl(monkeypatch):
    """发现失败缓存带 TTL：服务恢复后自动重新接管。"""
    monkeypatch.setattr(settings, "TOOL_MCP_FALLBACK", "mock")
    p = McpToolProvider()
    p._REDISCOVER_SECONDS = 0  # 立即允许重试
    calls = AsyncMock(side_effect=[RuntimeError("down"), {"query_order"}])
    monkeypatch.setattr(p, "_list_tools_once", calls)
    monkeypatch.setattr(p, "_call_tool_once", AsyncMock(return_value={"status": "已发货"}))
    r1 = await p.invoke("query_order", {}, tenant_id="t1")
    assert r1.data.get("degraded") == "mcp_unreachable_fallback_mock"
    r2 = await p.invoke("query_order", {}, tenant_id="t1")  # 重发现成功 → 走 MCP
    assert r2.data.get("status") == "已发货" and "degraded" not in r2.data
