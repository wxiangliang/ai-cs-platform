# Stage 31：通用 Playbook Engine

## 目标

在不改变现有意图与任务状态机语义的前提下，实现可配置、可暂停、可恢复、可回放的多轮业务流程。

## 范围

- Playbook Registry；
- Playbook Instance；
- step transition；
- slot state；
- ToolRequest / ActionRequest；
- 超时、放弃、失败、恢复；
- active_task 关联；
- 决策日志；
- 默认关闭。

## 非目标

- 不实现学习型策略；
- 不实现具体推荐算法；
- 不修改确认门；
- 不增加第二写入口。

## 验收

- 注册 mock Playbook 可完整跑通；
- 中途新任务可挂起并恢复；
- 同一写动作并发确认仍只执行一次；
- 无 LLM 可运行；
- 关闭开关时全量回归语义等价；
- Playbook 每步均可回放。
