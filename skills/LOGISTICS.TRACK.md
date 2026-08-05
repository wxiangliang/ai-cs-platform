---
skill_id: LOGISTICS.TRACK
name: 物流追踪
domain: LOGISTICS
description: 用户询问包裹到哪了、物流最新进展、快递当前位置
risk_level: L1
priority: 70

triggers:
  intents:
    - LOGISTICS.TRACK

required_tools:
  - tool_id: query_order
    purpose: 获取快递单号和快递公司
    required_slots: [customer_phone_or_order_id]
    optional: false
  - tool_id: query_logistics_track
    purpose: 查询实时物流轨迹
    required_slots: [tracking_number]
    optional: false

slots:
  - name: customer_phone_or_order_id
    description: 订单号或下单手机号
    ask_prompt: "请告诉我您的订单号或下单手机号，我帮您查快递"
    required: true

actions: []

constraints:
  max_tool_calls: 2              # 先查订单拿单号，再查物流轨迹
  requires_human_if:
    - "物流显示异常（超时未更新超过5天/已退回/海关扣押）"
    - "用户反馈已签收但未收到货"
  forbidden:
    - "不得在工具返回前猜测包裹位置"
    - "不得说「应该快到了」「明天应该能到」等无依据时间判断"
    - "签收但用户说没收到：绝对不能说「可能被人拿了」，转售后处理"

response_format:
  max_messages: 1
  style: "告知最新物流节点 + 预计到达时间（有则说，没有则不编）"
---

## 当前场景：物流追踪

**正常在途**：

「您的包裹目前在 [最新节点]，[快递公司] [快递单号]，预计 [到达时间] 到达」
（没有预计时间就不说，不要编）

**长时间无更新（超48小时无物流信息）**：

「您的包裹物流信息 [X] 天没有更新了，可能是中转延误，我帮您联系快递查一下，或者需要我转人工处理吗？」

**显示已签收但用户说没收到**：

这是售后场景，不在本 Skill 处理范围，切换到 AFTERSALE.COMPLAIN：
「显示已签收但您没收到，这需要走售后处理，我帮您核实一下订单详情」

**海关扣押/退回**：

「您的包裹目前显示 [异常状态]，这种情况需要人工处理，我帮您转接一下」
→ 触发 META.TRANSFER_HUMAN

**注意与 LOGISTICS.DELIVERY_TIME 的区别**：

- 本 Skill（TRACK）：关心「现在在哪」「到哪了」
- LOGISTICS.DELIVERY_TIME：关心「什么时候到」「多久发货」
- 实际对话中两者常混用，统一查订单+物流轨迹，在回复里同时覆盖即可
