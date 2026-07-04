# 系统总体架构

项目名称：`ai-cs-platform`

定位：基于 FastAPI、LangChain、LangGraph、PostgreSQL、Redis、Milvus 的 AI 客服聊天系统。

> 配套阅读：`roadmap.md`（Stage 01-15 总体路线图与各阶段状态 + backlog）、
> `../chat/intent_taxonomy.md`（意图体系规范，意图码/优先级/风险等级的单一事实来源）。
> 本文档描述**当前已实现**的分层与主链路形态（2026-07-04，Stage 01-15 全部落地）。

---

## 1. 当前状态

核心功能链路已闭环：意图识别（SetFit 语义模型 + 规则控制层 + LLM 难例二判）、
多轮补槽与任务状态机（含挂起/恢复）、写操作确认门与真实执行（mock 工具 + 全程审计）、
RAG/FAQ 知识库（Milvus + 文档解析管道）、商品库结合、人工接管与工单（Stage 07：
bot 静默 + 五类触发建单 + 坐席 claim/reply/resolve 闭环）、鉴权与多租户
（Stage 08，AUTH_ENABLED 开关）、决策日志全量留痕。

可观测（Stage 09/12）：Prometheus /metrics + Langfuse 链路追踪、quality_daily 看板、
数据回流与决策回放 CLI、CI 门禁、RAG 评估集；流式 SSE 端点已提供。

生产加固（Stage 13）：配置硬门禁、写操作防重放原子化、MCP 失败禁 mock 冒充事实、
幂等在途锁+指纹、API Key 即时吊销、Prometheus 多进程、L3 弱确认收紧、脱敏扩展。
内容安全护栏（Stage 14）：注入模式拦截、辱骂分级（连击转人工）、LLM prompt 防注入收口、
输出护栏；规则库在 guardrails.md 机器可读表（单一事实来源）。
体验闭环（Stage 15）：WS 双端实时（Redis Pub/Sub 跨进程）、会话级 CSAT、
会话生命周期（idle 关闭/重开/工单超时）、排队位置、主动消息入口。

尚未完成的为真实环境联调项：LLM Key / MinerU 服务 / 真实业务系统 MCP 对接 /
真实 embedding 阈值标定（均为可插拔接口，联调即换）；backlog 见 roadmap 3.6。

---

## 2. 分层架构

```text
Layer 0：API 接入层        FastAPI（chat / kb / product / health 路由）
Layer 1：Service 应用服务层 ChatService（trace、并发冲突处理、失败留痕、会话与历史 API）
Layer 2：LangGraph 编排层   8 线性节点 + 5 回复分支（见第 4 节），db_session 走 config 注入
Layer 3：业务决策层         HybridIntentClassifier（规则→SetFit→LLM 二判）/ SlotExtractor(规则+LLM兜底)
                            / DialogStateManager（任务栈）/ ConfirmationResponseParser
Layer 4：能力声明层         SkillRegistry（代码模板 + 31 个 skills_design md 声明双源合并，启动校验）
Layer 5：模型调用层         LLM Provider 工厂（timeout/降级）/ SetFit 模型 / Embedding（openai|hash）
Layer 6：执行与检索层       ActionExecutor（唯一写入口）/ ToolProvider(mock) /
                            KbRetriever+RagAnswerer（Milvus+关键词 RRF）/ ProductProvider
Layer 7：数据访问层         Repository（只做 CRUD，全部带 tenant_id）
Layer 8：基础设施层         PostgreSQL（事实来源）/ Redis / Milvus（可重建索引视图）
Layer 9：可观测层           DecisionLogger（意图/槽位/检索/工具调用/耗时/错误全量留痕）
```

---

## 3. 核心设计原则（全部已落地为代码约束）

```text
1.  route 只接参数调 Service；业务在决策层与节点中。
2.  意图体系单一事实来源：docs/chat/intent_taxonomy.md → app/chat/intent/catalog.py。
3.  上下文敏感意图（CONFIRM/DENY/SLOT_ONLY）永远由规则+状态机判定，不进语义模型。
4.  写操作必须过确认门；ActionExecutor 是系统唯一写工具入口（三重校验+防重放）。
5.  检索按「意图×对话状态」路由（R1-R5），补槽/确认门轮次绝不检索；
    价格/库存只能来自商品库/工具，禁止 RAG 回答（资损红线）。
6.  LLM 是增强层不是依赖：无 API Key / 调用失败时全链路自动降级（规则/模板），
    所有外部访问（DB/Redis/LLM/工具/Milvus）必须有 timeout。
7.  PG 是唯一事实来源：向量索引（Milvus）可随时 reindex 重建；工具审计/任务生命周期落表。
8.  每轮决策可回放：decision_log 记录意图 top_k、检索轨迹、工具调用、耗时、错误；
    失败轮次用独立事务留痕。
9.  多租户 tenant_id 贯穿全链路（会话归属校验、检索强制过滤）；鉴权在 Stage 08 收口。
10. 严格按阶段推进，每阶段文档附实现记录与遗留清单。
```

---

## 4. 运行时主链路（Stage 05 起为条件路由图）

```text
START
  → load_session_state      读会话/状态机/任务栈（归属校验；handoff 静默短路/closed 重开/CSAT 捕获短路）
  → preprocess_message      归一化
  → guardrail_check         护栏：注入/违禁词拦截、辱骂连击转人工、情绪标记（拦截不改状态机，Stage 14 实装）
  → intent_classify         规则控制层 → SetFit(29类) → LLM 难例二判 → UNKNOWN
  → slot_extract            规则抽取 + LLM 兜底（规则优先）
  → confirmation_parse      CONFIRMING 下含糊应答 LLM 解析（CONFIRM/DENY/MODIFY/UNRELATED）
  → dialog_state_resolve    状态机：补槽/确认门/任务挂起与恢复/执行交接
  → skill_resolve           按 final_intent 取 Skill
  ├─→ response_generate     模板底稿 + LLM 润色（补槽/确认门保持确定性模板）
  ├─→ rag_answer            R1/R2：FAQ 精确层 → 知识库混合检索 → 拒答
  ├─→ product_answer        R3：商品库事实优先 → RAG 增强 → 宁缺勿编
  ├─→ tool_invoke           订单/物流查询（mock 工具真实数据，失败按 R4 转 RAG）
  └─→ action_execute        确认门通过 → ActionExecutor 执行写工具 → 工单号回执
  → save_turn               消息/状态机/任务行/决策日志落库 + 续办提示定稿
                            + 转人工触发收口（Stage 07）/ CSAT 落库（Stage 15）
  → END
```

---

## 5. 对话状态机

```text
会话级（chat_dialog_state.state）：
  IDLE / COLLECTING / CONFIRMING / DONE / ABORTED / HANDOFF / FAILED
任务级（chat_task.status，Stage 05 起）：
  COLLECTING / CONFIRMING / EXECUTING / DONE / ABORTED / FAILED / SUSPENDED（挂起入栈）
后续扩展（未启用）：AUTH_CHECK / BLOCKED
```

任务挂起/恢复：补槽或确认门中提出新业务意图 → 旧任务入栈（SUSPENDED，深度上限
TASK_STACK_MAX）并做上下文槽位继承；新任务结束自动恢复并附续办提示。

---

## 6. 早期"预留入口"的落地对照

| v1 预留 | 现状 |
|---|---|
| RAGAnswerEngine / FAQAnswerEngine | ✅ `app/kb/answerer.py` + `retriever.py`（FAQ 精确层 + 混合检索） |
| ToolAnswerEngine | ✅ `tool_invoke` 节点 + ToolProvider |
| ActionExecutor | ✅ `app/chat/actions/executor.py`（唯一写入口） |
| ConfirmationResponseParser | ✅ `app/chat/confirmation/parser.py` |
| HumanHandoffEngine | ✅ `app/services/handoff_service.py`（幂等建单/上下文移交包/坐席闭环，Stage 07） |
| LLMJudge | 🔜 后续按需（当前已有 LLM 难例二判雏形；Stage 09 评估平台以规则门禁+人工审核在环落地，未引入 LLM 评审） |
