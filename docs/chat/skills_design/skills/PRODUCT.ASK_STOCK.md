---
skill_id: PRODUCT.ASK_STOCK
name: 库存查询
domain: PRODUCT
description: 用户询问商品是否有货、有无现货、特定规格是否有库存
risk_level: L0
priority: 70

triggers:
  intents:
    - PRODUCT.ASK_STOCK

required_tools:
  - tool_id: query_product_stock
    purpose: 查询商品库存状态及可用规格
    required_slots: [product_id]
    optional: false               # 无法在不知道商品的情况下回答库存

slots:
  - name: product_id
    description: 商品名称或编号
    ask_prompt: "您想查哪款商品的库存？"
    required: true
    inherit_from_context: true
  - name: sku_attr
    description: 具体规格（颜色/尺寸/型号等）
    ask_prompt: "您需要哪个规格的呢？比如颜色或尺寸"
    required: false               # 无则返回整体库存状态

actions: []

constraints:
  max_tool_calls: 1
  requires_human_if: "库存状态为预订/定制/特殊渠道采购"
  forbidden:
    - "不得在工具未返回前说「有货」或「没货」"
    - "不得承诺补货时间（除非工具明确返回）"
    - "不得说「应该还有」「估计够」等猜测性表达"

response_format:
  max_messages: 1
  style: "直接告知库存状态；无货时给出替代选项或到货通知方式"
---

## 当前场景：库存查询

**有库存**：「[商品名] [规格] 目前有现货，可以直接下单」

**无库存**：

不直接说「没有了」就结束，给出下一步：
- 「目前这个规格暂时缺货，预计 [工具返回时间] 到货，需要我帮您登记到货通知吗？」
- 若工具没有到货时间：「暂时缺货，我帮您记录一下，到货会第一时间通知您」
- 有替代规格/款式时：「这个规格目前没货，但 [类似款] 还有，您要看看吗？」

**整体有货但指定规格缺货**：

「[商品名] 还有货，但 [颜色/尺寸] 这个规格暂时缺了，其他规格（[列举]）都还有」
