---
skill_id: MEMBER.REGISTER
name: 会员注册
domain: MEMBER
description: 用户注册/开通本平台会员（新手引导；第三方平台账号注册不属于本技能）
risk_level: L2
priority: 60

triggers:
  intents:
    - MEMBER.REGISTER
  # 规则层确定性触发（语义层暂无训练样本）：「注册会员/开通会员/我要注册」；
  # 否定语境（不想开通会员）与第三方平台语境（注册抖音账号）不触发

required_tools:
  - tool_id: query_member_status
    purpose: 查询用户是否已注册（NBA 主动建议入口也用它做闭环判断）
    required_slots: []
    optional: true

slots:
  - name: phone
    description: 用于注册的手机号
    ask_prompt: "好的，我来帮您开通会员！请提供用于注册的手机号。"
    required: true
    type: string

tool_returns:
  - name: member_no
    from_tool: register_member

actions:
  - action_id: register_member
    description: 注册会员（写操作，经 ActionExecutor 唯一写入口）
    requires_confirmation: true
    confirmation_prompt: |
      将以手机号「{phone}」为您注册本平台会员，确认提交吗？
    rollback: false

constraints:
  max_tool_calls: 2
  requires_human_if:
    - "手机号已被其他账号注册（mock 环境不出现，真实系统交人工核实）"
  forbidden:
    - "不得跳过确认门直接注册（引导语境不豁免写操作红线）"
    - "不得虚构会员权益承诺（话术只说开通结果以短信为准）"
---

## 当前场景：会员注册（Stage 33，mock 版）

**流程**：收手机号 → 确认门（「确认」才提交）→ ActionExecutor 执行
`register_member` → 回执含受理单号（mock 返回确定性会员号）。

**入口**：
1. 用户主动：「注册会员 / 开通会员 / 我要注册」（规则层确定性触发）；
2. NBA 主动建议（START_ONBOARDING）：话术引导用户显式回复「注册」，
   不做弱确认解析——回复「注册」重新经规则层进入本技能。

**遗留**：验证码环节（需任务中途工具调用机制，随真实会员系统对接做）；
MCP 服务端 member 工具（当前 mock 兜底行为正确）。
