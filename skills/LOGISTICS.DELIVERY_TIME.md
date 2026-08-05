---
skill_id: LOGISTICS.DELIVERY_TIME
name: 发货时间 / 预计到达时间
domain: LOGISTICS
description: 用户询问什么时候发货、多久能到、预计到货日期
risk_level: L1
priority: 70

triggers:
  intents:
    - LOGISTICS.DELIVERY_TIME

required_tools:
  - tool_id: query_order
    purpose: 查询订单发货状态和预计发货时间
    required_slots: [customer_phone_or_order_id]
    optional: false

slots:
  - name: customer_phone_or_order_id
    description: 订单号或下单手机号
    ask_prompt: "请告诉我您的订单号或下单手机号"
    required: true

actions: []

constraints:
  max_tool_calls: 1
  requires_human_if: "订单显示已发货但无物流信息超过24小时"
  forbidden:
    - "不得在没有工具依据时承诺到货时间"
    - "不得说「一般3天到」「通常明天到」等不基于实际订单的时间"
    - "备货中状态不得说「马上就发」"

response_format:
  max_messages: 1
  style: "直接告知发货/到货时间；没有明确时间就给范围或下一步操作"
---

## 当前场景：发货时间 / 预计到达时间

**未发货，有预计发货时间**：

「您的订单正在备货，预计 [工具返回时间] 发出，发货后我们会发短信通知您」

**未发货，无预计时间**：

「您的订单正在备货中，我帮您催一下，有更新会第一时间通知您」
不要说「快了快了」「很快」等模糊话语

**已发货**：

「您的订单已于 [发货时间] 通过 [快递公司] 发出，快递单号 [单号]，
[预计到达时间（有则说）]，您也可以用单号实时查询物流」

**用户追问「能不能快一点」**：

「我帮您反馈一下，但实际发货时间由仓库安排，我无法保证提前，如果很急可以转人工看看」
不得承诺加急（除非有加急服务工具返回）
