# AI Coding Instructions

本项目是 `ai-cs-platform`，用于实现基于 **FastAPI、LangChain、LangGraph、PostgreSQL、Redis** 的 AI 客服聊天系统。

本文件是 AI/Codex 的**全局入口规则**。  
详细需求不要全部写在这里，必须放到 `docs/` 目录中，并按阶段拆分执行。

---

## 1. AI/Codex 必读顺序

后续所有 AI/Codex 代码生成，必须优先阅读并遵守以下文档：

1. `docs/00_docs_management_standard.md`
2. `docs/README.md`
3. `docs/architecture/roadmap.md`（总体路线图 Stage 01-15 + backlog）与
   `docs/architecture/system_overview.md`（分层架构与主链路）
4. `docs/chat/intent_taxonomy.md`（意图体系单一事实来源，改意图必先改它）
5. 与本次任务相关的阶段需求文档 `docs/requirements/stage-xx-*/`
   （各阶段文档末尾附实现记录附录——改已实现功能前必读）

如果任务涉及整体聊天框架、数据库或 API，还需要同时参考：

1. `docs/requirements/chat-framework/fastapi_langgraph_chat_framework.md`
2. `docs/database/chat_tables.md`
3. `docs/api/chat_api.md`

---

## 2. 通用代码要求

```text
1. 代码必须高可用、高性能、可扩展、可维护。
2. 核心类、核心方法、复杂逻辑必须有中文注释。
3. FastAPI 只做 API 接入，不写复杂业务流程。
4. LangGraph 负责编排聊天主流程。
5. PostgreSQL 用 async SQLAlchemy。
6. Redis 用 async redis client。
7. 所有外部资源访问必须有超时、异常处理和日志。
8. 写操作必须走确认门 + ActionExecutor（唯一写入口），不能让 LLM 直接执行。
9. 外部依赖（LLM/Milvus/Redis/MCP/Langfuse）故障一律降级，不打断聊天主链路。
10. 所有关键决策必须记录 decision_log；敏感信息（手机号/地址）落库前脱敏。
```

---

## 3. 禁止事项

```text
1. 禁止把所有逻辑写进 route。
2. 禁止手写 SQL 绕过 Alembic 创建核心业务表。
3. 禁止把数据库地址、Redis 地址、API Key 写死在代码里。
4. 禁止在日志中打印明文 API Key、手机号、地址、订单敏感信息。
5. 禁止让 LLM 直接决定退款、取消订单、改地址等写操作。
6. 禁止绕过 ToolProvider/ActionExecutor 直接调用业务工具。
7. 禁止一次性跨多个阶段乱改，必须按阶段需求文档执行；
   改已实现功能：先改需求文档（含附录），再同步改代码。
8. 禁止绕过 guardrails.md 机器可读规则库手写护栏词表/正则（单一事实来源）。
```

---

## 4. 推荐执行方式

每次让 AI/Codex 写代码时，都使用下面格式：

```text
请先阅读根目录 AGENTS.md，
再阅读 docs/requirements/stage-xx-xxx/xx_xxx.md。

本次只实现 Stage XX：xxx。
严格按文档实现，不要超范围实现。
要求代码有中文注释，模块边界清晰。
完成后说明新增文件、修改文件、启动方式、验证方式。
```

---

## 5. 阶段状态（2026-07-04）

```text
Stage 01-15 全部已实现（明细与状态见 docs/architecture/roadmap.md 第 2 节）：
基础框架 / 核心表 / LangGraph 主链路 / LLM+SetFit 意图 / 工具与确认门 /
RAG+FAQ+Milvus / 人工接管闭环 / 鉴权多租户 / 可观测评估平台 / 多意图与记忆 /
MCP 工具服务 / Langfuse 追踪 / 生产加固 / 内容安全护栏 / 体验闭环（WS/CSAT/生命周期）。

📝 需求已写待实现（当前 mock/开发模式即可落生产级）：
Stage 16 知识库运营后台 / 17 LLM 成本控制（语义缓存+预算+分级路由）/ 18 A/B 实验框架 /
19 多语言地基（i18n 文案收口 + 提示词国际化，纯重构零回归）。

后续工作三类（不新增阶段前不要动手实现）：
1. backlog（roadmap 3.6：多渠道/知识库后台/多模态/多语言/成本控制/AB 实验）；
2. 真实环境联调（LLM Key / MinerU / 真实业务系统换 MCP 服务端 / 真实 embedding 阈值标定）；
3. 运维搭建（Grafana 看板 / close_idle_sessions 进 cron / 坐席工作台前端）。
```

---

## 6. 修改既有功能的原则

```text
1. 先读对应阶段需求文档及其实现记录附录，再读代码。
2. 需求变更：先改需求文档，再同步改代码，最后更新附录与 CLAUDE.md 阶段进度。
3. 改意图体系：先改 docs/chat/intent_taxonomy.md；改护栏规则：改 guardrails.md 机器可读表。
4. 动表结构：改 Model → alembic autogenerate → 更新 docs/database/chat_tables.md
   → uv run python scripts/export_table_ddl.py 重导 DDL。
5. 提交前必过：uv run ruff check app tests scripts && uv run mypy app && uv run pytest。
6. 不要为了「功能看起来完整」破坏模块边界。
```
