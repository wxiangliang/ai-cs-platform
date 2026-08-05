---
skill_id: PROMOTION.COUPON
name: 优惠券
domain: PROMOTION
description: 用户询问优惠券使用方式、优惠码、领券、优惠券失效问题
risk_level: L1
priority: 70

triggers:
  intents:
    - PROMOTION.COUPON

required_tools:
  - tool_id: query_user_coupons
    purpose: 查询用户当前持有的可用优惠券
    required_slots: [user_id]
    optional: false
  - tool_id: query_coupon_policy
    purpose: 查询优惠券使用规则和限制
    required_slots: [coupon_id]
    optional: true

slots:
  - name: user_id
    description: 用户标识（系统从请求上下文注入，不向用户询问）
    ask_prompt: ""
    required: true
    type: string
    source: system_context          # 见 taxonomy 第 7 节槽位字典
  - name: coupon_id
    description: 优惠券编号（用户反馈使用问题时）
    ask_prompt: "请告诉我您的优惠券编号，我帮您查一下"
    required: false               # 用户只问「有没有券」时不需要

actions: []

constraints:
  max_tool_calls: 2
  requires_human_if: "优惠券显示未使用但已失效，用户要求补偿"
  forbidden:
    - "不得自行生成优惠码"
    - "不得承诺可以延长优惠券有效期"
    - "不得把非本店优惠券当成可用优惠券告知用户"

response_format:
  max_messages: 1
  style: "直接告知有哪些可用券和使用条件；无券时告知获取渠道"
---

## 当前场景：优惠券

**用户问「有没有优惠券/优惠码」**：

查询用户账户下的可用券：
有券 → 「您账户里有 [X] 张优惠券，[券名] 满 [X] 元减 [X] 元，有效期至 [日期]」
无券 → 「您目前没有可用的优惠券，[如有获取渠道：可以通过 XX 领取]」

**用户反馈优惠券无法使用**：

「我帮您查一下 [券号] 的使用限制：
[券的适用范围/最低消费/有效期]」
告知具体原因，不说「我也不知道为什么用不了」

**优惠券已过期**：

「这张优惠券有效期到 [日期]，已过期了，目前无法使用。
[如有新活动：最近有 [活动名] 可以领新券，您要了解吗？]」
不承诺补发或延期

**与议价的区别**：

- PROMOTION.COUPON：用户问现有优惠券如何使用
- 用户希望获得折扣/砍价 → 归 PRODUCT.ASK_PRICE（BARGAIN 别名），见该 Skill 的议价处理
  （旧文档中的 `PROMOTION.NEGOTIATE` 意图码已废弃，从未有对应 Skill，见 taxonomy 2.1 节）
