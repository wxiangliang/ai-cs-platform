---
skill_id: APPOINTMENT.BOOK
name: 服务预约
domain: APPOINTMENT
description: 预约上门服务（安装/维修/取件/退货上门/回访/演示）
risk_level: L2
priority: 60

triggers:
  intents:
    - APPOINTMENT.BOOK
  # 规则层确定性触发（「预约安装/帮我约个维修」）；被动式不触发

required_tools:
  - tool_id: query_appointment_slots
    purpose: 查询服务类型在期望时间附近的可用时间槽（会话式浏览记遗留 3）
    required_slots: [service_type]
    optional: true

slots:
  - name: service_type
    description: 服务类型（词表抽取：安装/维修/取件/退货上门/回访/演示）
    ask_prompt: "请问需要预约什么服务？（安装/维修/取件/退货上门/回访/演示）"
    required: true
    type: enum
    options: [安装, 维修, 取件, 退货上门, 回访, 演示]
  - name: appointment_time
    description: 期望时间（相对/绝对表达均可，沙盒按北京时间归一）
    ask_prompt: "您期望的上门时间是？例如「明天下午」或「8月10日 14:00」"
    required: true
    type: string
  - name: phone
    description: 联系电话（师傅上门前联系）
    ask_prompt: "请留一个联系电话，师傅上门前会提前与您确认。"
    required: true
    type: string

tool_returns:
  - name: appointment_no
    from_tool: create_appointment

actions:
  - action_id: create_appointment
    description: 创建预约（写操作，经 ActionExecutor 唯一写入口）
    requires_confirmation: true
    confirmation_prompt: |
      为您预约「{service_type}」服务，时间「{appointment_time}」（北京时间），
      联系电话「{phone}」，确认提交吗？
    rollback: true                # 可经 cancel_appointment 撤销

constraints:
  max_tool_calls: 2
  requires_human_if:
    - "时间槽满员且用户连续两次换时间仍冲突"
  forbidden:
    - "不得跳过确认门直接创建预约"
    - "不得虚构可用时间（可用性以工具返回为准）"
    - "时区必须明示（沙盒统一北京时间；真实系统 ISO8601 带时区）"
---

## 当前场景：服务预约（Stage 39，mock 资源池版）

**流程**：收 service_type/appointment_time/phone → 确认门 →
`create_appointment`（mock：按 (类型, 时间槽) 容量 3 原子扣减，满
→ SLOT_FULL 走执行失败分支转人工；过去时间 SLOT_EXPIRED 拒绝；同
(phone, 类型, 时间) 幂等返回同预约号）→ 回执含预约号。

**提醒/未到场**：真实系统推 `APPOINTMENT_REMINDER` / `APPOINTMENT_MISSED`
事件，经 Stage 36 事件通道（幂等/退订/静默时间全部继承）。

**遗留**：会话式时间槽浏览（任务中途工具机制）、改期原子接口、
真实调度系统时区契约（stage-39 需求第 5 节）。
