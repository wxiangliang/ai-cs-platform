---
skill_id: AFTERSALE.REFUND
name: 退款申请
domain: AFTERSALE
description: 用户申请退款、退货退款、投诉要求退款
risk_level: L3
priority: 60

triggers:
  intents:
    - AFTERSALE.REFUND
  slot_required: false

required_tools:
  - tool_id: query_order
    purpose: 核实订单真实状态、金额、签收情况
    required_slots: [customer_phone_or_order_id]
    optional: false
  - tool_id: query_refund_policy
    purpose: 查询该商品/订单的退款政策和资格
    required_slots: [order_id]
    optional: true                # 有就用，没有也能继续（转人工）

slots:
  - name: customer_phone_or_order_id
    description: 下单手机号或订单号
    ask_prompt: "好的，我来帮您处理，请告诉我您的订单号或下单手机号"
    required: true
    type: string

  - name: refund_reason
    description: 退款原因
    ask_prompt: "请问是什么原因需要退款呢？比如收到商品有问题，或者不想要了"
    required: true
    type: enum
    options: [质量问题, 收到商品与描述不符, 未收到商品, 不喜欢/不想要, 其他]


# 工具返回字段：确认话术占位符的来源声明（v2 schema）
tool_returns:
  - name: order_id
    from_tool: query_order
  - name: refund_amount
    from_tool: query_order
actions:
  - action_id: create_refund_ticket
    description: 创建退款申请工单
    requires_confirmation: true   # 必须用户明确确认才执行
    confirmation_prompt: |
      我将为您提交退款申请：
      - 订单：{order_id}
      - 退款金额：{refund_amount}（以实际审核为准）
      - 原因：{refund_reason}
      请问确认提交吗？
    rollback: false               # 提交后不可撤销，需人工处理

constraints:
  max_tool_calls: 2
  requires_human_if:
    - "订单状态异常（物流纠纷/已拒收/海关扣押）"
    - "用户反馈错发/多发/非本人订单"
    - "退款金额超过系统自动处理上限"
    - "用户情绪激烈且第一轮安抚无效"
  forbidden:
    - "不得在未查订单前承诺可以退款"
    - "不得承诺具体退款到账时间（除非政策工具明确返回）"
    - "不得承诺退款金额（以工具返回或审核结果为准）"
    - "不得让用户保留错发商品（必须转人工核实）"

response_format:
  max_messages: 2
  style: "第1条：安抚+表示已收到诉求；第2条：告知下一步（收集信息/已提交/转人工）"
---

## 当前场景：退款申请

**第一步：先安抚，不急着处理**

用户申请退款时往往已有负面情绪，第一句先表示理解，再进入流程：
- 好：「了解，给您带来不便很抱歉，我来帮您查一下」
- 差：「好的，请提供订单号」（太机械，缺少温度）

**第二步：核实订单，不盲目承诺**

拿到订单信息后用 `query_order` 核实：
- 订单真实存在且在退款资格期内 → 继续收集退款原因 → 确认后创建工单
- 订单不存在 → 请用户确认信息 → 仍不存在则转人工
- 订单已签收超时/超期 → 告知政策，视情况转人工特批

**售后资产异常（最高优先级规则）**：

用户反馈以下情况，**必须暂停自动流程，直接转人工**：
- 错发商品（收到的不是下单商品）
- 多发（收到多于订购数量）
- 退款后又收到货
- 非本人订单送达

在转人工前不得说「可以保留」「我们承担」「不用退回」，只说：「这种情况需要人工核实处理，我帮您转接一下」

**确认门**：

提交工单前必须让用户明确确认（「确认」「好的」「是的」算确认，「等等」「先不急」「再想想」算拒绝）。
用户说「算了不退了」→ 意图变为 META.ABORT，不再执行退款流程。
