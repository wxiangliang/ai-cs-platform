---
skill_id: META.DENY
name: 确认门否认
domain: META
description: 确认门下的否认应答（「不用/先不了」）与任务中途否定（「不是要办这个」）
risk_level: L0
priority: 95

triggers:
  intents:
    - META.DENY
  # 上下文敏感：仅 CONFIRMING（确认门应答）与 COLLECTING（任务中途否定，
  # Stage 23 方向纠偏）状态下由规则层判定；不进 SetFit/二判目录

required_tools: []
slots: []
actions: []

constraints:
  forbidden:
    - "否认后不得继续追问原任务槽位（任务已终止）"
    - "不得把含新诉求的否定吞成纯否认（残差判定见 Stage 23/26）"
---

## 当前场景：否认应答

**两个来源**（都由规则控制层短路，模型不参与）：

1. **确认门否认**（CONFIRMING）：「不用了/先不了」→ 终止当前写操作，
   任务不提交，回执「本次操作不会提交」；
2. **任务中途否定**（COLLECTING，Stage 23）：「不是要退货/不对」→
   仅终止当前任务（不清任务栈），回复重定向话术引导说出真实诉求；
   含数字/新诉求残差时不判否认（防吞新诉求，Stage 26 收紧）。

运行时模板以代码注册表为准（registry.py `meta_deny`）。
