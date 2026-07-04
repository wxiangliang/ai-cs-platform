---
skill_id: META.UNKNOWN
name: 兜底与澄清
domain: META
description: 意图无法识别或超出业务范围时的兜底：引导式澄清，多次失败转人工（Stage 06 起先过一层 FAQ 检索再澄清）
risk_level: L0
priority: 100

triggers:
  intents:
    - META.UNKNOWN

required_tools: []
# Stage 06 起：澄清前先做一次 FAQ 层轻量检索（kb_retrieve, optional=true），
# 命中则按 FAQ.GENERAL 流程回答——长尾问题的第一道网。

slots: []

actions: []

constraints:
  max_tool_calls: 1
  requires_human_if:
    - "同一会话连续 2 轮落入 UNKNOWN（澄清失败，主动询问是否转人工）"
    - "用户明显情绪激动"
  forbidden:
    - "不得假装理解了用户的问题"
    - "不得连续追问超过一次（澄清一次不成即给选项或转人工）"

response_format:
  max_messages: 1
  style: "承认没听懂 + 给出可选方向"
---

## 当前场景：兜底与澄清

**首次 UNKNOWN（无进行中任务）**：

「抱歉我没有完全理解您的意思，您是想咨询 商品、订单、物流 还是 售后 方面的问题？」

**有进行中任务（COLLECTING/CONFIRMING）时落入 UNKNOWN**：

不丢弃任务；把本轮抽到的槽位并入任务后重新评估（现行状态机行为），
回复围绕缺失槽位或确认门继续推进，不重复「没听懂」话术。

**连续第 2 次 UNKNOWN**：

「我可能没帮上您，需要为您转接人工客服吗？」→ 用户同意则走 META.TRANSFER_HUMAN 流程。

**超范围问题**（问天气/写代码等非客服业务）：

「这个问题超出了我的服务范围～我可以帮您处理商品咨询、订单、物流、售后相关的问题。」
