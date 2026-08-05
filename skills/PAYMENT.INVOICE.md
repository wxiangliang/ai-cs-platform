---
skill_id: PAYMENT.INVOICE
name: 发票申请
domain: PAYMENT
description: 用户申请开具发票、询问发票政策、修改发票信息
risk_level: L3
priority: 60

triggers:
  intents:
    - PAYMENT.INVOICE

required_tools:
  - tool_id: query_order
    purpose: 核实订单信息，确认是否可以开票
    required_slots: [customer_phone_or_order_id]
    optional: false
  - tool_id: create_invoice
    purpose: 创建开票申请
    required_slots: [order_id, invoice_type, invoice_title, tax_id]
    optional: false

slots:
  - name: customer_phone_or_order_id
    description: 订单号或下单手机号
    ask_prompt: "请告诉我您的订单号或下单手机号"
    required: true
  - name: invoice_type
    description: 发票类型（普通/增值税专用）
    ask_prompt: "您需要普通发票还是增值税专用发票？"
    required: true
    type: enum
    options: [普通电子发票, 增值税专用发票]
  - name: invoice_title
    description: 发票抬头（个人或公司名称）
    ask_prompt: "发票抬头是？（个人填姓名，公司填公司全称）"
    required: true
  - name: tax_id
    description: 税号（增值税专票必须，普通票可选）
    ask_prompt: "请提供公司税号"
    required: false               # 增值税专票时变为 required


# 工具返回字段：确认话术占位符的来源声明（v2 schema）
tool_returns:
  - name: order_id
    from_tool: query_order
actions:
  - action_id: create_invoice
    description: 提交开票申请
    requires_confirmation: true
    confirmation_prompt: |
      确认开票信息：
      - 订单：{order_id}
      - 发票类型：{invoice_type}
      - 发票抬头：{invoice_title}
      {- 税号：{tax_id}（如有）}
      确认提交吗？提交后信息不可修改。
    rollback: false               # 发票一旦开出不可修改，需作废重开

constraints:
  max_tool_calls: 2
  requires_human_if:
    - "增值税专用发票（金额大，人工审核）"
    - "需要纸质发票邮寄"
    - "已开发票需要作废重开"
  forbidden:
    - "不得在订单未付款完成时开票"
    - "不得修改已开出的发票信息（必须作废重开，转人工）"
    - "增值税专用发票不走自动流程"

response_format:
  max_messages: 2
  style: "逐步收集开票信息，确认门前展示完整信息"
---

## 当前场景：发票申请

**普通电子发票（常见场景）**：

收集：抬头 + 发票类型
确认后提交，「发票将发送到您的手机号/邮箱，一般 [X] 个工作日内到达」

**增值税专用发票**：

需要：公司全称 + 税号 + 注册地址 + 电话 + 开户行 + 账号
信息较多，告知用户：「增值税专票需要核实资质，我帮您转接财务处理」

**已开票，想修改**：

「发票开出后无法直接修改，需要作废后重开，这需要人工处理，我帮您转接一下」

**订单部分退款后开票**：

「您有部分退款，开票金额为实际支付金额 [X] 元，确认吗？」
不要按原始订单金额开票
