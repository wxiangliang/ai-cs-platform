---
skill_id: ORDER.CANCEL
name: 取消订单
domain: ORDER
description: 用户要求取消尚未发货的订单
risk_level: L3
priority: 60

triggers:
  intents:
    - ORDER.CANCEL

required_tools:
  - tool_id: query_order
    purpose: 核实订单状态，确认是否可取消（未发货才能自动取消）
    required_slots: [customer_phone_or_order_id]
    optional: false
  - tool_id: cancel_order
    purpose: 执行取消订单操作
    required_slots: [order_id]
    optional: false

slots:
  - name: customer_phone_or_order_id
    description: 订单号或下单手机号
    ask_prompt: "好的，请告诉我您的订单号或下单手机号"
    required: true
    type: string


# 工具返回字段：确认话术占位符的来源声明（v2 schema）
tool_returns:
  - name: order_id
    from_tool: query_order
  - name: product_name
    from_tool: query_order
  - name: amount
    from_tool: query_order
actions:
  - action_id: cancel_order
    description: 取消订单（退款至原支付方式）
    requires_confirmation: true
    confirmation_prompt: |
      确认取消以下订单：
      - 订单号：{order_id}
      - 商品：{product_name}
      - 退款金额：{amount}（退回原支付方式）
      确认取消吗？
    rollback: false               # 取消后不可撤销，需重新下单

constraints:
  max_tool_calls: 2
  requires_human_if:
    - "订单已发货（取消需人工拦截或走退货流程）"
    - "订单状态异常（已签收/纠纷中）"
    - "支付方式为线下/对公转账"
  forbidden:
    - "已发货订单不得自动取消，必须转人工或引导走退货流程"
    - "不得在查询前承诺可以取消"
    - "不得承诺退款到账时间（说「按支付方式退回，一般X个工作日」时须来自政策工具）"

response_format:
  max_messages: 2
  style: "第1条：确认订单信息；第2条：确认门话术或转人工说明"
---

## 当前场景：取消订单

**未发货 → 可自动取消**：

1. 查询订单，确认状态为「待发货/待揽收」
2. 展示确认信息，等用户明确确认
3. 执行取消，告知退款方式和预期时间

**已发货 → 不能直接取消**：

「您的订单已经发货了，现在无法直接取消。如果您不需要这个商品，可以在收到后申请退货退款，需要帮您了解退货流程吗？」
→ 可顺势切换到 AFTERSALE.RETURN 流程

**用户说「算了不取消了」**：

→ 识别为 META.ABORT，停止取消流程，回复「好的，订单保持不变，有需要随时告诉我」

**注意区分**：

- ORDER.CANCEL：取消还没发货的订单（钱还没走或退回很快）
- AFTERSALE.RETURN：收到货后退货（需要寄回商品）
- AFTERSALE.REFUND：退款（可能包含已发货的钱款处理）
