---
skill_id: META.ABORT
name: 用户撤回/放弃当前任务
domain: META
description: 用户主动说不要了、算了、取消，终止当前正在进行的任务
risk_level: L0
priority: 10

triggers:
  intents:
    - META.ABORT
  # 注意：META.ABORT 是最高优先级，在 Resolver R1 处理，优先于任何 active 任务

required_tools: []

slots: []

actions:
  - action_id: abort_current_task
    description: 标记当前任务为 ABORTED 状态
    requires_confirmation: false  # 用户已明确表示放弃，不再追问
    rollback: false

constraints:
  forbidden:
    - "不得追问「您确定不要了吗」（用户已明确，追问显得烦）"
    - "不得继续推进刚才的任务"
    - "不得让用户感觉撤回很麻烦"

response_format:
  max_messages: 1
  style: "简洁接受，顺势轻转"
---

## 当前场景：用户撤回

**识别信号**：

「算了」「不退了」「不用了」「取消吧」「先不管了」「我再想想」（后者可能是延迟，需结合语境判断）

**正确响应**：

「好的，没问题，有需要随时告诉我」—— 接受，不挽留，不追问。

**和「继续当前任务」的区别**：

- 用户说「等一下」「我查一下」→ 不是 ABORT，是暂停，任务维持 SUSPENDED
- 用户说「算了不退了」→ 是 ABORT，任务变为 ABORTED 终态
- 用户说「那改成换货吧」→ 不是 ABORT，是意图切换（AFTERSALE.EXCHANGE）

**ABORT 是终态**：

任务一旦 ABORTED，不会因用户后续「那还是退吧」而自动恢复，
需要重新走退款流程（会继承已知的 order_id 等槽位，但需要重新确认）。

**和 META.CORRECTION（纠正）的区别**：

- 「不是退款，我想换货」→ META.CORRECTION，纠正意图，旧任务结束但槽位可继承
- 「算了不弄了」→ META.ABORT，用户放弃，不切换到新意图
