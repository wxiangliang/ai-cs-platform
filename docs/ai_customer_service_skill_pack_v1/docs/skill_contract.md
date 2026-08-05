# Skill 与 Playbook 契约

## 1. Skill 定义

Skill 是完成某类用户目标的能力，可以包含多个步骤和工具，但必须具有稳定输入输出契约。

```python
class Skill:
    code: str
    risk_level: str
    required_tools: list[str]

    def can_start(context) -> EligibilityResult: ...
    def handle(context, event) -> SkillResult: ...
```

## 2. Playbook 定义

每个 Playbook 必须声明：

- `code`
- `version`
- `goal`
- `entry_conditions`
- `required_slots`
- `optional_slots`
- `steps`
- `tool_permissions`
- `write_actions`
- `completion_conditions`
- `abort_conditions`
- `timeout`
- `resume_policy`
- `priority`
- `suppression_conditions`
- `metrics`

## 3. 统一输出

```json
{
  "status": "RUNNING",
  "next_step": "COLLECT_BUDGET",
  "slot_updates": {},
  "tool_requests": [],
  "action_request": null,
  "response_directives": [
    {
      "type": "ASK_SLOT",
      "required": true,
      "payload": {
        "slot": "budget_range"
      }
    }
  ],
  "reason_codes": [
    "missing_required_slot"
  ]
}
```

## 4. 状态

```text
NOT_STARTED
RUNNING
WAITING_USER
WAITING_TOOL
WAITING_CONFIRMATION
SUSPENDED
COMPLETED
ABORTED
FAILED
EXPIRED
```

## 5. 事件

```text
USER_MESSAGE
SLOT_UPDATED
TOOL_SUCCEEDED
TOOL_FAILED
CONFIRMED
REJECTED
TIMEOUT
TASK_SWITCHED
TASK_RESUMED
USER_ABORTED
HUMAN_HANDOFF
```

## 6. 工具边界

Playbook 只能声明 ToolRequest：

```json
{
  "tool_name": "product_search",
  "arguments": {
    "category": "air_conditioner",
    "budget_max": 3000
  },
  "readonly": true
}
```

涉及写操作时只能生成 ActionRequest：

```json
{
  "action_code": "CREATE_ACCOUNT",
  "idempotency_key": "playbook_instance_id",
  "requires_confirmation": true,
  "arguments": {}
}
```

## 7. 版本

Playbook 实例必须记录启动时版本。已经运行中的实例不能静默切到新版本。
新版本仅影响新实例，或通过明确迁移函数升级。
