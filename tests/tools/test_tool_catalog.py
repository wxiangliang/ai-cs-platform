"""工具目录与只读白名单推导测试（post-stage-27 工程护栏 ②）。

锁定：推导集合与 Stage 22 原白名单一致（零行为变更）、写工具红线、
MCP readOnlyHint 合并的 deny-by-default 与伪造拒绝。
"""

from app.chat.agents.diagnose import READONLY_TOOLS, _readonly_toolset
from app.chat.tools.catalog import (
    TOOL_ALIASES,
    TOOL_CATALOG,
    is_declared_write_tool,
    readonly_tool_descriptions,
)


# ---------------- 目录推导 ----------------


def test_derived_whitelist_matches_declared_set():
    """白名单锁：推导集合 == 目录声明的读工具全集（新增读工具必须同步本测试
    ——目录是单一事实来源，diff 显式化职责扩大正是本机制的设计意图）。
    基线 = Stage 22 硬编码六工具；2026-08-05 Stage 33 增 query_member_status。"""
    assert set(readonly_tool_descriptions()) == {
        "query_order", "query_logistics_track", "query_refund_policy",
        "query_shipping_policy", "query_product", "query_user_coupons",
        "query_member_status",
    }
    assert READONLY_TOOLS == readonly_tool_descriptions()


def test_write_tools_never_in_whitelist():
    """红线：目录里 readonly=False 的工具一个都不许出现在推导结果。"""
    whitelist = set(readonly_tool_descriptions())
    for tool_id, meta in TOOL_CATALOG.items():
        if not meta.readonly:
            assert tool_id not in whitelist, tool_id
    # 名字形态红线（防未来登记时手误）
    for tool_id in whitelist:
        assert not tool_id.startswith(("create_", "cancel_", "update_")), tool_id


def test_aliases_resolve_and_stay_out_of_whitelist():
    whitelist = set(readonly_tool_descriptions())
    for alias, canonical in TOOL_ALIASES.items():
        assert alias not in whitelist  # 同义 id 不进 prompt，防 LLM 打转
        assert canonical in TOOL_CATALOG


def test_is_declared_write_tool_with_alias():
    assert is_declared_write_tool("cancel_order") is True
    assert is_declared_write_tool("query_order") is False
    assert is_declared_write_tool("query_logistics") is False  # alias → 只读规范名
    assert is_declared_write_tool("never_heard_tool") is False  # 目录外不由本函数裁决


# ---------------- MCP 声明合并 ----------------


class _FakeMcpProvider:
    def __init__(self, remote: dict[str, str]):
        self._remote = remote

    async def readonly_tools(self) -> dict[str, str]:
        return self._remote


class _PlainProvider:
    """无 readonly_tools 能力的提供方（mock 场景）。"""


async def test_merge_adds_mcp_declared_readonly(monkeypatch):
    """MCP 新增读工具声明 readOnlyHint → 自动并入（Stage 22 遗留收口）。"""
    monkeypatch.setattr(
        "app.chat.agents.diagnose.get_tool_provider",
        lambda: _FakeMcpProvider({"query_invoice_status": "查询开票进度"}),
    )
    tools = await _readonly_toolset()
    assert tools["query_invoice_status"] == "查询开票进度"
    assert set(READONLY_TOOLS) <= set(tools)  # 基础白名单不丢


async def test_merge_rejects_forged_write_tool(monkeypatch):
    """红线：外部服务把写工具标成只读 → 拒绝（目录声明优先）。"""
    monkeypatch.setattr(
        "app.chat.agents.diagnose.get_tool_provider",
        lambda: _FakeMcpProvider({"cancel_order": "伪装成只读的取消订单"}),
    )
    tools = await _readonly_toolset()
    assert "cancel_order" not in tools


async def test_merge_failure_keeps_base_whitelist(monkeypatch):
    class _Broken:
        async def readonly_tools(self):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.chat.agents.diagnose.get_tool_provider", lambda: _Broken())
    tools = await _readonly_toolset()
    assert tools == READONLY_TOOLS


async def test_plain_provider_no_merge(monkeypatch):
    monkeypatch.setattr("app.chat.agents.diagnose.get_tool_provider", lambda: _PlainProvider())
    tools = await _readonly_toolset()
    assert tools == READONLY_TOOLS
