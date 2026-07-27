# docs 目录说明

本目录用于保存 `ai-cs-platform` 项目的所有需求、架构、数据库、API、测试和运维文档。

---

## 1. 文档组织原则

```text
1. 一个需求一个 md 文件。
2. 每个阶段单独一个文件夹。
3. 文件名根据需求命名。
4. AGENTS.md 只放全局规则和文档入口，不写过长需求。
5. Codex 每次只执行一个阶段文档，不要一次性执行全部阶段。
```

---

## 2. 推荐目录结构

```text
docs/
  README.md
  00_docs_management_standard.md

  architecture/
    system_overview.md              # 分层架构与主链路
    functional_review_2026-07-04.md # 全局功能审查整改台账
    roadmap.md                      # ★ 总体路线图 Stage 01-15 + backlog（3.6 节）

  chat/
    intent_taxonomy.md              # ★ 意图体系规范（单一事实来源，v2 新增）
    skills_design/
      00_skill_schema.md            # Skill 定义规范 v2
      guardrails.md                 # 全局护栏
      README.md                     # Skill 清单（31 个）
      skills/*.md                   # 各 Skill 文件

  intent/
    README.md                       # ★ 意图训练数据集规范（v41 质检结论 + v42 映射规则）
    intent_train_v41_clean_nodup.csv    # 原始训练数据（只读）
    intent_train_v42_project.csv        # 项目对齐版（训练用，脚本生成）

  requirements/
    _templates/
      stage_requirement_template.md
    chat-framework/
      fastapi_langgraph_chat_framework.md
    stage-01-foundation/01_foundation_framework.md          # ✅ 已实现
    stage-02-chat-tables/01_chat_core_tables.md             # ✅ 已实现
    stage-03-chat-main-chain/01_chat_main_chain.md          # ✅ 已实现（含 v2 修订附录）
    stage-04-llm-integration/01_llm_intent_and_generation.md        # ✅ 已实现（LLM 工厂/二判/槽位兜底/润色/会话 API）
    stage-04-llm-integration/02_setfit_intent_classifier.md         # ✅ 已实现（SetFit 语义意图分类）
    stage-05-tools-confirmation/01_tool_layer_and_confirmation_gate.md  # ✅ 已实现（工具层+确认门执行闭环）
    stage-06-rag-faq/01_rag_faq_knowledge_base.md               # ✅ 已实现（Milvus，提前落地）
    stage-06-rag-faq/02_document_parsing_and_chunking.md        # ✅ 已实现（MinerU/Docling/内置解析链 + 结构感知切分）
    stage-06-rag-faq/03_retrieval_routing_and_product.md        # ✅ 已实现（检索路由矩阵 + 商品库结合 + FAQ/RAG 边界）
    stage-06-rag-faq/04_retrieval_pipeline_v2.md                # ✅ 已实现（归一化/加权RRF/Rerank/父子分块/歧义检测）
    stage-07-handoff/01_human_handoff_and_ticket.md             # ✅ 已实现（工单闭环 + bot 静默修复，附录有实现记录）
    stage-08-auth-hardening/01_auth_and_tenant_isolation.md     # ✅ 已实现（API Key/限流/幂等/发号强制化）
    stage-09-observability/01_metrics_and_evaluation.md         # ✅ 已实现（指标/回流/CI 门禁，附录有实现记录）
    stage-10-memory-multi-intent/01_multi_intent_and_memory.md # ✅ 已实现（多意图/任务治理/记忆双实现）
    stage-11-mcp-integration/01_mcp_tool_service.md             # ✅ 已实现（MCP 工具服务 + 客户端集成）
    stage-12-langfuse/01_langfuse_tracing.md                    # ✅ 已实现（链路追踪，附录有实现记录）
    stage-13-production-hardening/01_production_hardening.md    # ✅ 已实现（审计整改三批，附录有实现记录）
    stage-14-guardrails/01_content_safety_and_injection.md      # ✅ 已实现（注入防护/内容安全，附录有实现记录）
    stage-15-experience/01_realtime_csat_and_proactive.md       # ✅ 已实现（WS 实时/CSAT/生命周期，附录有实现记录）
    stage-16-kb-operations/01_kb_operations_backend.md          # ✅ 已实现（知识库运营后台，附录有实现记录）
    stage-17-llm-cost-control/01_semantic_cache_and_budget.md   # ✅ 已实现（语义缓存/预算熔断/分级路由，附录有实现记录，零回归）
    stage-18-ab-experiments/01_experiment_framework.md          # ✅ 已实现（A/B 实验框架：确定性分桶/变体注入/对比 SQL，附录有实现记录，零回归）
    stage-19-i18n-foundation/01_i18n_and_prompt_localization.md # ✅ 已实现（多语言地基：i18n 收口+提示词国际化，零回归）
    stage-20-memory-v2/01_structured_summary_and_context_discipline.md # ✅ 已实现（结构化会话摘要/单一表示不变式测试/MCP 大结果红线，附录有实现记录，零回归）
    post-stage-20-review-hardening/01_review_hardening_record.md # ✅ 实现记录（2026-07-27 全链路 review 六批整改：意图控制层/正确性/容量/延迟/演进/RAG 强化 WeKnora 对齐，274→300 tests）
    stage-21-smart-clarification/01_smart_clarification.md      # ✅ 已实现（智能澄清：UNKNOWN 轮次 top_k+上下文生成针对性澄清问句，无 Key 降级零回归，附录有实现记录）
    stage-22-readonly-diagnose-agent/01_readonly_diagnose_agent.md # ✅ 已实现（只读诊断 agent：ReAct 受约束变体，query_* 白名单+结构性终止+数字事实校验，默认关，附录有实现记录）

  database/
    chat_tables.md                  # 核心表设计 v2（字段类型/枚举/外键决策）
                                    # 可执行 DDL 见仓库根目录 sql/ddl/（脚本生成，勿手改）

  api/
    chat_api.md                     # API 契约 v2（含错误契约与后续 API 规划）

  prompts/
    skill_and_guardrails_standard.md  # 早期原则文档（已被 skills_design 细化取代，保留背景）

  testing/
    test_strategy.md

  ops/
    local_dev_and_runbook.md
```

---

## 3. Codex 使用方式

示例：

```text
请先阅读根目录 AGENTS.md 与 docs/chat/intent_taxonomy.md，
再阅读 docs/requirements/stage-04-llm-integration/01_llm_intent_and_generation.md。

本次只实现 Stage 04。
严格按文档实现，不要超范围实现。
完成后说明新增文件、修改文件、启动方式和验证方式。
```

---

## 4. 当前阶段

```text
Stage 01-19 已实现（各文档附录有实现记录与遗留说明）：
客服主闭环 + 转人工实时闭环 + 鉴权 + 生产加固 + 内容安全护栏 + 体验闭环 +
可观测（Prometheus + Langfuse + 质量闭环）+ 多语言地基（i18n）+ 知识库运营后台 +
LLM 成本控制（语义缓存/预算熔断/分级路由）+ A/B 实验框架（确定性分桶/变体注入/对比 SQL）。

📝 需求已写、待实现：（暂无——backlog 未拆需求项见 roadmap 3.6）

仍在 backlog（依赖真实模型/数据，roadmap 3.6）：多渠道接入、多模态、多语言的输入理解侧（分类/词表）。
另有真实环境联调项（LLM Key、MinerU、真实业务系统 MCP 对接、真实 embedding 阈值标定）
与运维搭建项（Grafana 看板、cron 任务、坐席前端）。
总体规划见：docs/architecture/roadmap.md
```
