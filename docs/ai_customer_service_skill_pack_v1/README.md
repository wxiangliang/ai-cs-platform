# AI 聊天客服主动服务 Skill Pack v1

> 适配现有架构：FastAPI + LangGraph、规则优先、SetFit 意图识别、任务状态机、
> Meta-classifier、确认门、Skill Router、Tool Registry、PostgreSQL 决策日志。
>
> 本包解决的不是“再加几个意图”，而是在现有响应式客服内核上增加：
> **Playbook、多轮引导、客户旅程、Next Best Action、商品推荐、活动策略和回复规划**。

## 1. 包含能力

| 能力 | 目录 | 核心目标 |
|---|---|---|
| 新手注册引导 | `skills/new_user_onboarding/` | 多轮完成注册、验证和基础资料引导 |
| 选品顾问 | `skills/product_discovery/` | 把模糊购买需求转成结构化约束 |
| 商品推荐 | `skills/product_recommendation/` | 硬约束过滤、候选排序、可解释推荐 |
| 活动引导 | `skills/promotion_guide/` | 资格、有效期、频控和抑制条件下提示活动 |
| 促成交辅助 | `skills/purchase_assist/` | 回答购买疑虑、比较候选、引导下一步，不强推 |
| Next Best Action | `skills/next_best_action/` | 决定本轮是否适合主动引导，以及引导什么 |
| 回复规划 | `skills/response_planner/` | 组合主任务结果、必要引导和可选建议 |
| 客户旅程 | `skills/customer_journey/` | 管理长期阶段，不与 active_task 混在一起 |

## 2. 三条独立决策轴

```text
Conversation Mode Gate
    判断 SOCIAL_ONLY / TASK_ONLY / MIXED / OOS

Intent + Task State
    判断用户要什么，以及当前任务如何续接、切换和执行

Business Policy / Next Best Action
    判断完成当前响应后，是否适合主动引导
```

禁止把系统主动动作训练成用户意图。例如：

```text
用户说“帮我推荐一款”       → PRODUCT.SELECTION_HELP（用户意图）
系统发现用户尚未注册       → START_ONBOARDING（业务动作，不是意图）
系统提示符合条件的活动     → MENTION_CAMPAIGN（业务动作，不是意图）
系统建议继续比较两个商品   → OFFER_PRODUCT_COMPARE（业务动作，不是意图）
```

## 3. 推荐接入位置

```text
规则控制层
→ Conversation Mode Gate
→ 意图识别 / 槽位抽取
→ Meta-classifier / 任务状态机
→ 主 Skill 执行
→ Business Policy / Next Best Action
→ Response Planner
→ 回复生成
→ 决策日志
```

`SOCIAL_ONLY` 默认不触发营销动作；退款、投诉、写操作确认、负面情绪优先级高于
所有推荐和活动。

## 4. 实施顺序

1. 阅读 `docs/architecture.md` 和 `docs/skill_contract.md`。
2. 先实现 `requirements/stage-31-playbook-engine.md`。
3. 落地 `new_user_onboarding`，验证 Playbook 通用能力。
4. 落地 `product_discovery` 与 `product_recommendation`。
5. 再增加 `next_best_action` 和 `promotion_guide`。
6. 最后启用 `purchase_assist`，并以影子模式观察主动动作。
7. 每个阶段都执行 `MIGRATION_CHECKLIST.md`。

## 5. 目录说明

- `skills/`：给产品、架构评审和开发看的完整 Skill 规格。
- `requirements/`：可直接作为后续 Stage 实施需求。
- `code_skeleton/`：与现有 `app/chat/` 风格对齐的 Python 骨架。
- `configs/`：Skill 注册表和活动配置示例。
- `tests/`：必须补齐的关键单元测试样例。
- `agents.md`：给 Codex/开发代理的执行约束。
- `MANIFEST.sha256`：包内文件校验清单。

## 6. 重要红线

1. 主动经营动作永远不能压过用户明确诉求。
2. 投诉、退款、负面情绪、确认门、人工接管期间禁止营销。
3. 价格、库存、活动资格必须来自结构化工具，不允许 LLM 编造。
4. 推荐必须先做硬约束过滤，再做排序。
5. 用户拒绝推荐或活动后，必须进入冷却或永久退出。
6. 所有主动动作都必须落 `reason_codes`，支持回放和审核。
7. 第一版 NBA 使用规则，不直接训练端到端策略模型。
