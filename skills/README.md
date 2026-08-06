# skills/ —— 运行时技能声明目录

> **这是运行时配置不是文档**：Skill Loader 启动时读本目录**根层** `*.md`
> 的 YAML front-matter（glob 非递归，README 与子目录不加载），把能力声明
> （工具/动作/约束/风险等级/优先级）合并进代码注册表
> （`app/chat/skills/registry.py`）。字段规范见
> `docs/chat/skills_design/00_skill_schema.md`；意图码单一事实来源是
> `docs/chat/intent_taxonomy.md`。
>
> 子目录 `capabilities/`：**能力规格**（策略轴/机制类，非意图技能，
> 不被加载）——设计规格与落点对照见 `capabilities/README.md`。

## 1. 双源分工（谁管什么）

| 来源 | 管什么 |
|---|---|
| 本目录 md（front-matter） | required_tools / actions（含确认话术）/ constraints / risk_level / priority / rag_fallback / prompt_fragment（body 进 LLM 提示） |
| 代码注册表 registry.py | 运行时模板（collect/confirm/confirmed/answer）与 required_slots（与 SlotExtractor 能力对齐） |

启动校验（`SKILL_LOADER_STRICT`）：skill_id 必须在意图目录、domain 在
10 域枚举、缺 risk_level/priority 拒绝启动；文件名 = `skill_id`.md。

## 2. 与注册表的双向覆盖（测试锁定，改动先看这）

- **一一对应**：35 个 md ↔ 注册表 33 意图 + 2 个例外（Stage 39 增
  APPOINTMENT.BOOK/CANCEL）；
- **只有 md 没有注册表**（预期）：`META.SLOT_ONLY` / `META.CORRECTION`
  ——上下文控制意图由规则层+状态机处理，不需要独立回复模板；
- 新增意图三步：taxonomy 注册 → registry 加条目（模板/槽位）→
  本目录加同名 md（能力声明）。缺任何一步守护测试报错。

## 3. Skill Pack 八能力的落点对照（2026-08-05 统一说明；规格 2026-08-06 迁入 `capabilities/`）

`capabilities/` 里的 8 个规格（原 skill pack skills/）是**能力规格**，
不是本目录根层意义上的意图技能。实现时按三轴纪律归位——
**只有「用户意图」进本目录**，系统主动动作与回复组装是策略轴/机制，
刻意不做成意图（红线：系统动作≠用户意图，训练成意图会污染分类器）：

| 包能力 | 落点 | 形态 |
|---|---|---|
| product_discovery + product_recommendation | 本目录 `PRODUCT.RECOMMEND.md`（Stage 32 实做） | ✅ 意图技能 |
| purchase_assist（对比部分） | 本目录 `PRODUCT.COMPARE.md`（Stage 32） | ✅ 意图技能 |
| new_user_onboarding | 本目录 `MEMBER.REGISTER.md`（Stage 33） | ✅ 意图技能 |
| promotion_guide | `configs/campaigns.json` + NBA `MENTION_CAMPAIGN`（Stage 31） | 策略轴配置，非意图 |
| next_best_action | `app/chat/proactive/nba.py` 规则策略（Stage 31/33） | 策略轴代码，非意图 |
| response_planner | `save_turn` 回复定稿点收口（主回复+至多一条建议追加） | 机制，非意图 |
| customer_journey | `customer_journey` 表 + `app/services/journey_service.py`（Stage 38：save_turn 规则推导，NBA 活动 `eligible_journey_stages` 门控消费） | 长期上下文，非意图 |

原包目录保留为设计参考；至此八能力全部归位（2026-08-06）。

## 4. 多语言

registry 中文模板 = zh 源；非默认语言在语言包查
`skill.<skill_id>.<template_key>` 覆盖（Stage 19），本目录 md 可加
`templates.<lang>` 声明（当前仅示范）。
