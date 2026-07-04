"""MCP 业务工具服务（Stage 11）。

把订单查询/物流查询以 **MCP（Model Context Protocol）标准工具**对外提供，
模拟真实业务系统的接入形态：本项目（客服平台）作为 MCP 客户端调用本服务
（TOOL_PROVIDER=mcp，见 app/chat/tools/mcp_provider.py）。

当前数据源为共享确定性 mock（app/chat/tools/mock_data.py，与进程内 mock 一致，
便于对拍验证）；对接真实业务系统时，把工具函数内部替换为真实 DB/RPC 查询即可，
客服平台侧零改动——这正是 MCP 分层的意义。

启动：uv run python scripts/run_mcp_server.py [--port 8400]
协议端点：http://localhost:8400/mcp（streamable-http）
"""

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app.chat.tools import mock_data  # noqa: E402


def build_server(host: str, port: int) -> FastMCP:
    """构建 MCP 服务（独立函数便于测试）。"""
    server = FastMCP("ai-cs-business-tools", host=host, port=port)

    @server.tool()
    def query_order(order_id: str, tenant_id: str = "") -> dict[str, Any]:
        """查询订单状态：返回订单当前状态、商品、金额、运单号（已发货时）。"""
        return mock_data.order_data(tenant_id, order_id)

    @server.tool()
    def query_logistics_track(order_id: str, tenant_id: str = "") -> dict[str, Any]:
        """查询物流轨迹：返回最新进度、轨迹事件与预计送达时间。"""
        return mock_data.logistics_data(tenant_id, order_id)

    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP 业务工具服务（订单/物流查询）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8400)
    args = parser.parse_args()
    build_server(args.host, args.port).run(transport="streamable-http")
