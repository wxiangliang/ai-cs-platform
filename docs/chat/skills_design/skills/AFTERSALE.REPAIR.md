---
skill_id: AFTERSALE.REPAIR
name: 维修 / 保修
domain: AFTERSALE
description: 用户询问保修政策、申请维修服务、商品出现故障
risk_level: L2
priority: 60

triggers:
  intents:
    - AFTERSALE.REPAIR

required_tools:
  - tool_id: query_order
    purpose: 核实购买记录和购买日期，判断是否在保修期内
    required_slots: [customer_phone_or_order_id]
    optional: false
  - tool_id: query_warranty_policy
    purpose: 查询该商品的保修政策和保修期限
    required_slots: [product_id]
    optional: true
  - tool_id: create_repair_ticket
    purpose: 创建维修工单
    required_slots: [order_id, issue_description]
    optional: false

slots:
  - name: customer_phone_or_order_id
    description: 订单号或下单手机号
    ask_prompt: "请告诉我您的订单号或购买时的手机号"
    required: true
  - name: issue_description
    description: 故障或问题描述
    ask_prompt: "请描述一下商品出现了什么问题？"
    required: true
    type: string


# 工具返回字段：确认话术占位符的来源声明（v2 schema）
tool_returns:
  - name: order_id
    from_tool: query_order
  - name: product_name
    from_tool: query_order
actions:
  - action_id: create_repair_ticket
    description: 创建维修申请工单
    requires_confirmation: true
    confirmation_prompt: |
      确认提交维修申请：
      - 订单：{order_id}
      - 商品：{product_name}
      - 问题描述：{issue_description}
      提交后我们安排技术人员联系您。确认吗？
    rollback: false

constraints:
  max_tool_calls: 3
  requires_human_if:
    - "用户描述的问题判断不清楚是否在保修范围（如人为损坏模糊）"
    - "超出保修期但用户坚持维修（需人工判断是否付费维修）"
    - "高价值商品维修（金额超阈值需人工跟进）"
  forbidden:
    - "不得在未查保修政策时承诺「在保修期内可以免费维修」"
    - "不得对人为损坏承诺免费维修"
    - "不得编造维修时效"

response_format:
  max_messages: 2
  style: "第1条：核实信息+说明保修情况；第2条：告知下一步（确认门/转人工）"
---

## 当前场景：维修 / 保修

**在保修期内，属保修范围**：

「您的商品购买于 [日期]，在 [保修期] 内，这个问题属于保修范围。
我帮您创建维修申请，技术人员会联系您安排」
→ 确认门 → 创建维修工单

**保修期内，人为损坏（不在保修范围）**：

「您描述的情况属于人为损坏，不在免费保修范围内，可以选择付费维修。
需要了解付费维修的大概费用吗？」→ 转人工报价

**超出保修期**：

「您的商品购买已超出保修期，可以选择付费维修，需要我转人工了解维修方案和费用吗？」

**用户只是问保修政策（未申请维修）**：

直接告知政策，不进入维修申请流程：
「这款商品提供 [X] 年 [保修内容] 保修，人为损坏不在保修范围内」
