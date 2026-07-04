---
skill_id: ORDER.CREATE
name: 下单意向
domain: ORDER
description: 用户表示想买、想下单、询问如何购买
risk_level: L2
priority: 60

triggers:
  intents:
    - ORDER.CREATE

required_tools:
  - tool_id: query_product_info
    purpose: 确认商品信息、库存、价格用于下单确认
    required_slots: [product_id]
    optional: true
  - tool_id: query_product_stock
    purpose: 确认下单前库存充足
    required_slots: [product_id, sku_attr]
    optional: true

slots:
  - name: product_id
    description: 要购买的商品
    ask_prompt: "您想购买哪款商品呢？"
    required: true
    inherit_from_context: true
  - name: sku_attr
    description: 规格（颜色/尺寸等）
    ask_prompt: "请问您需要哪个规格？"
    required: false               # 商品只有一个规格时不需要问

actions:
  - action_id: guide_to_checkout
    description: 引导用户到支付/结算页面
    requires_confirmation: false  # 只是引导，非强制写操作
    rollback: false
  # 注意：真实下单（写库）由支付系统完成，本 Skill 只做引导
  # 若系统集成了代下单能力，需加 requires_confirmation: true

constraints:
  max_tool_calls: 2
  requires_human_if: "用户有批量采购/定制/合同需求"
  forbidden:
    - "不得在库存未确认前承诺可以发货"
    - "不得代替用户做购买决定"
    - "批量采购不走自动流程，必须转人工"

response_format:
  max_messages: 2
  style: "确认商品信息 → 告知下单方式；简洁引导，不过分推销"
---

## 当前场景：下单意向

**核心任务**：帮用户顺利完成下单，减少摩擦。

**有商品 + 有库存时**：

「[商品名] [规格] 现在有货，直接拍下就可以，有什么需要确认的吗？」

**用户说「我要买了」但商品不明确**：

先从 context_stacks.recent_products 看刚聊过什么，
有明确候选 → 「您是要购买 [商品名] 吗？」
无候选 → 「您想购买哪款商品呢？」

**库存不足时**：

「这款目前库存紧张，我帮您查一下能否预订/补货通知」
→ 切换到 PRODUCT.ASK_STOCK 的无货处理逻辑

**批量采购/合同采购**：

「批量采购需要人工报价，我帮您转接一下」→ 直接触发 META.TRANSFER_HUMAN
不要自行估算批量价格
