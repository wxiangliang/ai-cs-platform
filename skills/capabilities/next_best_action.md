---
capability_id: next_best_action
status: implemented
implemented_by:
  modules: [app/chat/proactive/nba.py]
  metrics: [proactive_actions_total]
---

# NEXT_BEST_ACTION Policy Skill

## 目标

在主任务决策之后，从零个或多个候选动作中选择最多一个主动动作。

## 输入

```text
conversation_mode
intent_result
active_task
active_playbook
journey_stage
sentiment
customer_preferences
recent_product_context
campaign_candidates
frequency_cap_state
tool_health
```

## 输出动作

```text
NO_PROACTIVE_ACTION
START_ONBOARDING
START_PRODUCT_DISCOVERY
OFFER_PRODUCT_COMPARE
OFFER_PURCHASE_HELP
MENTION_CAMPAIGN
RESUME_PLAYBOOK_HINT
```

## 第一版决策顺序

```text
全局抑制
→ 当前 Playbook 必要动作
→ 用户主动请求对应动作
→ 与主任务强相关的辅助动作
→ Journey 相关动作
→ Campaign 候选
→ NO_PROACTIVE_ACTION
```

## 约束

- 最多选择一个主动动作；
- 可选动作不能阻塞主回复；
- 用户拒绝后写频控状态；
- 同一会话连续两轮不得重复同一主动动作；
- 投诉/退款/确认门直接返回 NO_PROACTIVE_ACTION；
- 必须输出 reason_codes。

## 影子模式

首版：

```text
计算候选和选择结果
→ 落日志
→ 不进入 Response Planner
```

人工审核分歧和打扰风险后，再逐动作灰度。
