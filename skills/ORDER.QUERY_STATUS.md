---
skill_id: ORDER.QUERY_STATUS
name: 订单状态查询
domain: ORDER
description: 用户询问订单是否发货、订单状态、预计到货时间
risk_level: L1
priority: 70

triggers:
  intents:
    - ORDER.QUERY_STATUS
    - ORDER.QUERY_LOGISTICS       # 物流查询复用同一 Skill，区别在工具参数
  slot_required: false            # 先收集槽位，再调工具

required_tools:
  - tool_id: query_order
    purpose: 查询订单状态、发货时间、快递单号
    required_slots: [customer_phone_or_order_id]
    optional: false               # 无工具结果不能瞎回复

slots:
  - name: customer_phone_or_order_id
    description: 下单手机号或订单号
    ask_prompt: "请告诉我您下单时使用的手机号，或者订单编号，我帮您查询"
    required: true
    type: string

actions: []                       # 只读，无写操作

constraints:
  max_tool_calls: 1
  requires_human_if: "工具返回订单状态为异常或未找到订单"
  forbidden:
    - "不得在工具返回前猜测发货时间"
    - "不得承诺具体到货日期（除非工具明确返回）"

response_format:
  max_messages: 2
  style: "简洁告知结果，结果不明时给出下一步"
---

## 当前场景：订单查询

**核心原则**：工具返回什么就告诉用户什么，不扩展、不猜测。

**按工具返回状态回复**：

- 已发货：告知快递公司、单号、预计时间（如工具有返回）；没有预计时间就不说
- 未发货：告知预计发货时间（如工具有返回）；没有就说「会尽快安排，可以留意短信通知」
- 订单不存在/未找到：请用户确认手机号或订单号是否正确，核实后仍无结果则转人工
- 已签收：确认签收状态，如用户说「没收到」则转售后流程（意图切换到 AFTERSALE）

**禁止**：
- 工具未返回前不得说「应该明天到」「一般3天」等猜测性时间
- 不得透露其他客户的订单信息
