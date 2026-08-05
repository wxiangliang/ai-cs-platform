# 接入检查清单

## 架构

- [ ] 用户意图与系统主动动作使用不同枚举。
- [ ] `active_task`、`active_playbook`、`journey_stage` 分开持久化。
- [ ] Playbook 只编排 Skill/Tool，不绕过 Tool Registry。
- [ ] 所有写操作仍经确认门和 ActionExecutor。
- [ ] NBA 位于主任务决策之后，不得覆盖主任务结果。
- [ ] Response Planner 可以丢弃低优先级可选动作。

## 安全与体验

- [ ] 投诉、退款、负面情绪、人工接管期间抑制营销。
- [ ] 用户拒绝后写入冷却或 opt-out。
- [ ] 活动次数和冷却按 tenant + customer + campaign 记录。
- [ ] 推荐前校验库存、地区可售、预算和规格硬约束。
- [ ] LLM 不得生成结构化事实。
- [ ] 回复中主任务结果必须先于推荐或活动。

## 可观测

- [ ] 每次 Playbook 流转记录 from_step / to_step / reason_codes。
- [ ] 每个 NBA 候选记录入选和被抑制原因。
- [ ] 推荐记录召回候选、过滤原因、排序分解和最终展示。
- [ ] 活动记录 eligibility、frequency cap 和 suppression。
- [ ] 所有主动动作记录用户接受、拒绝、忽略和后续转化。

## 发布

- [ ] 默认关闭新开关。
- [ ] 影子模式至少覆盖真实流量样本。
- [ ] 灰度按 tenant 或确定性 hash 分桶。
- [ ] 支持一键回退到仅响应式客服。
- [ ] 回退不破坏已有 active_task 和确认门。
