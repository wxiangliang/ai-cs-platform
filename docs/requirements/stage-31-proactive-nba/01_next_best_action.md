# Stage 31 需求：主动服务地基（Next Best Action + 活动引导）

> 来源：`docs/ai_customer_service_skill_pack_v1/`（外部 Skill Pack，8 能力
> 5 阶段的完整规划——**保留原包作设计参考，本文档是结合本仓库场景的取舍版**）。
> 本 Stage 只落包中「NBA 规则策略 + 活动引导 + 回复追加」的最小闭环，
> 其余能力的推迟理由见第 2 节。同批完成：**Skill 声明目录迁仓库根 `skills/`**。

---

## 1. 定位：第三条决策轴

前两条轴已就位：模式轴（Stage 30 Mode Gate：这句是闲聊还是业务）、
任务操作轴（Stage 26/27：对当前任务续接/切换/二判）。本 Stage 补第三条：

```text
Business Policy / Next Best Action（业务策略轴）：
  主任务处理完成后，本轮是否适合追加**至多一个**主动动作？追加什么？
```

核心纪律（包 README 红线，全部采纳）：**系统主动动作不是用户意图**——
「系统提示符合条件的活动」是业务动作 `MENTION_CAMPAIGN`，永远不进意图
taxonomy、不进 SetFit 训练集、不进 Meta 六分类（三轴各自独立）。

## 2. 对 Skill Pack 的取舍（8 能力 → v1 落 3 件）

| 包能力 | 决策 | 理由 |
|---|---|---|
| next_best_action | **v1 实现（规则版）** | 包红线 7 自己也要求第一版用规则不训模型；影子先行（包 stage-33 第一阶段） |
| promotion_guide（活动引导） | **v1 实现** | 配置驱动+资格+频控+冷却，零模型零 migration，是 NBA 第一个可见动作 |
| response_planner | **v1 简化实现** | 不建独立 planner 模块——本仓库 save_turn 已是回复组装点（续办提示/工单信息先例），「主回复+至多一条建议追加」在此收口即可 |
| new_user_onboarding | 推迟 | 本沙盒无真实注册/验证体系，Playbook 空转；真实用户体系接入后再做 |
| product_discovery / purchase_assist | 推迟 | 需新增 `PRODUCT.SELECTION_HELP` 意图（taxonomy 注册+每类≥300 训练样本+重训，README 第 5 节流程）——另立 stage 走完整意图新增流程 |
| product_recommendation | 部分已有 | `product_answer` 已做硬约束式候选列举与「无命中宁缺勿编」；排序/可解释推荐随 discovery 一起做 |
| customer_journey | 推迟 | 长期旅程阶段需要持久用户画像与真实流量；v1 相关性用「当前意图」近似（包相关性约束第 3 节的第一项） |
| playbook_engine | 推迟 | 现有任务状态机+skill 补槽已覆盖「多轮收集」主干；等 discovery/onboarding 真需要跨任务引导流程时再引入，避免两套多轮机制并存 |

## 3. 架构与接入位置

```text
…主链路不动…
save_turn（回复定稿点）：
  reply 定稿前 → decide_proactive(state)
    ├─ 全局抑制检查（第 4 节，任一命中 → NO_ACTION + suppressed_by）
    ├─ 拒绝检测（上轮曾展示 + 本轮拒绝语 → 冷却，本轮也不展示）
    ├─ 活动池遍历：启用+有效期+意图相关性+频控（Redis）
    └─ 产出 {action, campaign_id, reason_codes, applied}
  PROACTIVE_APPLY=true 且选中 → reply += 活动提示（i18n 模板）
  无论是否追加 → 决策证据落 decision_log.graph_trace_json.proactive（影子可回放）
```

- **零 migration**：频控/冷却/退出全走 Redis（键含 tenant/user/campaign），
  决策证据进既有 graph_trace_json（guardrail/meta_shadow 同模式）；
- **fail-closed**：Redis 故障 → 抑制不展示（营销宁可少发不能超发——与
  限流 fail-open 方向相反，方向由业务代价决定）；配置缺失/损坏 → 无活动池；
- 双开关：`PROACTIVE_ENABLED`（计算+落日志=影子）→ `PROACTIVE_APPLY`
  （真实追加进回复）。**默认双关=零回归**；一键关闭回纯响应式客服。

## 4. 全局抑制矩阵（包 priority_and_suppression 采纳，映射到本仓库信号）

任一命中强制 `NO_ACTION`（顺序即检查序，reason code 落日志）：

| 抑制条件 | 本仓库信号 | code |
|---|---|---|
| 护栏拦截轮 | `state.blocked` | `blocked` |
| 人工接管中/静默轮 | `handoff_silent` / 建单轮 | `handoff` |
| CSAT 询问/捕获轮 | `csat_capture` / csat_pending | `csat` |
| 主任务未完成 | `status != DONE`（NEEDS_SLOT/NEEDS_CONFIRM/EXECUTING/FALLBACK 全含——确认门轮天然抑制） | `not_done` |
| 负面情绪 | `emotion == negative`（Stage 14 flag） | `negative_emotion` |
| 高风险流程 | 意图域 AFTERSALE/PAYMENT 或 ORDER.CANCEL（退款/投诉/换修/支付/取消） | `high_risk_flow` |
| 纯闲聊轮 | 来源 `MODE_SOCIAL` 或 mode_gate 判 SOCIAL_ONLY | `social_only` |
| 用户拒绝冷却/退出 | Redis 冷却键 / opt-out 集合 | `rejected_cooldown` / `opted_out` |
| 频控达上限 | 会话内已展示 / 客户×活动次数达标 | `session_cap` / `campaign_cap` |
| 无相关活动 | 活动池空 / 意图相关性不匹配 / 过期未启用 | `no_campaign` |

相关性约束（防「有活动就推」）：活动配置声明 `eligible_intents`
（意图码或 `DOMAIN.` 前缀），只在**本轮完成的意图**匹配时候选——
「查价格完成后提相关满减」允许，「退款会话里推满减」结构性到不了这步。

## 5. 活动配置（配置驱动，运营改 JSON 不改代码）

`CAMPAIGN_CONFIG_PATH`（默认 `configs/campaigns.json`，示例
`configs/campaigns.example.json`；mtime 缓存自动重载，同 experiments 模式）：

```jsonc
[{
  "campaign_id": "summer_2026_ac",
  "enabled": true,
  "valid_from": "2026-08-01T00:00:00+08:00",
  "valid_to": "2026-08-31T23:59:59+08:00",
  "eligible_intents": ["PRODUCT.", "FAQ.GENERAL"],   // 意图码或域前缀
  "hook": "您咨询的空调品类正在参加夏季满减：满 3000 减 200（仅限指定型号，不可与其他折扣叠加）",
  "max_per_customer": 2                                // 该活动对同一客户最多展示次数
}]
```

红线（包红线 3 采纳）：hook 是运营写死的确定性文案（含必要披露），
**不经 LLM 生成/改写**——活动资格、门槛、金额没有编造空间。

## 6. 频控与拒绝（Redis，全 fail-closed）

```text
proactive:sess:{tenant}:{session}            会话内展示数（TTL 1 天，上限 PROACTIVE_SESSION_MAX=1）
proactive:imp:{tenant}:{user}:{campaign}     客户×活动展示数（TTL 30 天，上限 max_per_customer）
proactive:last:{tenant}:{session}            上轮展示标记（TTL 10 分钟，拒绝检测窗口）
proactive:cool:{tenant}:{user}               拒绝冷却（TTL PROACTIVE_REJECT_COOLDOWN_HOURS=48h）
proactive:optout:{tenant}:{user}             永久退出（无 TTL；「以后都别推」类强拒绝）
```

拒绝检测（v1 规则）：上轮展示标记存在 + 本轮文本命中拒绝语
（不用推荐/不需要/别推了/不感兴趣…）→ 记冷却；含「以后/都别/永远」→ opt-out。
弱信号（没接话）不算拒绝，自然被会话频控盖住。

## 7. 观测

- 指标 `proactive_actions_total{action, outcome}`（outcome=
  applied/shadow/suppressed，多租户不进 label）；
- 决策日志 `graph_trace_json.proactive` = {action, campaign_id,
  reason_codes, applied}——影子期分析「本会补发什么」，验收
  「投诉/退款/确认门营销触发数为 0」直接 SQL 可查（包 stage-33 门禁）。

## 8. 验收标准（含包 stage-33 门禁）

1. 默认双关零回归（全量测试不动）；
2. 抑制矩阵逐条测试锁定：退款 COLLECTING 推配件=抑制、投诉中满减=抑制、
   SOCIAL_ONLY 无关联活动=抑制、确认门轮=抑制（包冲突示例表全覆盖）；
3. 每次决策输出 reason_codes 并落日志；
4. 拒绝后冷却生效、强拒绝 opt-out、频控达标即抑制；
5. 影子模式（ENABLED 不 APPLY）不改变任何回复字节；
6. Redis 故障 → 抑制（fail-closed 方向测试锁定）；
7. 一键关闭（PROACTIVE_ENABLED=false）恢复纯响应式。

## 9. 遗留

1. 包中推迟能力的落地顺序：discovery（先走意图新增流程）→ playbook →
   purchase_assist → journey（依赖真实用户体系）；
2. 拒绝检测语义化（现为规则正则，误判「不用了谢谢」类放弃语的风险由
   上轮展示标记窗口收窄；真实样本后考虑并入意图控制层）；
3. 活动配置后台化（现 JSON 文件，与 KB 运营后台同路线）；
4. quality_daily 加主动动作转化列（真实流量后）；
5. per-customer-per-day 全局频控（现为会话+活动两级，日级看数据再加）。
