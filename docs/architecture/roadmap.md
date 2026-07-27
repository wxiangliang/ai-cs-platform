# 项目总体路线图（Roadmap v2）

> 本文档是 ai-cs-platform 的阶段总规划，回答「做成一个功能完整、可扩展的 AI Agent 客服系统，
> 分几步走、每步做什么、验收什么」。各阶段的详细需求见 `docs/requirements/stage-XX-*/`。
> 与 `architecture/system_overview.md`（分层与主链路）、`chat/intent_taxonomy.md`（意图体系）配套阅读。

---

## 1. 目标形态（终局架构）

一个多租户 AI 客服 Agent 平台，核心能力闭环：

```text
用户消息
  → 意图识别（规则短路 + LLM 分类 + 向量辅助，置信度融合）
  → 槽位收集（多轮补槽、上下文继承、任务挂起/恢复）
  → 决策路由
      ├── 业务技能（Skill）：工具查询（订单/物流/商品）→ LLM 组织回复
      ├── 写操作：确认门 → ActionExecutor 执行 → 审计留痕
      ├── FAQ/RAG：向量库检索 → 引用生成 → 拒答保护
      └── 转人工：工单 + 会话接管
  → 回复生成（模板 → LLM 生成，流式输出）
  → 决策日志全量落库（训练与评估数据回流）
```

不可妥协的架构原则（全阶段有效）：

1. **LLM 永远无权直接执行写操作**——所有 L2/L3 风险操作必须经确认门与 ActionExecutor。
2. **意图体系单一事实来源**——`docs/chat/intent_taxonomy.md`，代码与 Skill 文档从它派生。
3. **每轮决策可回放**——decision_log 记录意图、槽位、检索、工具调用、耗时、错误。
4. **多租户隔离贯穿全链路**——所有查询带 tenant_id，鉴权上线后 tenant_id 来自凭证而非请求体。
5. **严格按阶段推进**——每阶段有明确验收，不跨阶段实现。

---

## 2. 阶段总览

| Stage | 名称 | 状态 | 核心交付 |
|---|---|---|---|
| 01 | 基础框架 | ✅ 已实现 | FastAPI / 配置 / 日志 / 异常 / 统一响应 / PG+Redis async / Alembic |
| 02 | 聊天核心表 | ✅ 已实现 | chat_session / chat_message / chat_dialog_state / chat_decision_log |
| 03 | 聊天主链路 | ✅ 已实现（含 v2 修订） | LangGraph 主链路（初版 9 节点线性，现已演进为 8 线性节点 + 5 回复分支，见 system_overview 第 4 节）、规则意图、补槽状态机、模板回复、决策日志；v2 修复确认门闭环 / 租户校验 / JSONB 落库等缺陷（见 3.1） |
| 04 | LLM 接入 | ✅ 已实现（2026-07-02） | **04-02** SetFit 语义分类（29 类，acc 0.94）；**04-01** LLM Provider 工厂（timeout/降级）、LLM 难例二判（SetFit 低置信才调用）、LLM 槽位兜底（规则优先）、LLM 回复润色（事实保护，仅 DONE/FALLBACK）、会话创建与历史分页 API、意图评估门禁；db_session 迁出 GraphState（checkpointer 就绪）。遗留：流式 SSE、真实 LLM 端点联调 |
| 05 | 工具层与确认门闭环 | ✅ 已实现（2026-07-02） | Skill Loader（31 个 md 声明合并+启动校验）、ToolProvider+mock、**ActionExecutor（唯一写入口，三重校验+防重放，确认→执行→工单号回执）**、ConfirmationResponseParser（LLM 解析 MODIFY/UNRELATED）、chat_task/chat_tool_call 审计表、任务挂起/恢复+上下文槽位继承、tool_invoke 节点（订单/物流查询真实 mock 数据）。遗留：真实工具 HTTP Provider、outbox 模式 |
| 06 | RAG / FAQ / 向量库 | ✅ 已实现（2026-07-02，Milvus） | 后端抽象 + Milvus 实现、kb 三表（PG 事实来源）、混合检索（向量+关键词 RRF）、FAQ 精确层、拒答与引用、rag_answer 节点、reindex CLI；**06-02** 文档解析管道（MinerU HTTP/Docling/内置链，pdf/docx/xlsx/md/txt，结构感知切分：标题路径注入/表格独立分片重复表头/图片 caption 合块）；**06-03** 检索路由矩阵 R1-R5 + 商品库 ProductProvider（商品意图商品库优先，价格库存禁走 RAG）+ product_answer 节点；**06-04** 检索管道 v2（query 归一化繁简/同义词/型号、动态加权 RRF、可选 CrossEncoder 重排、父子分块章节上下文、歧义检测） |
| 07 | 人工接管与工单 | ✅ 已实现（2026-07-03） | chat_handoff_ticket（部分唯一索引承载幂等，context_json 上下文移交包）、五类触发收口（USER_REQUEST/PAYMENT_ISSUE/REPEATED_UNKNOWN/EXECUTION_FAILED/SKILL_RULE）、**bot 静默修复**（session=handoff 短路，意图分类前拦截）、坐席队列/详情/认领(409 防抢单)/回复(role=agent)/归还 API（admin scope）、UNKNOWN 连击计数（context_stacks_json）、决策日志记录 reason+ticket_id |
| 08 | 鉴权与多租户加固 | ✅ 已实现（2026-07-02） | **生产门槛**：API Key per tenant（凭证解析 tenant_id）、scope 分离管理面、限流（租户+会话级）、Idempotency-Key、session 强制服务端发号；AUTH_ENABLED 开关保开发模式零回归 |
| 10 | 多意图/任务治理/记忆 | ✅ 已实现（2026-07-03） | 一句话多意图（分段识别+任务栈承接+防串槽）、任务 TTL 与追问上限、记忆系统（MemoryProvider 协议：自研 local + mem0 双实现，短期窗口/会话摘要/长期事实，无 Key 自动降级） |
| 11 | MCP 工具服务与集成 | ✅ 已实现（2026-07-03） | 订单/物流查询以 MCP 标准工具服务提供（FastMCP streamable-http）；McpToolProvider 客户端集成（TOOL_PROVIDER=mcp，动态发现、未覆盖/故障回落 mock + degraded 标记 + TTL 重发现）；数据与 mock 共享生成器可对拍 |
| 09 | 可观测与评估平台 | ✅ 已实现（2026-07-03） | Prometheus 指标 9 个（/metrics，label 基数受控多租户走 SQL）、质量看板 quality_daily 物化视图 + 8 组看板 SQL（quality_queries.md）、数据回流 CLI（export_review_set：低置信/LLM 二判/FALLBACK/差评→人工审核→build --extra 重训）、FAQ 沉淀候选导出、chat_feedback 表+反馈 API（归属双校验防投毒）、CI 门禁（.github/workflows/ci.yml）、决策回放 replay_trace CLI、RAG 评估集 32 组落地（阈值双档门禁） |
| 12 | Langfuse 链路追踪 | ✅ 已实现（2026-07-03） | LangChain CallbackHandler 挂 LangGraph run config（节点 span+LLM prompt/token 自动捕获，节点零改动）；session/user/tenant/trace_id 关联；无 Key/服务不可达静默降级；与 Prometheus 互补（聚合 vs 单次调用明细） |
| 13 | 生产加固 | ✅ 已实现（2026-07-03） | 审计整改三批全部完成：配置硬门禁（APP_ENV=prod 缺项拒启）、管理面强制 token、ActionExecutor 防重放原子化（并发确认恰好执行一次）、MCP 失败禁 mock 冒充事实（TOOL_MCP_FALLBACK=fail 默认）、幂等 after-commit+在途锁+body 指纹、API Key 即时吊销（Redis 版本广播）+expires_at、Prometheus 多进程、L3 弱确认收紧（「好的」→重确认）、脱敏扩展（地址+decision_log）、建单 SAVEPOINT、FAQ needs_reindex、启动 DB 探活、路径锚定、限流先判后写 |
| 14 | 内容安全护栏 | ✅ 已实现（2026-07-04） | 规则库入 guardrails.md 机器可读表格（单一事实来源，injection/abuse_severe/emotion/output_leak 四类）；guardrail_check 实装（注入拦截、辱骂连击 2 次→ABUSE 工单+静默、情绪 flag 软化话术、同文本灌注拦截，计数走 Redis fail-open）；wrap_user_input 收口全部 5 个 LLM 拼接点 + RAG system 硬约束；输出护栏（润色回退底稿/RAG 弃生成走摘录/记忆写入过滤）；guardrail_blocks_total 指标 + 决策日志留痕；训练语料 500 条零误拦回归用例 |
| 16 | 知识库运营后台 | ✅ 已实现（2026-07-05） | 文档版本与草稿-发布审核状态机（draft/pending_review/published/archived，生效判据 published_version 非空且未 archived——编辑不影响线上）、版本回滚、定时生效/失效（kb_schedule cron）、命中率/盲区 SQL（kb_quality_queries）、8 个运营 API；publish 复用 ingest 管线，219 tests 零回归 + 真实 Milvus e2e |
| 17 | LLM 成本控制 | 📝 需求已写（语义缓存不依赖真实 LLM） | 语义缓存（同问直答，事实类禁缓存红线）、租户 token 预算与熔断（超限降级模板）、模型分级路由（简单轮走小模型） |
| 18 | A/B 实验框架 | 📝 需求已写（框架可落地，结论待流量） | 确定性 hash 分桶、变体只覆盖已有可配项、decision_log 落变体、按变体切分 quality_daily 对比 |
| 19 | 多语言地基（i18n） | ✅ 已实现（2026-07-04） | 面向用户文案收口 `app/core/i18n.py`+`app/locales/`（中文源逐字零回归、en 骨架示范）、skill 模板语言覆盖、LLM 提示词加「按用户语言回复」（不翻译提示词）、locale 贯穿（请求＞会话记忆＞默认，写入 metadata 沿用）；输出侧地基完成，输入理解侧（分类/词表）待数据 |
| 15 | 体验闭环 | ✅ 已实现（2026-07-04） | WS 双端实时（用户端 agent_reply/session_resumed/proactive、坐席端 ticket_created/user_message；Redis Pub/Sub 跨进程 + 故障本地直投）、会话级 CSAT（chat_csat 表 + 短路捕获 + quality_daily csat 列 + 低分回流）、会话生命周期（idle 关闭+重开、ASSIGNED 超时 CLOSED 补 Stage 07 遗留，close_idle_sessions CLI）、排队位置反馈、主动消息 notify 入口 |
| 20 | 记忆摘要 v2 | ✅ 已实现（2026-07-21） | 结构化会话摘要（固定 schema JSON、数组/总长编译时上界、解析失败纯文本降级、存量兼容）、单一表示不变式回归锁定、MCP 大结果红线（doc-only） |
| — | Post-Stage 20 全链路 Review 加固 | ✅ 已实现（2026-07-27） | 六批整改（意图控制层/正确性/容量/延迟/演进/RAG 强化 WeKnora 对齐），274→300 tests，详见 `docs/requirements/post-stage-20-review-hardening/` |
| 21 | 智能澄清 | ✅ 已实现（2026-07-27） | UNKNOWN 轮次 LLM 生成针对性澄清问句（top_k 候选+近期对话），替代固定模板；无 Key 降级零回归；unknown_streak 安全网不动；三层演进（澄清→只读诊断 agent→离线 deep agent）的第一层 |
| 22 | 只读诊断 agent | ✅ 已实现（2026-07-27） | 解释性查询多步只读调查（ReAct 受约束变体：query_* 白名单/结构性终止/数字事实校验/完全可降级）；tool_invoke 静态链后按启发式触发，解释追加事实底稿；默认 DIAGNOSE_AGENT_ENABLED=false，联调开启；三层演进第二层 |
| 23 | 对话方向纠偏 | ✅ 已实现（2026-07-27） | 错方向三盲区纯规则防线：任务中途否定（COLLECTING 判 DENY 仅终止当前任务+重定向话术）、零进度恢复降调、中置信软确认（<0.60 新开任务复述意图）；direction_correction_total 指标 + 错向监控 SQL；零 LLM 依赖 |

> **Stage 23 后功能建设收口**。后端下一步（真实 LLM/业务联调 → 生产化 → 数据飞轮 → 能力补全）
> 的完整规划见 `docs/requirements/post-stage-23-backend-roadmap/01_backend_next_steps.md`，
> 开工前按规范拆 Stage 文档。

> 依赖关系：04 是 05/06 的前置（都需要 LLM Provider 工厂）；05 与 06 可并行；
> 07 依赖 05（工单是写操作）；08 可随时插入，越早越好；09 从 04 起持续建设。

---

## 3. 各阶段要点

### 3.1 Stage 03 修订（本次执行）

v1 实现存在如下必须修复的缺陷（详见 `requirements/stage-03-chat-main-chain/01_chat_main_chain.md` 附录「v2 修订记录」）：

| 级别 | 问题 | 修复 |
|---|---|---|
| P0 | 确认门死循环：CONFIRMING 状态下「确认」无意图可接，永远重复确认话术 | 新增 META.CONFIRM / META.DENY 上下文意图；状态机处理 CONFIRMING 分支（确认→受理并结束任务；否认→取消任务）。Stage 03 只「受理」不执行真实工具 |
| P0 | JSONB 原地变更：续接补槽时 active_task 更新不落库（SQLAlchemy 判无变化） | 状态机构造新 dict；JSONB 列启用 MutableDict 追踪 |
| P0 | 跨租户/跨用户会话劫持：load_session_state 不校验会话归属 | 按 (tenant_id, session_id) 查询并校验 user_id，不匹配返回 403/404 |
| P1 | 并发冲突裸 500：乐观锁 StaleDataError / 首轮并发 IntegrityError 无处理 | 转 409 CONCURRENT_UPDATE 业务错误码 |
| P1 | 节点异常整轮无痕：失败时消息与决策日志全部回滚 | ChatService 捕获后用独立事务写带 error_json 的 decision_log |
| P1 | 「取消订单」被误判 META.ABORT 并回复「已为您取消」 | 新增 ORDER.CANCEL 意图（写操作确认门）；ABORT 词表收紧 |
| P1 | 空消息把状态机打成 FAILED；纯字母被判 SLOT_ONLY；手机号被抽成订单号 | 护栏拦截不改状态机；slot-only 要求含数字；抽取先扣除手机号 |
| P1 | 意图码与 Skill 文档不一致（HANDOFF_REQUEST vs TRANSFER_HUMAN） | 代码统一为 META.TRANSFER_HUMAN（taxonomy 规范码） |
| P1 | DB 无语句级 timeout；分类器无抽象接口 | engine 加 command_timeout；IntentClassifier 协议化（async），为 Stage 04 铺路 |

### 3.2 Stage 04 —— LLM 接入 ✅ 已实现（2026-07-02）

详见 `requirements/stage-04-llm-integration/`（两份文档附录有实现记录；实际方案调整：
语义分类主力为 **SetFit 本地模型**，LLM 降为难例二判；回复为**模板底稿+LLM 润色**）。原规划要点：

- **LLM Provider 工厂**：统一封装模型调用（OpenAI 兼容接口），强制 timeout、重试、失败降级；API Key 走 settings。
- **混合意图分类**：规则高置信短路 → LLM few-shot 分类（prompt 内嵌 taxonomy 第 6 节裁决表）→ 置信度阈值以下走澄清/UNKNOWN；输出 top_k 落 decision_log。
- **LLM 槽位抽取**：规则先抽，LLM 兜底复杂表达；结果合并策略明确。
- **LLM 回复生成**：意图/状态确定后，用 Skill 的 prompt_fragment + guardrails 生成自然回复（替换生硬模板）；护栏红线进 system prompt。
- **会话与历史 API**：`POST /api/chat/sessions`（服务端发号）、`GET /api/chat/sessions/{id}/messages`（分页）——前端联调必需。
- **意图评估集**：每意图 ≥10 条样例，评估脚本入库，改分类器必须回归。

### 3.3 Stage 05 —— 工具层与确认门闭环 ✅ 已实现（2026-07-02）

详见 `requirements/stage-05-tools-confirmation/`（文档附录有实现记录与范围调整：
Skill Loader 为双源合并、挂起任务自动恢复+续办提示、新增上下文槽位继承）。原规划要点：

- **Skill Loader**：从 `docs/chat/skills_design/skills/*.md` 的 YAML front-matter 加载 Skill 声明（替换内存静态注册表），启动时校验 schema（含 risk_level/priority/tool_returns）。
- **工具接口层**：`ToolProvider` 协议 + mock 实现（query_order / query_logistics / create_refund_ticket 等），真实对接留到业务系统就绪。
- **确认门闭环**：ConfirmationResponseParser（理解确认/否认/修改）+ ActionExecutor（执行前二次校验风险等级与槽位完整性）+ 执行结果落 chat_tool_call。
- **新表**：`chat_task`（任务生命周期持久化，替代仅存 JSONB 快照）、`chat_tool_call`（工具调用审计）。
- **任务挂起/恢复**：启用 task_stack_json，COLLECTING 中插入新意图时旧任务入栈。

### 3.4 Stage 06 —— RAG / FAQ / 向量库 ✅ 已实现（Milvus，2026-07-02）

详见 `requirements/stage-06-rag-faq/`（含实现记录）。要点：

- **向量库选型：Milvus**（v3 决策，替代 v2.1 的 pgvector+ES 双实现方案）；
  上层只依赖 `VectorStoreBackend` 协议，pgvector / ES 为后续可选后端（新增 backend 文件即可）。
- **PG 是唯一事实来源**：原文/分块/embedding 全落 PG（kb_document / kb_chunk / faq_entry），
  Milvus 只是索引视图，`python -m app.kb.reindex` 可全量重建；Milvus 写失败标记 needs_reindex 兜底。
- **两级检索**：FAQ 精确层（标准答案零幻觉）→ 文档层混合检索（Milvus 向量 + PG 关键词，RRF 融合）。
- **两条触发路径**：FAQ.GENERAL 意图；META.UNKNOWN 兜底且无任务时先过知识库（长尾第一道网）。
  业务 Skill 的 `rag_fallback: true` 依赖 Stage 05 工具层，届时接入。
- **拒答与引用**：低于阈值拒答不编造；检索轨迹（含拒答轮次的命中分数）落 decision_log.retrieval_json，
  回答来源与引用落 chat_message.metadata_json。
- **韧性**：知识库/Milvus 故障不打断聊天主链路（节点内降级模板回复），ready 探针单列 kb_milvus 状态。
- **遗留**：检索评估集与阈值标定待接入真实 embedding（Stage 04）后建设。

### 3.5 Stage 07-09（详细需求 2026-07-02 写就；07/08/09 均已实现）

- **07 人工接管 ✅ 已实现（2026-07-03）**：`requirements/stage-07-handoff/01_human_handoff_and_ticket.md`
  （附录有实现与验证记录）——修复"接管后 bot 抢答"缺陷（session=handoff 意图分类前静默短路）、
  工单闭环（PENDING→ASSIGNED→RESOLVED，claim 条件更新防并发抢单）、上下文移交包、
  五类触发收口（用户要求/支付异常/连续兜底/执行失败/Skill 安全阀 requires_human_if）、
  坐席 API（admin scope）。
- **08 鉴权加固 ✅ 已实现（2026-07-02）**：`requirements/stage-08-auth-hardening/01_auth_and_tenant_isolation.md`
  （附录有实现与验证记录）——API Key per tenant + scope 分离、限流（429+Retry-After）、
  Idempotency-Key 幂等、session 强制服务端发号、trace 中间件化；
  `AUTH_ENABLED` 开关下开发模式零回归；密钥用 `scripts/manage_api_keys.py` 管理。
- **09 评估平台 ✅ 已实现（2026-07-03）**：`requirements/stage-09-observability/01_metrics_and_evaluation.md`
  （附录有实现与验证记录）——指标/看板 SQL/回流 CLI/反馈表/CI 门禁/决策回放，
  "线上表现→回流→重训→门禁"质量闭环已打通（quality_daily 物化视图 + export_review_set +
  build --extra + tests/eval 门禁）；RAG 评估集 32 组同步落地（`docs/testing/rag_eval_set.md`）。

---

## 3.6 功能差距 backlog（2026-07-03 差距分析，未排期）

按需求成熟度排序，排期时再写阶段文档：

| 方向 | 说明 | 前置 |
|---|---|---|
| 多渠道接入 | 微信公众号/企微/APP SDK 接入层（channel 字段已预留，需各渠道消息协议适配 + 回调网关） | Stage 15 的 WS/通知基建 |
| ~~知识库运营后台~~ → **Stage 16** 📝 | 已升级为正式阶段（需求已写） | 无 |
| 多模态消息 | 图片（订单截图 OCR 提单号、商品图识别）、语音（ASR 转文本进主链路） | 真实模型接入 |
| 多语言 | **输出侧地基 → Stage 19** 📝（文案收口+提示词国际化，现在可做）；输入理解侧（分类器多语言训练集、护栏词表/槽位正则）仍 backlog | 输入侧待训练数据 |
| ~~LLM 成本控制~~ → **Stage 17** 📝 | 已升级为正式阶段（语义缓存不依赖真实 LLM，阈值标定待流量） | 部分待真实 LLM 用量 |
| ~~A/B 实验~~ → **Stage 18** 📝 | 已升级为正式阶段（框架可落地，显著性结论待流量） | Stage 09 数据积累 |

---

## 4. 与 v1 规划的差异说明

1. v1 只规划到 Stage 03，架构文档中的「后续预留」（7 大 Engine、扩展表、预留 API）从未落成阶段文档——本版补齐 Stage 04-09。
2. Stage 04/05 顺序确认为「先 LLM 后工具」：确认门解析（Stage 05）依赖 LLM 理解自然语言确认/修改，需要 Stage 04 的 Provider 基建。
3. RAG 放 Stage 06 而非更早：RAG 质量依赖 LLM 生成（04）与拒答转人工路径（05 的工单雏形），且知识库表引入 pgvector 属独立基建。
4. 鉴权（08）在架构上越早越好，但为不阻塞核心链路验证，允许 04-07 期间继续用请求体传 tenant_id 的**开发模式**，文档与代码需标注该临时约定。

---

## 变更记录

| 日期 | 说明 |
|---|---|
| 2026-07-02 | v2 首版：补齐 Stage 04-09 规划；记录 Stage 03 修订清单 |
