---
skill_id: META.CORRECTION
name: 用户纠正
domain: META
description: 用户纠正系统理解错误，表示「不是这个意思」「我说的是别的」
risk_level: L0
priority: 20

triggers:
  intents:
    - META.CORRECTION

required_tools: []

slots: []

actions:
  - action_id: correct_active_intent
    description: 将当前任务标记为已纠正，继承已有槽位，切换到新意图
    requires_confirmation: false
    rollback: false

constraints:
  forbidden:
    - "不得坚持原来的理解（用户已明确纠正）"
    - "不得重新问已经回答过的信息（纠正后继承有效槽位）"
    - "不得因为纠正而清空所有上下文"

response_format:
  max_messages: 1
  style: "简短确认纠正，立刻进入正确的处理流程"
---

## 当前场景：用户纠正

**META.CORRECTION 的识别信号**：

「不是，我的意思是...」「你理解错了」「我没说要退款，我说的是换货」
「不是订单问题，我问的是商品」「我不是说这个」

**和 META.ABORT 的区别**：

- META.CORRECTION：用户纠正理解，继续推进（换了一个意图）
- META.ABORT：用户放弃当前需求，不再处理

**处理规则**：

1. 立刻接受纠正，不辩解：「明白了，我重新理解一下」
2. 从用户的纠正话语里识别新的真实意图（意图重新分类）
3. 继承原任务中仍然有效的槽位（如 order_id 仍然适用于新意图）
4. 丢弃不再适用的槽位（如 refund_reason 在换货意图里无效）
5. 继续推进新意图

**示例**：

用户之前：「我要退款」→ 系统开始走 AFTERSALE.REFUND，收集了 order_id  
用户纠正：「不是，我想换货，不是退款」  
→ META.CORRECTION → 新意图 AFTERSALE.EXCHANGE  
→ 继承：order_id ✓  
→ 丢弃：refund_reason ✗  
→ 回复：「明白，您是要换货，我帮您继续处理。请问想换成哪个规格？」  
（order_id 已知，不再问）

**不要做的**：

「好的，那我重新开始。请告诉我您的订单号」← 错，已经有 order_id 了
