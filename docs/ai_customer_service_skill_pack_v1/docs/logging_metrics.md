# 决策日志与指标

## 1. Playbook 日志

```text
playbook_code
playbook_version
instance_id
from_step
to_step
event
slot_delta
tool_request_names
status
reason_codes
latency_ms
```

## 2. NBA 日志

```text
candidate_actions
selected_action
selected_priority
suppressed_actions
suppression_reason_codes
frequency_cap_state
policy_version
shadow_prediction
```

## 3. 推荐日志

```text
request_constraints
recalled_product_ids
filtered_product_ids
filter_reasons
rank_features
rank_scores
displayed_product_ids
recommendation_reasons
user_response
```

## 4. Campaign 日志

```text
campaign_id
eligible
eligibility_reasons
suppressed
suppression_reasons
impression_count
last_impression_at
user_opt_out
```

## 5. 指标

- playbook_start_total
- playbook_complete_total
- playbook_abort_total
- playbook_step_latency
- product_discovery_completion_rate
- recommendation_accept_rate
- recommendation_hard_constraint_violation_total
- nba_candidate_total
- nba_selected_total
- nba_suppressed_total
- proactive_rejection_rate
- campaign_impression_total
- campaign_opt_out_total
- promotion_during_complaint_total（必须为 0）
- false_scarcity_violation_total（必须为 0）
