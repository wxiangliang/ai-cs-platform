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

## 约定（2026-08-06 活契约化：规格不是被动文档）

1. 每个规格文件必须带 front-matter **落点锚点声明**：

   ```yaml
   ---
   capability_id: <与文件名一致>
   status: implemented | partial | deferred
   implemented_by:            # implemented/partial 至少声明一个锚点
     modules: [app/...py]     # 文件必须存在
     intents: [PRODUCT.RECOMMEND]   # 必须在意图目录
     configs: [configs/xxx.example.json]
     events: [APPOINTMENT_REMINDER] # 必须在事件白名单
     metrics: [proactive_actions_total]
   ---
   ```

2. **启动校验**（`app/chat/skills/capability_loader.py`，随 Skill Loader
   执行）：锚点漂移（模块被删/意图改名/配置移位）默认告警、
   `SKILL_LOADER_STRICT=true` 拒绝启动；CI 由
   `tests/skills/test_capability_specs.py` 硬断言零漂移 + 状态快照锁定；
3. 新能力规格先放这里评审（status: deferred 免锚点），实现时按三轴纪律
   归位（用户意图 → 上层意图技能 md；策略/机制 → 代码+configs），
   实现后改 status 并回填锚点；
4. `*.playbook.yaml` 是规格附带的流程声明（参考件，运行时以任务状态机
   为准——不建第二套引擎的论证见 stage-32 需求 1.3 节）；其
   `entry_intents` 必须在意图目录（校验覆盖，防规格与实现脱节的意图名）。

> 来源：`docs/ai_customer_service_skill_pack_v1/`（原包保留需求/骨架/
> 测试样例等参考件，其 skills/ 规格 2026-08-06 迁至此处统一管理）。
