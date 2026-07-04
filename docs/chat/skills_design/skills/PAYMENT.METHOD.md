---
skill_id: PAYMENT.METHOD
name: 支付方式
domain: PAYMENT
description: 用户询问支持哪些支付方式、能否分期、能否用某种方式付款
risk_level: L0
priority: 70

triggers:
  intents:
    - PAYMENT.METHOD

required_tools:
  - tool_id: query_payment_options
    purpose: 查询当前支持的支付方式和分期选项
    required_slots: []
    optional: false

slots: []                         # 无需槽位，支付方式查询是全局政策

actions: []

constraints:
  max_tool_calls: 1
  requires_human_if: "用户要求对公转账/银行电汇/大额定制支付"
  forbidden:
    - "不得承诺支持工具里没有返回的支付方式"
    - "不得说「一般都支持」「应该可以」等不确定表达"

response_format:
  max_messages: 1
  style: "列举支持的支付方式，有分期就说清楚条件"
---

## 当前场景：支付方式

**标准回复**：

「我们支持 [支付方式列表（工具返回）]。[如有分期：部分商品支持 [分期期数] 期免息分期]」

**用户问能否分期**：

有分期政策 → 「支持，[商品名/订单满X元] 可以选择 [X] 期分期，每期 [金额]」
无分期政策 → 「目前这款商品暂不支持分期，可以一次性付款」
不确定 → 「我帮您确认一下这款商品是否支持分期」

**用户要求对公转账/大额**：

「对公转账需要人工开具发票和确认账户信息，我帮您转接一下」
→ 触发 META.TRANSFER_HUMAN
