# Stage 41 需求：会话主动引导与建议闭环（Conversation Director 取舍版）

> 来源：一次关于「意图识别与动态流程关系 + 顶层会话策略编排层（Conversation
> Director / Next Best Action）」的外部讨论。讨论的两个核心判断**全部采纳**：
> ① 意图是流程的触发源之一但不是唯一源，触发决策必须综合状态/槽位/风险/资格；
> ② 流程状态反过来是意图识别的上下文（双向关系）。
> 但逐条盘点后发现：讨论所描绘的架构**大部分已在本仓库落地**（见第 2 节映射表）。
> 本文档职责有三：把讨论概念映射到既有实现（防止重复建设）、
> 固化「明确不做」的架构决策（第 4 节）、圈定真实缺口作为本阶段范围（第 3 节）。

---

## 1. 阶段目标

补齐「响应式客服」到「主动互动」的最后三块缺口，使系统能够：

```text
1. 客户建立会话还没说话时，做一次受抑制矩阵约束的轻量开场引导（不再完全沉默）；
2. 系统上一轮提出的主动建议（活动/引导/延伸），客户回「可以/好的」时能被
   确定性识别为接受并启动对应任务（现在只识别拒绝，接受通道缺失）；
3. 任务完成后的主动候选从「营销活动 + 注册引导」扩展出「服务延伸」层
   （查订单完成→提示可查物流），并固化候选优先级阶梯。
```

全部为 Stage 31 NBA 机制的增量，零新决策系统、零 migration、默认关闭零回归。

---

## 2. 讨论能力 → 本仓库现状映射（盘点结论：多数已有）

这是本文档最重要的一节：讨论中每个概念在本仓库的对应物。
**已有项一律不重复建设**，讨论文本仅作设计佐证归档。

### 2.1 已完整覆盖（不做任何改动）

| 讨论概念 | 本仓库对应实现 |
|---|---|
| 「意图 + 置信度 + 状态 + 槽位 + 风险 → 才触发流程」（Workflow Trigger 综合判断） | Stage 26 切换守护（`INTENT_SWITCH_THRESHOLD_*` 状态分级阈值 + margin + 显式切换信号）、P2 margin 路由、Stage 27 Meta 决策融合契约。触发条件化早已不是 `if intent == X` |
| 「流程状态帮助解释本轮意图」（WAITING_X 下「可以」≠ 普通意图） | 规则控制层先于 SetFit 的整个设计：确认门 CONFIRMING 下 META.CONFIRM/DENY（Stage 05）、COLLECTING 下 pending_fill 定向补槽（Stage 26 P1）、CSAT pending 短路捕获（Stage 15）、handoff 静默短路（Stage 07）。讨论建议的「流程应答识别」思想=本仓库既有骨架 |
| META 控制意图不进 SetFit 训练 | META.CONFIRM/DENY/SLOT_ONLY 全部规则层产出；MARKETING_OPT_OUT 对应 proactive opt-out 强拒绝（Stage 31） |
| 意图三分类（直接响应型/任务流程型/机会触发型） | 技能 FAQ/READ/WRITE 分级 + 检索路由矩阵 R1-R5（Stage 06-03）+ Stage 31「先答主问题，机会只做至多一条轻量追加」 |
| ASK_PRICE 条件分流（明确→直答；缺品类→收集；投诉中→禁营销） | `product_answer` 商品库直答（价格禁 RAG 红线）+ PRODUCT.RECOMMEND 补槽（Stage 32 category/budget）+ 抑制矩阵 `high_risk_flow`/`negative_emotion`（Stage 31） |
| NBA Engine（候选→硬抑制→优先级→最多一个动作→requires_user_acceptance） | Stage 31 全部：全局抑制矩阵、频控/冷却/opt-out（fail-closed）、`PROACTIVE_SESSION_MAX=1`、话术引导显式回复（Stage 33「回复『注册』」） |
| 顶层决策三层（硬规则→评分→LLM 只管文案） | Stage 31 红线更严：v1 纯规则，hook 运营写死**不经 LLM**；评分层按 Stage 27 模式影子采数据后再上 |
| EVENT/SCHEDULE_TRIGGER（库存恢复通知、定时任务） | Stage 36 事件驱动（白名单/幂等/退订/静默/发送前重查事实）+ `deploy/scheduler.py` 定时任务框架 |
| 客户生命周期状态机（VISITOR→REGISTERED→ACTIVE→…） | Stage 38 customer_journey（NEW→…→PURCHASED 单调不倒退 + **at_risk 叠加标记**=讨论的 SERVICE_RECOVERY 临时抑制态；活动 `eligible_journey_stages` 门控） |
| 「退款刚完成，客户说谢谢也不推新品」 | 抑制矩阵 high_risk_flow + at_risk + 拒绝冷却三重结构性保证 |
| 「正面情绪不能单独触发营销」 | 抑制矩阵中情绪只做**否决项**（负面→抑制），正面从不作为触发条件 |
| 多意图主次任务（查退款+顺便推荐→主意图先办、次意图入栈低优先级） | Stage 10 多意图切分：次要意图入任务栈自动续办 |
| 运营配置触发条件 + 发布校验意图码存在 | campaigns.json `eligible_intents`（域前缀支持）+ capabilities 活契约启动校验模式（意图码漂移即告警） |
| 「写操作流程必须过确认门 / 营销必须有抑制节点」的 DSL 发布校验 | 不需要校验——**结构性保证**：写唯一入口 ActionExecutor（模型绕不过），主动追加唯一收口 save_turn 抑制矩阵。配置层根本没有绕过的表达能力，强于任何发布期校验 |

### 2.2 部分覆盖 → 本阶段补齐（真实缺口）

| 讨论概念 | 现状 | 缺口 |
|---|---|---|
| SESSION_OPENED 主动欢迎（客户没说话就引导） | proactive 唯一收口在 save_turn——**必须客户先发消息**，开场完全沉默 | 会话创建时的开场引导决策（第 3.1 节） |
| WAITING_RECOMMENDATION_CONSENT +「可以」→ WORKFLOW_ACCEPT | 拒绝检测已有（`proactive:last` 窗口 + 拒绝语→冷却）；**接受不解析**——建议展示后客户说「好的」会掉进 SetFit 当普通消息 | 接受通道 META.PROACTIVE_ACCEPT（第 3.2 节，Stage 31 遗留 2「拒绝检测语义化」的姊妹项） |
| 任务后续四等级（必要后续/服务延伸/帮助性推荐/营销活动） | NBA 候选只有 START_ONBOARDING 与 MENTION_CAMPAIGN 两源 | 服务延伸候选源 + 优先级阶梯固化（第 3.3 节） |

### 2.3 讨论提出但本阶段明确不做（决策固化，见第 4 节理由）

```text
1. 通用 Workflow Engine / 流程 DSL / workflow_instance 表
2. 独立 workflow_trigger_router 图节点
3. conversation_goal 新状态对象
4. 前端行为事件（PAGE_VIEWED / 结账中断 / 页面停留）
5. 接受概率 / 打扰成本评分模型
6. SCHEDULE 型营销跟进（询价 24h 未购买回访）
```

---

## 3. 本阶段要做什么（三件）

### 3.1 开场引导（Session Welcome）

```text
接入点：POST /api/chat/sessions 会话创建成功后（含 closed 会话自动重开不触发——
       重开有用户消息在途，走正常链路）。
决策：  decide_welcome(tenant, user_id, locale)
  ├─ 复用全局抑制检查子集：opt-out / 人工接管未归还 / at_risk → 不发
  ├─ 频控：proactive:welcome:{tenant}:{user}（TTL 24h，同客户每日至多一次；
  │        Redis 故障 fail-closed 不发——营销方向纪律）
  ├─ journey 门控：welcome 配置可声明 eligible_journey_stages（NEW/未知客户
  │        发注册引导变体；PURCHASED 发服务型变体），复用 Stage 38 门控语义
  └─ 产出：i18n 模板消息（运营配置 hook，不经 LLM——Stage 31 红线沿用）
落地：  role=assistant 消息落库（metadata_json 标 proactive_welcome + config_id
       =发送依据可追溯，Stage 36 先例）+ WS 已连接则实时推送；
       决策证据不进 decision_log（无轮次）——落消息 metadata + 指标。
开关：  PROACTIVE_WELCOME_ENABLED=false 默认关；受 PROACTIVE_ENABLED 总开关双关。
```

红线：开场引导是**轻量单条**（欢迎+能力提示+可选注册引导），不是流程——
客户接下来说什么走完全正常的主链路；不做「等待几秒检测停留」类前端行为逻辑。

### 3.2 主动建议接受通道（META.PROACTIVE_ACCEPT）

```text
现状不对称：上轮展示建议 + 本轮「不用了」→ 冷却（已有）；
           上轮展示建议 + 本轮「可以/好的/需要」→ 掉进 SetFit（缺失）。

机制：proactive:last 键升级为携带 payload：
     {action, campaign_id/followup_id, accept_intent}（TTL 10 分钟不变）。
     accept_intent 由活动/延伸配置声明（如 "PRODUCT.RECOMMEND"），
     发布校验意图码必须存在于 taxonomy（capabilities 契约模式）。

规则控制层新增判定（顺序红线：排在确认门 CONFIRMING、CSAT 捕获、
pending_fill 全部既有短路**之后**——高风险语义优先，最后才轮到营销应答）：
  上轮展示标记存在
  + 本轮命中接受语（可以/好的/要/需要/发我看看…短语表，同拒绝语表同层维护）
  + 纯接受判定：无业务残差（复用 Stage 23 残差判定思想——
    「好的，另外我要退货」不判接受，照常走分类）
  → META.PROACTIVE_ACCEPT（source=RULE_PROACTIVE_ACCEPT）
  → 按 accept_intent 开新任务走正常链路（补槽/确认门照旧，不跳步）

弱信号（没接话、接了别的话题）不判接受——自然过期。
开关：随 PROACTIVE_APPLY（没有真实展示就没有接受语境，天然联动）。
```

红线：接受通道**只对上轮真实展示过的建议生效**（窗口键是唯一凭据）；
接受后进入的是普通任务（该补槽补槽、该确认确认），**不因「用户同意过」
跳过任何确认门**——同意看推荐 ≠ 同意下单。

### 3.3 服务延伸候选源 + 候选优先级阶梯

```text
配置：configs/followups.json（campaigns 同模式：mtime 缓存/损坏 fail-open 空表）
  [{
    "followup_id": "order_to_logistics",
    "trigger_intents": ["ORDER.QUERY"],      // 本轮 DONE 的意图
    "suggest_key": "proactive.followup.logistics",  // i18n 模板键
    "accept_intent": "LOGISTICS.QUERY",       // 接受后开的任务
    "enabled": true
  }]

接入：decide_proactive 候选生成扩为三源，固化优先级阶梯（讨论 P0-P7 的落地版，
     高层级有候选即短路，仍然全轮至多一个动作）：

  （结构性前置，非候选）P0 安全合规=护栏 / P1 服务优先=抑制矩阵全部否决项
  P2  必要后续 —— 暂无候选源，留位（如物流异常→催件建议，随真实工具）
  P3  START_ONBOARDING（获客，Stage 33 既有）
  P4  SERVICE_FOLLOWUP（本阶段新增，服务延伸）
  P5  MENTION_CAMPAIGN（营销，Stage 31 既有）

  ——Stage 33 已定「onboarding 先于活动」，本阶段只是把隐式顺序表格化+插入 P4。

抑制：服务延伸走**同一张抑制矩阵**（负面情绪/高风险流程/频控全部生效），
     v1 不为它放宽任何条目——保守方向，放宽进遗留。
```

---

## 4. 本阶段不做什么（架构决策固化）

```text
1. 不建通用 Workflow Engine / 流程 DSL / workflow_instance 表。
   Stage 32 已验收「Playbook 引擎不建二套」映射表：任务状态机=step、
   task_stack=挂起恢复、TTL/MAX_ASKS=超时放弃、ActionExecutor=写恰好一次、
   decision_log=回放。讨论中的全部流程示例（选品/对比/注册/退款）都已被
   该映射承载。引入第二套多轮机制的触发条件（真实运营需要可视化编排流程）
   仍未出现——出现时也是「配置驱动既有机制」优先于「新引擎」。
2. 不建独立 workflow_trigger_router 图节点。触发决策已收口在三个有契约执法
   的点：classify_control（控制语义）/dialog_state_resolve（切换守护+Meta 影子）/
   save_turn（NBA）。再加一个中心节点=职责重复+节点契约膨胀。
3. 不加 conversation_goal 状态对象。journey stage（跨会话）+ task_stack（会话内）
   + proactive reason_codes（决策依据）三者已覆盖其全部分析口径；
   新对象需要新表新状态同步点，收益不明。
4. 不接前端行为事件（PAGE_VIEWED/停留/结账中断）。web/ 是测试控制台不是真实
   商城前端，无埋点来源；事件白名单机制（Stage 36）就是未来接入点，不需预建。
5. 不上接受概率/打扰成本评分。Stage 31 既定路线：规则版先跑，影子期采
   reason_codes + 接受/拒绝数据（本阶段接受通道恰好补齐正样本采集），
   有真实分布后再谈模型（Stage 27 先影子后接管的纪律）。
6. 不做 LLM 顶层决策。讨论自己的结论也是三层中 LLM 只管文案——本仓库现状。
```

---

## 5. 技术要求

```text
1. 零 migration：welcome 频控/接受窗口全走 Redis 既有键模式；
   开场消息 metadata_json 记录发送依据（Stage 36 先例）。
2. 全部 fail-closed（营销方向纪律，与 Stage 31 一致）：Redis 故障不发
   欢迎、不判接受（接受判定失败=走正常分类，无害）。
3. 配置损坏 fail-open 空表（campaigns/guidelines 同模式）；accept_intent
   / trigger_intents 启动校验存在于 taxonomy，缺失告警并跳过该条。
4. i18n：全部话术走 t() / 配置模板，多语言按 Stage 19 模式覆盖。
5. 指标：复用 proactive_actions_total{action,outcome}——action 新增
   WELCOME / SERVICE_FOLLOWUP，outcome 新增 accepted（接受通道命中）；
   多租户不进 label。
6. 决策留痕：接受轮 decision_log.intent_result 记 source=RULE_PROACTIVE_ACCEPT
   + 命中的 payload 摘要；NBA 轮 graph_trace_json.proactive 既有结构不变，
   candidates 增加分层来源标记。
7. 默认关闭零回归：PROACTIVE_WELCOME_ENABLED=false；服务延伸源受
   PROACTIVE_ENABLED/APPLY 双开关；接受通道随 APPLY 联动。
```

---

## 6. 目录和文件要求

```text
app/chat/proactive/
  nba.py               # 候选三源 + 优先级阶梯（改）
  welcome.py           # 开场引导决策（新）
  followups.py         # 服务延伸配置池（新，campaigns.py 同模式）
app/chat/intent/
  classify_control 链  # META.PROACTIVE_ACCEPT 判定（改，顺序红线见 3.2）
app/api/routes/chat.py # 会话创建后触发 welcome（改，route 只调 service）
configs/
  followups.example.json / welcome.example.json
app/locales/zh.py      # 开场/延伸模板文案
tests/stage41/         # 专项回归
```

---

## 7. 验收标准

```text
1. 默认关闭全量测试零回归。
2. 开场引导：新会话收到 1 条 assistant 开场消息（metadata 含 config_id）；
   同客户 24h 内第二个会话不发；opt-out/at_risk 客户不发；Redis 故障不发；
   closed 重开不发。
3. 接受通道：展示活动→「好的」→ 开出 accept_intent 任务且走完整补槽/确认门；
   「好的，另外我要退货」→ 不判接受走正常分类；无展示窗口时「好的」→
   不判接受（防误劫持闲聊）；确认门 CONFIRMING 下「好的」仍归确认门
   （顺序红线测试锁定）。
4. 服务延伸：ORDER.QUERY DONE 后追加物流延伸建议；同轮有 onboarding 候选时
   onboarding 胜出（阶梯测试）；负面情绪/高风险流程照常抑制；
   仍然全轮至多一个动作。
5. 抑制矩阵既有测试（投诉中零营销等）全部不动。
```

---

## 8. 遗留

```text
1. P2 必要后续候选源（物流异常→催件工单建议）——随真实工具数据。
2. 服务延伸抑制条目差异化（是否允许 at_risk 客户收纯服务型延伸）——
   保守起步，真实反馈后评估。
3. 接受/拒绝语义化解析（LLM 兜底含糊应答，确认门 parser 同模式）——
   与 Stage 31 遗留 2 合并处理。
4. 询价未购买定时跟进（SCHEDULE 触发营销）——需行为数据与合规评估，
   事件通道是现成接入点。
5. 接受率/打扰成本数据积累后的评分层（Stage 27 影子→接管纪律）。
6. 运营配置后台化（welcome/followups 随 campaigns 同路线）。
7. （实施后发现）接受通道开出的任务不继承上文槽位：查订单完成→接受查物流
   建议后需重报订单号——可复用任务挂起的上下文槽位继承机制，待评估
   （继承范围要防串槽，Stage 26 纪律）。
8. （实施后发现）开场引导无用户消息无 locale 上下文，暂用默认语言模板；
   多语言租户可在 welcome 配置按渠道声明 locale。
```
