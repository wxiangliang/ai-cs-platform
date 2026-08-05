# 主动服务架构

## 1. 分层

```text
用户输入
  ↓
规则与护栏
  ↓
Conversation Mode Gate
  ↓
Intent Understanding
  ↓
Task Governance
  ↓
Primary Skill
  ↓
Business Policy / Next Best Action
  ↓
Response Planner
  ↓
Response Composer
```

### Understanding Layer

负责：

- 对话模式；
- 业务意图；
- 槽位；
- 实体；
- 情绪；
- 显式切换、拒绝和确认信号。

### Task Layer

负责：

- active_task；
- suspended_tasks；
- 确认门；
- 补槽；
- 任务切换；
- 任务恢复；
- 写操作执行资格。

### Playbook Layer

负责多轮业务目标：

- 新手注册；
- 选品；
- 商品比较；
- 购买辅助；
- 活动使用引导。

Playbook 不直接决定“用户是什么意图”，也不直接绕过状态机执行写操作。

### Business Policy Layer

负责决定是否增加可选引导：

```text
候选动作生成
→ 优先级排序
→ 抑制规则
→ 频控
→ 选择零个或一个主动动作
```

### Response Layer

按优先级组合：

1. 安全与确认；
2. 主任务结果；
3. 必要的下一步引导；
4. 用户主动要求的推荐；
5. 系统主动但可选的建议；
6. 社交回应。

## 2. 状态边界

```json
{
  "active_task": {
    "task_id": "t1",
    "intent": "PRODUCT.SELECTION_HELP",
    "status": "COLLECTING"
  },
  "active_playbook": {
    "instance_id": "pb1",
    "playbook_code": "PRODUCT_DISCOVERY",
    "step": "COLLECT_BUDGET",
    "status": "RUNNING"
  },
  "journey": {
    "stage": "CONSIDERING",
    "confidence": 0.82
  }
}
```

- Task 是本轮用户诉求。
- Playbook 是实现目标的多步方法。
- Journey 是跨会话长期阶段。

## 3. 主动动作接管原则

业务策略只能：

- 补充主任务所需引导；
- 在主任务完成后提出一个可选下一步；
- 在无冲突时启动适合的 Playbook；
- 返回 `NO_PROACTIVE_ACTION`。

业务策略不能：

- 改写已识别用户意图；
- 清空或切换任务栈；
- 执行写操作；
- 跳过确认门；
- 覆盖投诉、退款或人工接管。
