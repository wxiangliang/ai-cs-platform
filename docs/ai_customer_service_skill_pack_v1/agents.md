# Codex / 开发代理实施说明

## 目标

在不破坏现有意图识别、Meta-classifier、任务栈、确认门、RAG 与工具层的前提下，
增加可配置的 Playbook、商品顾问、推荐、活动和 Next Best Action。

## 不允许的实现

1. 不把 `START_ONBOARDING`、`MENTION_CAMPAIGN`、`CLOSE_DEAL` 加进用户意图 taxonomy。
2. 不让 LLM 自由选择写工具。
3. 不让 LLM 编造价格、库存、活动资格、折扣有效期或商品参数。
4. 不把 active_playbook 合并进 active_task 的 status 字段。
5. 不在 LangGraph 主图中为每个具体活动建立一个节点。
6. 不在退款、投诉、人工接管或确认门中插入营销话术。
7. 不因实现推荐而修改已有商品事实来源优先级。
8. 不用合成数据的高指标作为学习型策略上线依据。

## 建议实施方式

- Playbook 引擎使用纯决策函数：状态 + 输入事件 → 下一状态 + 动作请求。
- Tool 调用由现有 Skill Router/Tool Provider 执行。
- 写请求只生成 `ActionRequest`，交给现有确认门。
- NBA 第一版使用明确规则和 reason_codes。
- Response Planner 先组成结构化计划，再交给模板或 LLM 表达。
- 所有新表和字段提供迁移、宽容读取和回滚路径。
- 新功能默认关闭，测试覆盖关闭时语义等价。

## 必须先读取

1. `docs/architecture.md`
2. `docs/skill_contract.md`
3. `docs/priority_and_suppression.md`
4. 对应 `requirements/stage-*`
5. 对应 `skills/*/SKILL.md`

## 每次提交必须回答

- 修改了哪一个单一事实来源？
- 是否增加新的写入口？
- 是否可能覆盖用户当前明确诉求？
- 无 LLM、无活动服务、无推荐服务时如何降级？
- 决策证据落在哪里？
- 如何灰度与回退？
- 哪些测试证明没有破坏确认门和任务栈？
