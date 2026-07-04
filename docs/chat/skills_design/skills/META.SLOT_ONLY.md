---
skill_id: META.SLOT_ONLY
name: 裸槽位（仅给出信息值，无明确动作）
domain: META
description: 用户只说了一个信息值，没有明确动作意图（如只说「白色」「A12345」「两件」）
risk_level: L0
priority: 50

triggers:
  intents:
    - META.SLOT_ONLY

required_tools: []

slots: []                         # 不需要收集新槽位，本身就是槽位值

actions: []

constraints:
  forbidden:
    - "不得把裸槽位当成独立意图处理"
    - "不得回复「好的，我明白了」却不做任何事情"
    - "不得追问「您是什么意思」—— 先结合上下文推断"

response_format:
  max_messages: 1
  style: "结合当前 active 任务填入槽位值，继续原流程；无 active 任务则轻询意图"
---

## 当前场景：裸槽位

**META.SLOT_ONLY 不是终态意图**，它的意思是：

「用户只给了一个值，意图轴为空，需要结合状态确定用途」

**有 active 任务时（最常见场景）**：

系统正在问「您需要哪个规格」→ 用户回「白色」：
→ META.SLOT_ONLY，值为「白色」
→ 填入 active 任务的 sku_attr 槽位
→ 继续原流程（不需要重新问用户意图）
回复：「好的，白色，帮您继续处理」

系统正在问「订单号是多少」→ 用户回「A12345」：
→ META.SLOT_ONLY，值为订单号
→ 填入 order_id 槽位
→ 继续查询流程

**有 suspended 任务时**：

用户之前在退款流程，中途问了别的问题，现在只说「我再补充一下 A12345」：
→ 判断是否是 suspended 任务的槽位值
→ 是 → 填入挂起任务，唤醒继续

**无任何 active/suspended 任务时**：

孤立的裸槽位需要推断意图：
- 说了一个订单号 → 可能是查询，轻问「您是想查询这个订单的状态吗？」
- 说了「两件」→ 可能是购买数量，「请问您是要购买两件 [上下文商品名] 吗？」
- 无法推断 → 「您是有什么需要我帮忙的吗？」（开放式，不强行猜）

**注意**：

META.SLOT_ONLY 是分类器侧标签，下游 Resolver 处理时：
intent 轴=null，slot 轴=有值 → 走槽位填充逻辑，不走意图路由
