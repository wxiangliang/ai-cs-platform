# RESPONSE_PLANNER Skill

## 目标

将主任务结果、Playbook 指令和可选 NBA 动作组织为结构化回复计划。

## 输入优先级

```text
SAFETY
CONFIRMATION
TASK_RESULT
REQUIRED_GUIDANCE
USER_REQUESTED_RECOMMENDATION
OPTIONAL_NBA
SOCIAL_ACK
```

## 输出

```json
{
  "parts": [
    {
      "type": "TASK_RESULT",
      "required": true,
      "facts": {}
    },
    {
      "type": "ASK_SLOT",
      "required": true,
      "payload": {
        "slot": "budget_range"
      }
    },
    {
      "type": "OPTIONAL_SUGGESTION",
      "required": false,
      "payload": {
        "action": "OFFER_PRODUCT_COMPARE"
      }
    }
  ],
  "dropped_parts": [],
  "reason_codes": []
}
```

## 规则

- 主任务结果必须先出现；
- 一轮最多一个可选主动建议；
- 有确认门时不插入可选建议；
- 负面情绪时只保留任务和必要安抚；
- 回复过长时先删除低优先级部分；
- LLM 只根据 plan 表达，不重新决定动作。
