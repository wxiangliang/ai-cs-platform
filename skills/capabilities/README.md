# skills/capabilities/ —— 能力规格（不被 Skill Loader 加载）

> 与上层 `skills/*.md`（意图技能，Loader 启动加载）的区别：这里是
> **能力/策略的设计规格**——系统主动动作、策略引擎、长期上下文这类
> 「不是用户意图」的东西（红线：系统动作≠用户意图，做成意图会污染
> 分类器）。Loader 的 glob 非递归，本目录天然不进注册表。

## 落点状态（每个规格实现在哪，详表见 `../README.md` 第 3 节）

| 规格 | 状态 | 实现落点 |
|---|---|---|
| `product_discovery` / `product_recommendation` | ✅ | 意图技能 `../PRODUCT.RECOMMEND.md`（Stage 32） |
| `purchase_assist` | ✅ 对比部分 | 意图技能 `../PRODUCT.COMPARE.md`；其余推迟 |
| `new_user_onboarding` | ✅ | 意图技能 `../MEMBER.REGISTER.md`（Stage 33）；验证码环节遗留 |
| `promotion_guide` | ✅ | `configs/campaigns.json` + NBA `MENTION_CAMPAIGN`（Stage 31） |
| `next_best_action` | ✅ | `app/chat/proactive/nba.py`（Stage 31/33） |
| `response_planner` | ✅ 简化 | `save_turn` 回复定稿点收口 |
| `customer_journey` | ✅ | `customer_journey` 表 + `app/services/journey_service.py`（Stage 38） |

## 约定

1. 新能力规格先放这里评审，实现时按三轴纪律归位（用户意图 → 上层意图
   技能 md；策略/机制 → 代码+configs），实现后回填上表落点；
2. `*.playbook.yaml` 是规格附带的流程声明（参考件，运行时以任务状态机
   为准——不建第二套引擎的论证见 stage-32 需求 1.3 节）；
3. 本目录文件**不需要** YAML front-matter（不走 loader 校验）。

> 来源：`docs/ai_customer_service_skill_pack_v1/`（原包保留需求/骨架/
> 测试样例等参考件，其 skills/ 规格 2026-08-06 迁至此处统一管理）。
