---
skill_id: PAYMENT.ISSUE
name: 支付失败 / 扣款异常
domain: PAYMENT
description: 用户反馈付款失败、重复扣款、扣款后订单未生成、退款到账问题
risk_level: L1
priority: 60

triggers:
  intents:
    - PAYMENT.ISSUE

required_tools:
  - tool_id: query_order
    purpose: 查询订单支付状态
    required_slots: [customer_phone_or_order_id]
    optional: false
  - tool_id: query_payment_record
    purpose: 查询支付流水记录
    required_slots: [customer_phone_or_order_id]
    optional: true                # 订单查不到时用支付记录核实

slots:
  - name: customer_phone_or_order_id
    description: 订单号或下单手机号
    ask_prompt: "请告诉我您的订单号或下单手机号，我帮您查一下"
    required: true
  - name: issue_type
    description: 问题类型
    ask_prompt: "请问是支付失败、重复扣款，还是退款没到账呢？"
    required: false
    type: enum
    options: [支付失败订单未生成, 重复扣款, 扣款后订单未显示, 退款未到账, 其他]

actions: []                       # 支付异常全部转人工，不自动处理

constraints:
  max_tool_calls: 2
  requires_human_if: "所有支付异常情况均需人工核实，不自动处理"
  forbidden:
    - "不得自行判断是否已扣款成功（以支付系统记录为准）"
    - "不得承诺退款时间（须支付系统确认）"
    - "不得让用户重复支付后再退（可能造成二次损失）"

response_format:
  max_messages: 2
  style: "第1条：确认问题类型；第2条：告知查询结果+转人工"
---

## 当前场景：支付异常

**支付失败**：

「我帮您查了一下，订单 [X] 支付状态为失败，您的账户应该没有扣款。
您可以重新尝试支付，如果再次失败我帮您转人工处理」

**重复扣款**：

重复扣款是财务敏感操作，直接转人工：
「重复扣款我需要帮您转给财务核实，请稍等」
不要自行判断是否扣了两次

**扣款后订单未生成**：

「我帮您查了支付记录，[已扣款/未确认扣款]，订单状态异常，
需要人工核实处理，我帮您转接一下，不用重新支付」

**退款未到账**：

「退款一般需要 [政策工具返回的时间] 个工作日到账，
您的退款于 [退款日期] 发起，预计 [到账日期] 到账。
如果超过这个时间还未到账，请联系我们」
（如果查不到退款记录 → 转人工核实）
