---
skill_id: META.TRANSFER_HUMAN
name: 转人工
domain: META
description: 用户主动要求转人工，或系统判断需要人工介入
risk_level: L0
priority: 30

triggers:
  intents:
    - META.TRANSFER_HUMAN
  # 也可由其他 Skill 的 requires_human_if 条件触发，不经意图分类

required_tools:
  - tool_id: transfer_to_human
    purpose: 将对话转接给人工客服
    required_slots: []
    optional: false

slots: []                         # 不需要额外收集，直接执行

actions:
  - action_id: transfer_to_human
    description: 转接人工客服
    requires_confirmation: false  # 不需要确认，用户已明确表示或系统判断必须转
    rollback: false

constraints:
  max_tool_calls: 1
  requires_human_if: []           # 本身就是转人工，无需再嵌套
  forbidden:
    - "不得让用户反复解释问题（转接时带上当前对话摘要）"
    - "不得说「系统繁忙」「人工不可用」等敷衍话术（除非工具返回确实不可用）"

response_format:
  max_messages: 1
  style: "一句话告知正在转接，不拖拉"
---

## 当前场景：转人工

**主动转接**（用户要求）：

「好的，我帮您转接人工客服，请稍等」—— 简洁，不加解释，不反问「您确定要转吗」。

**被动转接**（系统判断）：

当其他 Skill 的 `requires_human_if` 条件触发时（如退款金额异常、售后资产纠纷），
先告知原因再转：「这个情况需要人工同事处理，我帮您转接一下」。

**人工不可用时**（工具返回不可转）：

「目前人工客服在高峰期，预计等待 X 分钟，您是否需要留下联系方式由同事回拨？」
—— 给出替代方案，不能只说「暂时无法转接」就结束。

**带上下文转接**：

转接时系统自动携带本轮对话摘要（意图、已收集槽位、用户诉求），
LLM 在转接消息里简要说明：「用户需要处理 [意图描述]，相关信息：[已知信息摘要]」。
