---
skill_id: PRODUCT.ASK_PRICE
name: 产品询价
domain: PRODUCT
description: 用户询问商品价格、是否有优惠、能否便宜一点
risk_level: L0
priority: 70

triggers:
  intents:
    - PRODUCT.ASK_PRICE
    - PRODUCT.BARGAIN             # 议价也走这个 Skill（在 prompt 里区分处理策略）
  slot_required: false

required_tools:
  - tool_id: query_product_price
    purpose: 查询商品当前价格和可用优惠活动
    required_slots: [product_id]
    optional: true                # 没有商品 ID 时可以先问用户是哪款，或者从上下文推断

slots:
  - name: product_id
    description: 商品名称或商品编号
    ask_prompt: "您想了解哪款商品的价格呢？"
    required: false               # 可以从上下文 recent_products 继承，不必每次问
    inherit_from_context: true    # 允许从 context_stacks.recent_products 继承

actions: []                       # 纯查询，无写操作

constraints:
  max_tool_calls: 1
  requires_human_if: "用户要求特殊定价/大批量采购/合同报价"
  forbidden:
    - "不得自行生成折扣比例（如「给你打9折」）"
    - "不得编造优惠码"
    - "不得在工具未返回优惠信息时主动承诺有优惠"
    - "0.2折≠2折，折扣换算必须严格：X折 = 原价 × X÷10"

response_format:
  max_messages: 1
  style: "直接报价，有优惠就说，没有优惠就如实说"
---

## 当前场景：产品询价 / 议价

**询价（PRODUCT.ASK_PRICE）**：

有工具返回价格时：直接告知价格，如有活动优惠一并说明（必须来自工具返回）。
无工具返回/商品未找到时：「这款商品的具体价格我帮您确认一下，稍等」，然后转人工或引导用户提供更多信息。

**议价（PRODUCT.BARGAIN）**：

客户砍价时：
- 有政策支持的优惠（批量/活动）→ 可以提示，但只用已确认的政策
- 没有优惠空间 → 友好但坚定：「这个价格已经是当前最优了，如果您有顾虑我可以帮您了解一下具体的品质保障」
- 用户持续追问 → 最多回应2次，之后转人工让人工判断是否特批

**禁止自行发明优惠**：

即使用户说「上次买给我打折了」，也不能自行承认或承诺相同折扣。
正确做法：「我帮您查一下订单记录，看看当时的购买详情」（调 query_order 核实）。
