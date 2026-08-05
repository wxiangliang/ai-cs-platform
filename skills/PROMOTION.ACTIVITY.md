---
skill_id: PROMOTION.ACTIVITY
name: 活动规则查询
domain: PROMOTION
description: 用户询问当前有什么活动、活动规则、活动是否有效、折扣条件
risk_level: L0
priority: 70

triggers:
  intents:
    - PROMOTION.ACTIVITY

required_tools:
  - tool_id: query_active_promotions
    purpose: 查询当前进行中的促销活动和规则
    required_slots: []
    optional: false

slots: []                         # 活动查询无需槽位，返回全部活动

actions: []

constraints:
  max_tool_calls: 1
  requires_human_if: "用户询问的活动工具里没有返回，但用户坚称看到了"
  forbidden:
    - "不得编造活动内容、活动时间、折扣金额"
    - "折扣换算严格遵守：X折 = 原价 × X÷10（2折=20%，不是200%）"
    - "不得承诺活动延长"

response_format:
  max_messages: 1
  style: "列举活动名称+核心优惠+有效期+使用条件；不超过3个活动，太多反而让人困惑"
---

## 当前场景：活动查询

**有活动时**：

「当前有以下活动：
[活动名]：[核心优惠描述]，有效期 [开始]-[结束]，[使用条件]
[活动名2]：...（最多列3个，其余引导用户去活动页查看）」

**没有活动时**：

「目前没有进行中的活动，新活动开始时会通知您」
不要编造「应该快有了」「双十一快到了」等猜测

**用户说「我看到你们有 XX 活动」但工具里没有**：

「我这边没有查到这个活动的记录，有可能已经结束了，或者您看到的是第三方平台的信息，
我帮您转人工确认一下」
不要直接否定用户，也不要承认有这个活动

**折扣换算强调**：

用户说「你们不是有 8折 活动吗，这个 100 元的商品打 8 折是多少」：
正确：「8折 = 100 × 0.8 = 80 元」
绝对不要算错，8折不是 20%，是 80%
