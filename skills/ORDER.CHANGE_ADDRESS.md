---
skill_id: ORDER.CHANGE_ADDRESS
name: 修改收货地址
domain: ORDER
description: 用户要求修改还未发货订单的收货地址
risk_level: L3
priority: 60

triggers:
  intents:
    - ORDER.CHANGE_ADDRESS
    - ORDER.CHANGE_INFO           # 修改其他订单信息（收件人/手机号）也走本 Skill

required_tools:
  - tool_id: query_order
    purpose: 确认订单状态（只有未发货才能改地址）
    required_slots: [customer_phone_or_order_id]
    optional: false
  - tool_id: update_order_address
    purpose: 更新收货地址
    required_slots: [order_id, new_address]
    optional: false

slots:
  - name: customer_phone_or_order_id
    description: 订单号或下单手机号
    ask_prompt: "请告诉我您的订单号或下单手机号"
    required: true
  - name: new_address
    description: 新的收货地址（省市区街道详细地址）
    ask_prompt: "请提供您的新收货地址（省、市、区、街道和门牌号）"
    required: true
    type: string
  - name: new_receiver_name
    description: 新收件人姓名（如需修改）
    ask_prompt: "收件人姓名需要修改吗？"
    required: false
  - name: new_receiver_phone
    description: 新收件人手机号（如需修改）
    ask_prompt: "收件人手机号需要修改吗？"
    required: false


# 工具返回字段：确认话术占位符的来源声明（v2 schema）
tool_returns:
  - name: order_id
    from_tool: query_order
actions:
  - action_id: update_order_address
    description: 修改订单收货地址
    requires_confirmation: true
    confirmation_prompt: |
      确认将订单 {order_id} 的收货信息修改为：
      - 地址：{new_address}
      {- 收件人：{new_receiver_name}（如有变更）}
      {- 手机：{new_receiver_phone}（如有变更）}
      确认修改吗？
    rollback: false               # 改后需再次申请才能改回

constraints:
  max_tool_calls: 2
  requires_human_if:
    - "订单已发货（地址无法自动修改，需联系快递公司）"
    - "跨境订单修改地址（可能涉及关税重算）"
  forbidden:
    - "已发货不得自动修改，必须告知已无法自动修改并转人工"
    - "不得修改其他用户的订单地址"
    - "地址信息不完整时不得提交修改"

response_format:
  max_messages: 2
  style: "第1条：确认当前订单和新地址；第2条：确认门或转人工说明"
---

## 当前场景：修改收货地址

**未发货 → 可修改**：

收集新地址后展示确认信息，用户确认后执行修改。
修改成功：「已为您更新收货地址，请注意查收快递」

**已发货 → 无法自动修改**：

「您的订单已经发货了，地址无法直接修改。
您可以联系快递公司（快递单号 {tracking_number}）申请改派，
或者等收到后拒收，我们安排退货退款。需要帮您联系人工处理吗？」

**地址不完整时**：

「地址信息需要包含省、市、区和详细街道，请补充完整」
不要用不完整的地址提交修改

**跨境订单**：

「跨境订单修改地址可能影响报关信息，需要人工处理，我帮您转接一下」
→ 触发 META.TRANSFER_HUMAN
