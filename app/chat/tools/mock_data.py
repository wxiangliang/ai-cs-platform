"""确定性 mock 业务数据生成（Stage 11 抽取为共享模块）。

被两处复用，保证数据一致：
1. MockToolProvider（进程内 mock 工具）；
2. MCP 业务工具服务（scripts/run_mcp_server.py，模拟外部业务系统）。
确定性规则：同样入参永远同样输出（hash 派生），便于测试断言与问题复现。
对接真实业务系统后本模块仅测试使用。
"""

import hashlib
from typing import Any


def _digest(*parts: str) -> str:
    """入参派生的确定性短哈希（工单号/金额来源）。"""
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def order_data(tenant_id: str, order_id: str) -> dict[str, Any]:
    """订单查询假数据（状态按 hash 稳定分布：多数待发货，少数已发货）。"""
    seed = _digest(tenant_id, "query_order", order_id)
    shipped = int(seed[:2], 16) % 4 == 0
    return {
        "order_id": order_id or f"MO{seed[:10].upper()}",
        "status": "已发货" if shipped else "待发货",
        "product_name": "凉风空调 X1",
        "amount": f"{1000 + int(seed[2:5], 16) % 2000}.00 元",
        "refund_amount": f"{1000 + int(seed[2:5], 16) % 2000}.00 元",
        "tracking_number": f"SF{seed[5:15].upper()}" if shipped else "",
    }


def logistics_data(tenant_id: str, order_id: str) -> dict[str, Any]:
    """物流轨迹假数据。"""
    seed = _digest(tenant_id, "query_logistics_track", order_id)
    return {
        "order_id": order_id,
        "tracking_number": f"SF{seed[:10].upper()}",
        "latest": "包裹已到达本市转运中心，正在派送中",
        "eta": "预计明天 18:00 前送达",
        "events": [
            "昨天 20:15 已从发货仓发出",
            "今天 06:40 到达本市转运中心",
            "今天 08:12 派送中",
        ],
    }


# —— Stage 33 会员 mock：进程内注册状态（tenant:user → member_no）。
# 闭环用途：register_member 写入后 query_member_status 即已注册，
# NBA 不再重复建议；真实会员系统对接后本表仅测试使用
MEMBERS: dict[str, str] = {}


def _member_key(tenant_id: str, user_id: str) -> str:
    return f"{tenant_id}:{user_id}"


def member_status_data(tenant_id: str, user_id: str) -> dict[str, Any]:
    """会员状态查询假数据。"""
    member_no = MEMBERS.get(_member_key(tenant_id, user_id))
    return {"user_id": user_id, "registered": member_no is not None, "member_no": member_no}


def register_member_data(tenant_id: str, user_id: str, phone: str) -> dict[str, Any]:
    """注册会员假数据：确定性会员号，写入进程内状态表（幂等：同键同号）。"""
    key = _member_key(tenant_id, user_id)
    member_no = MEMBERS.get(key) or f"MB{_digest(tenant_id, 'member', user_id, phone)[:10].upper()}"
    MEMBERS[key] = member_no
    return {"ticket_no": member_no, "member_no": member_no, "phone": phone}


def ticket_data(tenant_id: str, tool_id: str, order_id: str) -> dict[str, Any]:
    """写操作工单假数据。"""
    seed = _digest(tenant_id, tool_id, order_id)
    prefix = {
        "create_refund_ticket": "RF", "create_return_ticket": "RT",
        "create_exchange_ticket": "EX", "create_repair_ticket": "RP",
        "create_complaint_ticket": "CP", "create_invoice": "IV",
        "cancel_order": "CA", "update_order_address": "AD",
    }.get(tool_id, "TK")
    data: dict[str, Any] = {"ticket_no": f"{prefix}{seed[:10].upper()}", "order_id": order_id}
    if tool_id == "cancel_order":
        data["refund_way"] = "原路退回"
    return data
