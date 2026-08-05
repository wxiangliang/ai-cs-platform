---
skill_id: AFTERSALE.RETURN
name: 退货申请
domain: AFTERSALE
description: 用户申请退货（收到商品后寄回），通常伴随退款
risk_level: L3
priority: 60

triggers:
  intents:
    - AFTERSALE.RETURN

required_tools:
  - tool_id: query_order
    purpose: 核实订单状态、签收时间、是否在退货期内
    required_slots: [customer_phone_or_order_id]
    optional: false
  - tool_id: query_return_policy
    purpose: 查询该商品退货政策（7天无理由/质量问题无限期等）
    required_slots: [order_id]
    optional: true
  - tool_id: create_return_label
    purpose: 生成退货快递面单（确认后才调用）
    required_slots: [order_id, return_reason]
    optional: false

slots:
  - name: customer_phone_or_order_id
    description: 订单号或下单手机号
    ask_prompt: "好的，请告诉我您的订单号或下单手机号"
    required: true
  - name: return_reason
    description: 退货原因
    ask_prompt: "请问退货的原因是什么？"
    required: true
    type: enum
    options: [不喜欢/不想要, 尺寸不合适, 质量问题, 与描述不符, 收到商品损坏, 其他]
  - name: has_original_packaging
    description: 是否有原包装
    ask_prompt: "商品的原包装还在吗？"
    required: false               # 部分政策要求原包装，有则采集


# 工具返回字段：确认话术占位符的来源声明（v2 schema）
tool_returns:
  - name: order_id
    from_tool: query_order
  - name: product_name
    from_tool: query_order
actions:
  - action_id: create_return_ticket
    description: 创建退货申请，生成退货面单
    requires_confirmation: true
    confirmation_prompt: |
      确认提交退货申请：
      - 订单：{order_id}
      - 商品：{product_name}
      - 退货原因：{return_reason}
      提交后我们会发送退货面单到您手机，请按面单寄回商品。确认吗？
    rollback: false

constraints:
  max_tool_calls: 3
  requires_human_if:
    - "超出退货期限（由政策工具判断）"
    - "商品为定制/个性化商品（通常不支持无理由退货）"
    - "涉及售后资产异常（错发/多发/非本人订单）"
    - "质量问题退货（可能需要人工审核照片）"
  forbidden:
    - "超出退货期不得自动承诺可以退货"
    - "定制商品不得自动走退货流程"
    - "不得承诺退货快递费用由谁承担（须政策工具明确）"
    - "不得在用户未确认前生成退货面单"

response_format:
  max_messages: 2
  style: "第1条：安抚+核实订单；第2条：告知退货政策+确认门或转人工"
---

## 当前场景：退货申请

**在退货期内，质量无问题（不喜欢/尺寸）**：

1. 核实订单和退货期
2. 告知退货政策（7天无理由/运费承担规则）
3. 确认退货原因
4. 确认门 → 创建退货单 → 发送退货面单
5. 告知寄回地址和注意事项

**质量问题退货**：

「质量问题退货，我们会安排人工优先处理，需要您提供一下商品照片，方便核实一下」
→ 触发 META.TRANSFER_HUMAN，同时说明需要提供照片

**超出退货期**：

「您的订单签收已超过 [退货期] 天，按平台政策超出了无理由退货时限。
如果是质量问题，可以走维修或投诉通道，需要我帮您了解吗？」
不直接拒绝，给出替代方案

**退货 vs 退款的区别**：

- AFTERSALE.RETURN：要把商品寄回（物流+退款两步）
- AFTERSALE.REFUND：只要钱（可能不需要寄货，如虚拟商品/严重质量问题）
- 用户说「退了」时，先判断是退货还是仅退款，不要搞混
