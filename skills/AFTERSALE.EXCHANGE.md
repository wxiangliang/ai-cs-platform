---
skill_id: AFTERSALE.EXCHANGE
name: 换货申请
domain: AFTERSALE
description: 用户要求换货，换不同规格或同款补发
risk_level: L3
priority: 60

triggers:
  intents:
    - AFTERSALE.EXCHANGE

required_tools:
  - tool_id: query_order
    purpose: 核实订单和商品信息
    required_slots: [customer_phone_or_order_id]
    optional: false
  - tool_id: query_product_stock
    purpose: 确认换货目标规格是否有库存
    required_slots: [product_id, sku_attr]
    optional: false               # 库存不足换货无法进行
  - tool_id: create_exchange_ticket
    purpose: 创建换货工单
    required_slots: [order_id, exchange_reason, target_sku]
    optional: false

slots:
  - name: customer_phone_or_order_id
    description: 订单号或下单手机号
    ask_prompt: "好的，请告诉我您的订单号或下单手机号"
    required: true
  - name: exchange_reason
    description: 换货原因
    ask_prompt: "请问是什么原因需要换货呢？"
    required: true
    type: enum
    options: [尺寸不合适, 颜色选错了, 收到商品有损坏, 与描述不符, 其他]
  - name: target_sku
    description: 要换成的规格（颜色/尺寸等）
    ask_prompt: "您想换成哪个规格？"
    required: true

# 工具返回字段：确认话术占位符的来源声明（v2 schema 新增）
tool_returns:
  - name: order_id                  # query_order 解析订单线索后返回
    from_tool: query_order
  - name: product_id                # 原商品 ID，供 query_product_stock 使用
    from_tool: query_order
  - name: original_sku              # 原商品规格，用于确认话术展示
    from_tool: query_order

actions:
  - action_id: create_exchange_ticket
    description: 创建换货工单
    requires_confirmation: true
    confirmation_prompt: |
      确认提交换货申请：
      - 订单：{order_id}
      - 原商品：{original_sku}
      - 换成：{target_sku}
      - 原因：{exchange_reason}
      换货需要先将原商品寄回，收到后我们发出新商品。确认吗？
    rollback: false

constraints:
  max_tool_calls: 3
  requires_human_if:
    - "目标规格无库存（转人工看是否有其他方案）"
    - "超出换货期限"
    - "涉及错发/多发等售后资产异常"
  forbidden:
    - "目标库存不足不得承诺可以换货"
    - "不得承诺换货时效（除非政策工具明确）"
    - "不得让用户先寄回再查库存（应先确认目标规格有库存）"

response_format:
  max_messages: 2
  style: "第1条：安抚+说明换货流程；第2条：确认槽位+确认门"
---

## 当前场景：换货申请

**换货前先查库存**（重要顺序）：

1. 先确认用户想换什么规格
2. 查目标规格库存 → 有库存再继续
3. 查订单核实换货资格
4. 确认门 → 创建换货单

**目标规格无库存**：

「您想换的 [规格] 目前暂时没货，有以下选择：
1. 等到货通知（预计 [时间]）
2. 选择其他有货的规格（[列举可选规格]）
3. 申请退款
您倾向哪种方式？」
不要直接说「换不了」就结束

**质量损坏换货**：

「收到的商品有损坏，这边安排优先处理，请发几张商品照片给我，方便我们核实」
→ 需要人工审核，触发 META.TRANSFER_HUMAN

**换货 vs 退货退款**：

用户说「换货」时确认是否真的想换（而不是实际想退款）：
如果用户犹豫，可以说「换货是寄回原商品换同款其他规格；如果不想要了可以直接退款，您更倾向哪个？」
