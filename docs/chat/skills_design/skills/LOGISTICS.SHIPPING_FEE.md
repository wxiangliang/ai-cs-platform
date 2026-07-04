---
skill_id: LOGISTICS.SHIPPING_FEE
name: 运费查询
domain: LOGISTICS
description: 用户询问运费、是否包邮、运费怎么算
risk_level: L0
priority: 70

triggers:
  intents:
    - LOGISTICS.SHIPPING_FEE

required_tools:
  - tool_id: query_shipping_policy
    purpose: 查询运费政策、包邮条件、指定地区运费
    required_slots: []
    optional: false
    filter_by_slots: [destination_region, order_amount]

slots:
  - name: destination_region
    description: 收货地区（省市）
    ask_prompt: "请问您的收货地址是哪个省市？"
    required: false               # 无则返回通用运费政策
  - name: order_amount
    description: 预计订单金额（用于判断是否满额包邮）
    ask_prompt: "您大概打算买多少金额的商品呢？"
    required: false

actions: []

constraints:
  max_tool_calls: 1
  requires_human_if: "用户询问偏远地区/跨境运费/大件物品运费"
  forbidden:
    - "不得在工具未返回前说「包邮」或报具体运费金额"
    - "不得编造满额包邮门槛"

response_format:
  max_messages: 1
  style: "直接告知政策；有包邮条件的说清楚条件"
---

## 当前场景：运费查询

**标准包邮**：

「满 [门槛金额] 包邮，[地区限制（如有）]」

**按地区收费**：

「[目的地] 运费 [X] 元，满 [金额] 包邮」

**偏远/特殊地区**：

「偏远地区运费可能有所不同，我帮您确认一下，或者转人工查询」

**跨境运费**：

「跨境订单运费根据重量和目的地计算，具体需要人工报价」
→ 触发 META.TRANSFER_HUMAN

**用户反映运费太贵**：

不要答应减免运费，说「这是当前的运费标准」，
若有活动可提示「您可以凑单满 [X] 元享受包邮」（须有工具依据）
