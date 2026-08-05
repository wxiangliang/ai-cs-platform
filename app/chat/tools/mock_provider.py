"""Mock 工具提供方：返回确定性假数据（联调与测试基线）。

数据生成在 app/chat/tools/mock_data.py（与 MCP 业务工具服务共享，保证一致）。
对接真实业务系统时新增 Provider 实现并在 factory 中切换，本文件退化为测试替身。
"""

import asyncio
import time
from typing import Any

from app.chat.tools import mock_data
from app.chat.tools.base import ToolResult
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class MockToolProvider:
    """内置 mock 实现。"""

    name = "mock"

    async def invoke(
        self, tool_id: str, params: dict[str, Any], *, tenant_id: str
    ) -> ToolResult:
        start = time.perf_counter()
        try:
            data = await asyncio.wait_for(
                self._dispatch(tool_id, params, tenant_id), timeout=settings.TOOL_TIMEOUT
            )
        except asyncio.TimeoutError:
            return ToolResult(ok=False, error_code="TOOL_TIMEOUT",
                              latency_ms=round((time.perf_counter() - start) * 1000, 2))
        except KeyError:
            return ToolResult(ok=False, error_code="TOOL_NOT_FOUND",
                              latency_ms=round((time.perf_counter() - start) * 1000, 2))
        return ToolResult(ok=True, data=data,
                          latency_ms=round((time.perf_counter() - start) * 1000, 2))

    async def _dispatch(self, tool_id: str, params: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        """按 tool_id 生成确定性假数据（共享 mock_data）；未知 tool_id 抛 KeyError。"""
        order_id = str(params.get("order_id") or params.get("customer_phone_or_order_id") or "")

        # —— 读操作 ——
        if tool_id == "query_order":
            return mock_data.order_data(tenant_id, order_id)
        if tool_id in ("query_logistics_track", "query_logistics"):
            return mock_data.logistics_data(tenant_id, order_id)
        if tool_id in ("query_product", "query_product_stock", "query_product_attr"):
            return {"product_name": params.get("product_name", ""), "stock": 35, "price": "1999.00 元"}
        if tool_id in ("query_refund_policy", "query_return_policy", "query_shipping_policy"):
            return {"policy": "签收后 7 天内商品完好可申请无理由退货；质量问题运费由商家承担。"}
        if tool_id in ("query_user_coupons", "query_coupon_policy"):
            return {"coupons": [{"name": "满 1000 减 50", "expire": "2026-07-31"}]}
        if tool_id == "query_member_status":
            return mock_data.member_status_data(tenant_id, str(params.get("user_id") or ""))

        # —— 写操作（只允许 ActionExecutor 调用）——
        if tool_id in (
            "create_refund_ticket", "create_return_ticket", "create_exchange_ticket",
            "create_repair_ticket", "create_complaint_ticket", "create_invoice",
            "cancel_order", "update_order_address",
        ):
            return mock_data.ticket_data(tenant_id, tool_id, order_id)
        if tool_id == "register_member":
            # 会员注册（Stage 33）：user_id 缺省用手机号兜底（mock 幂等按键）
            return mock_data.register_member_data(
                tenant_id,
                str(params.get("user_id") or params.get("phone") or ""),
                str(params.get("phone") or ""),
            )

        raise KeyError(tool_id)


# 模块级单例（factory 按 TOOL_PROVIDER 返回）
mock_tool_provider = MockToolProvider()
