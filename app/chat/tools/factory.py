"""工具提供方工厂：按 TOOL_PROVIDER 配置返回实现。"""

from functools import lru_cache

from app.chat.tools.base import ToolProvider
from app.core.config import settings


@lru_cache(maxsize=1)
def get_tool_provider() -> ToolProvider:
    """mock=进程内假数据；mcp=MCP 业务工具服务（未覆盖/故障自动回落 mock）。"""
    provider = settings.TOOL_PROVIDER.lower()
    if provider == "mock":
        from app.chat.tools.mock_provider import mock_tool_provider

        return mock_tool_provider
    if provider == "mcp":
        from app.chat.tools.mcp_provider import mcp_tool_provider

        return mcp_tool_provider
    raise ValueError(f"不支持的 TOOL_PROVIDER={settings.TOOL_PROVIDER}，当前可选：mock / mcp")
