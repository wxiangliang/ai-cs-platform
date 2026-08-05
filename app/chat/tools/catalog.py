"""进程内工具目录（post-stage-27 工程护栏 ②：白名单元数据化的单一事实来源）。

此前诊断 agent 的只读白名单硬编码在 diagnose.py（Stage 22 遗留：MCP 新增
读工具需手动同步）。本目录把「工具是否只读」变成声明：

- 诊断 agent 白名单 = 本目录 readonly=True 的**规范名**工具
  （推导而非手抄）∪ MCP 服务声明 readOnlyHint 的工具（mcp_provider 发现时捕获）；
- deny-by-default：目录未收录、未声明只读的一律进不了白名单；
- 红线：readonly=False 的写工具**无论谁声明什么**都进不了白名单——
  外部 MCP 服务把 cancel_order 标成只读也无效（目录声明优先，
  防服务端伪造，见 post-stage-27 文档遗留 2）。

alias 只是 mock 派发的同义 id（query_logistics == query_logistics_track），
不单独进白名单——决策 prompt 只列规范名，防 LLM 在同义工具间打转。
写操作工具永远只归 ActionExecutor（确认门后），与本目录的白名单推导无关。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolMeta:
    """工具声明：一句话描述（进诊断决策 prompt）+ 是否只读 + 最低身份等级。

    min_ial（Stage 35）：IAL0 无要求 / IAL1 渠道身份 / IAL2 OTP 级 /
    IAL3 高风险二次验证。执法点在 ActionExecutor（写）与 tool_invoke（读），
    结构性拒绝——模型任何输出都绕不过。
    """

    description: str
    readonly: bool
    min_ial: int = 0


# 规范名工具目录（与 mock_provider._dispatch / MCP 服务工具保持一致）
TOOL_CATALOG: dict[str, ToolMeta] = {
    # —— 只读查询（诊断 agent 白名单由此推导）——
    # 政策类公开信息 IAL0；涉个人数据（订单/物流/优惠券/会员）IAL1
    "query_order": ToolMeta("查询订单状态/金额/运单号（参数 order_id）", readonly=True, min_ial=1),
    "query_logistics_track": ToolMeta("查询物流轨迹与最新进度（参数 order_id）", readonly=True, min_ial=1),
    "query_refund_policy": ToolMeta("查询退款/退货政策（无参数）", readonly=True),
    "query_shipping_policy": ToolMeta("查询运费/配送政策（无参数）", readonly=True),
    "query_product": ToolMeta("查询商品价格/库存（参数 product_name）", readonly=True),
    "query_user_coupons": ToolMeta("查询用户可用优惠券（无参数）", readonly=True, min_ial=1),
    "query_member_status": ToolMeta("查询用户是否已注册会员（参数 user_id）", readonly=True, min_ial=1),
    # —— 写操作（唯一入口 ActionExecutor，永不进任何模型可选白名单）——
    # 改地址 IAL2：收货地址改写是账号盗用的典型攻击面（Stage 35 评审示例）
    "create_refund_ticket": ToolMeta("提交退款工单", readonly=False, min_ial=1),
    "create_return_ticket": ToolMeta("提交退货工单", readonly=False, min_ial=1),
    "create_exchange_ticket": ToolMeta("提交换货工单", readonly=False, min_ial=1),
    "create_repair_ticket": ToolMeta("提交维修工单", readonly=False, min_ial=1),
    "create_complaint_ticket": ToolMeta("提交投诉工单", readonly=False, min_ial=1),
    "create_invoice": ToolMeta("提交开票申请", readonly=False, min_ial=1),
    "cancel_order": ToolMeta("取消订单", readonly=False, min_ial=1),
    "update_order_address": ToolMeta("修改订单收货地址", readonly=False, min_ial=2),
    "register_member": ToolMeta("注册会员（Stage 33）", readonly=False, min_ial=1),
}

# mock 派发接受的同义 id → 规范名（不进白名单，仅存在性说明）
TOOL_ALIASES: dict[str, str] = {
    "query_logistics": "query_logistics_track",
    "query_product_stock": "query_product",
    "query_product_attr": "query_product",
    "query_return_policy": "query_refund_policy",
    "query_coupon_policy": "query_user_coupons",
}


def readonly_tool_descriptions() -> dict[str, str]:
    """只读工具白名单（id → 描述），诊断 agent 决策 prompt 与准入校验共用。"""
    return {
        tool_id: meta.description
        for tool_id, meta in TOOL_CATALOG.items()
        if meta.readonly
    }


def is_declared_write_tool(tool_id: str) -> bool:
    """是否为目录声明的写工具（含 alias 归一）。白名单合并 MCP 声明时的红线过滤。"""
    canonical = TOOL_ALIASES.get(tool_id, tool_id)
    meta = TOOL_CATALOG.get(canonical)
    return meta is not None and not meta.readonly


def required_ial(tool_id: str) -> int:
    """工具最低身份等级（含 alias 归一）；未收录工具按 IAL1 保守处理。"""
    canonical = TOOL_ALIASES.get(tool_id, tool_id)
    meta = TOOL_CATALOG.get(canonical)
    return meta.min_ial if meta is not None else 1
