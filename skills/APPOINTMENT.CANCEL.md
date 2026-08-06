---
skill_id: APPOINTMENT.CANCEL
name: 取消预约
domain: APPOINTMENT
description: 取消已有的服务预约（被动被取消的咨询不算）
risk_level: L2
priority: 60

triggers:
  intents:
    - APPOINTMENT.CANCEL
  # 规则层：「取消预约」先于 BOOK 判定；「预约被取消了」按状态咨询不触发

required_tools: []

slots:
  - name: appointment_no
    description: 预约号（AP 前缀，正则抽取）
    ask_prompt: "请提供要取消的预约号（AP 开头）。"
    required: true
    type: string

actions:
  - action_id: cancel_appointment
    description: 取消预约（写操作，经 ActionExecutor 唯一写入口）
    requires_confirmation: true
    confirmation_prompt: |
      您要取消预约「{appointment_no}」，确认吗？
    rollback: false               # 取消后需重新预约

constraints:
  max_tool_calls: 1
  forbidden:
    - "不得跳过确认门直接取消"
    - "预约号不存在时如实告知，不得假装取消成功"
---

## 当前场景：取消预约（Stage 39）

收预约号 → 确认门 → `cancel_appointment`（mock：号不存在返回
APPOINTMENT_NOT_FOUND 走执行失败分支）。改期 v1 = 取消 + 重新预约。
