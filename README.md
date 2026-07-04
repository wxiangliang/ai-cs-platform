# ai-cs-platform

> Production-grade multi-tenant **AI customer-service agent** platform.
> 生产级多租户 **AI 客服 Agent** 平台。
>
> **FastAPI · LangChain / LangGraph · PostgreSQL · Redis · Milvus**

**Languages / 语言:** [English](#english) · [中文](#中文)

> ⚠️ The AI layer (LLM / embeddings / MCP tools) **degrades gracefully to rules/templates/mock when unconfigured**, so the whole platform runs end-to-end in dev mode with **zero external API keys**. Real model endpoints are an integration step, not a hard dependency.
> ⚠️ AI 层（LLM / embedding / MCP 工具）**未配置时自动降级为规则/模板/mock**，所以整套系统在开发模式下**零外部 Key**即可端到端跑通。接真实模型端点是联调项，不是硬依赖。

---

## English

### Overview

`ai-cs-platform` is a customer-service chatbot backend built around a **LangGraph decision graph** (8 linear nodes + 5 reply branches). Every turn is deterministic, replayable (decision log), multi-tenant, and safe by construction: **the LLM never has direct write authority** — refunds / cancellations / address changes all pass through a confirmation gate and a single idempotent write entry point.

It is developed in **stages (01–19)**; each stage has a requirement doc plus an implementation-record appendix under `docs/requirements/`.

### Highlights

- **Intent understanding** — hybrid classifier: rule control layer → SetFit semantic model (29 classes) → LLM second-opinion on low-confidence hard cases; multi-intent splitting with per-segment slot extraction.
- **Dialog & tasks** — multi-turn slot filling, dialog state machine, task stack with suspend/resume, task governance (TTL, max-asks → handoff).
- **Safe writes** — confirmation gate + `ActionExecutor` as the **only** write path (atomic execution claim, replay protection, independent audit transaction).
- **RAG / FAQ knowledge base** — Milvus hybrid retrieval (vector + jieba keyword RRF) with reranking, parent/child chunking, ambiguity detection, and **refuse-don't-fabricate** guardrails. PostgreSQL is the single source of truth.
- **Product catalog** — price/stock served from the product table (**never** from RAG — a red line against stale-fact loss).
- **Human handoff** — ticket lifecycle, context handoff package, agent APIs, silent-repair while handed off, real-time **WebSocket** for both user and agent sides.
- **Content safety** — injection defense, abuse grading, output leakage guard, prompt-injection wrapping at every LLM concatenation point.
- **Memory** — short-term window + LLM session summary + long-term facts (`local` or `mem0`).
- **Auth & multi-tenancy** — API key per tenant, scope separation, sliding-window rate limiting, idempotency keys.
- **Observability** — Prometheus metrics, Langfuse tracing, quality dashboard (materialized view), data-flywheel export, decision replay.
- **Cost control (Stage 17)** — semantic cache, per-tenant token budget circuit breaker, model tier routing.
- **A/B experiments (Stage 18)** — deterministic bucketing, whitelist parameter overrides, split-by-variant comparison SQL.
- **i18n foundation (Stage 19)** — user-facing copy funneled through `t()`; prompts instruct the model to answer in the user's language.

### Architecture

```
Request → API route → ChatService → LangGraph
  load_session_state → preprocess → guardrail_check → intent_classify
    → slot_extract → confirmation_parse → dialog_state_resolve → skill_resolve
      → [ response_generate | rag_answer | product_answer | tool_invoke | action_execute ]
        → save_turn (persist message + decision log)
```

- Route layer only wires params; business logic lives in services / decision layer.
- `db_session` is injected via graph config so the state stays serializable.
- All external I/O (DB / Redis / LLM / Milvus / MCP) has timeouts and **fails open** — degrade, never break the main chain.

### Implemented stages

| Stage | Theme |
|------:|-------|
| 01–02 | Foundation framework · chat core tables |
| 03–04 | Main chat chain · LLM integration (SetFit + factory + polish) |
| 05–06 | Tool layer & confirmation gate · RAG/FAQ/vector KB (+ parsing, routing, pipeline v2) |
| 07–08 | Human handoff & tickets · Auth & multi-tenant hardening |
| 09 | Observability & evaluation (Prometheus / quality views / CI gate / SSE) |
| 10 | Multi-intent · task governance · memory |
| 11–12 | MCP tool service · Langfuse tracing |
| 13–14 | Production hardening · content-safety guardrails |
| 15–16 | Experience loop (WS / CSAT / lifecycle) · KB operations backend |
| 17–18 | LLM cost control · A/B experiment framework |
| 19 | Multilingual foundation (i18n) |

### Quick start

Requires **Python 3.12 + uv**, **PostgreSQL** and **Redis** (mandatory), **Milvus 2.5+** (optional — set `KB_ENABLED=false` to skip).

```bash
docker compose up -d                  # PG + Redis (add --profile kb for Milvus)
cp .env.example .env                  # dev mode runs with zero config
uv sync                               # install deps
uv run alembic upgrade head           # create tables
uv run uvicorn app.main:app --reload  # start
curl http://localhost:8000/api/health
```

Send a message (dev mode — tenant from body):

```bash
curl -X POST http://localhost:8000/api/chat/sessions/demo-1/messages \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"t1","user_id":"u1","message":"我要退款，订单号 SO12345678"}'
```

Enable the knowledge base (dev pseudo-vectors), then reindex:

```bash
# in .env: KB_ENABLED=true, EMBEDDING_PROVIDER=hash, FAQ_HIT_THRESHOLD=0.6, RAG_MIN_SCORE=0.2
uv run python -m app.kb.reindex --tenant t1
```

### Quality gate

```bash
uv run ruff check app tests scripts && uv run mypy app && uv run pytest
```

Current status: **ruff clean · mypy clean · 261 tests passing.** CI: `.github/workflows/ci.yml` (includes intent / multi-intent / RAG eval gates).

### Documentation

| Entry | Content |
|---|---|
| `AGENTS.md` / `CLAUDE.md` | Global AI-collaboration rules / stage progress & directory map |
| `docs/architecture/roadmap.md` | Roadmap (stage status + backlog) |
| `docs/architecture/system_overview.md` | Layered architecture & LangGraph main chain |
| `docs/requirements/stage-xx-*/` | Per-stage requirements + implementation appendix |
| `docs/api/chat_api.md` | API contract (REST / SSE / WebSocket) |
| `docs/database/chat_tables.md` | Schema design (DDL in `sql/ddl/`, generated — don't hand-edit) |
| `docs/ops/local_dev_and_runbook.md` | Local dev & runbook |

### Known limitations / open issues

- **Real model calibration** — dev mode uses `EMBEDDING_PROVIDER=hash` (deterministic pseudo-vectors, dev-only, forbidden in prod). Retrieval thresholds, semantic-cache generalization, and token-budget / model-tier splits all need **real embeddings + real-traffic calibration**.
- **LLM endpoint** — real LLM answer polishing / hard-case second-opinion are only smoke-tested; without an API key everything runs on rules/templates.
- **No frontend** — agent workbench, KB operations UI, and experiment UI are **API-only** by design; only backend + CLI shipped.
- **Multilingual is output-side only** — i18n covers user-facing copy and "answer in the user's language". The **input-understanding side** (intent classifier multilingual training set, guardrail lexicon, slot regex) is still Chinese-centric.
- **A/B significance & scope** — the framework splits data and computes rates + sample size, but **significance conclusions require real traffic**; experiment intent-domain scope is currently record-only (bucketing happens before intent classification).
- **Ops/deploy items** — Grafana dashboards, cron scheduling (idle-session close, KB schedule, quality-view refresh), agent real-time push at the gateway layer, and a model-bearing CI runner for the SetFit gate are not wired here.

### Tech stack

Python 3.12 (uv-pinned) · SQLAlchemy 2.x async + asyncpg · Alembic · redis.asyncio · Pydantic v2 + pydantic-settings · LangChain / LangGraph · Milvus · Prometheus · Langfuse · FastMCP.

---

## 中文

### 项目简介

`ai-cs-platform` 是一个以 **LangGraph 决策图**（8 线性节点 + 5 回复分支）为核心的 AI 客服后端。每一轮对话都是确定性、可回放（决策日志）、多租户，且从架构上保证安全：**LLM 永远没有直接写权限**——退款 / 取消 / 改地址等写操作一律经过确认门和唯一幂等写入口。

项目按 **Stage 01–19 分阶段推进**，每个阶段在 `docs/requirements/` 下都有需求文档 + 实现记录附录。

### 核心能力

- **意图理解** —— 混合分类器：规则控制层 → SetFit 语义模型（29 类）→ 低置信难例 LLM 二判；多意图切分 + 每段独立抽槽。
- **对话与任务** —— 多轮补槽、对话状态机、任务栈挂起/恢复、任务治理（TTL、追问超限转人工）。
- **安全写操作** —— 确认门 + `ActionExecutor` 作为**唯一**写入口（原子拿执行权、防重放、独立事务留痕）。
- **RAG / FAQ 知识库** —— Milvus 混合检索（向量 + jieba 关键词 RRF）+ 重排、父子分块、歧义检测，**拒答不编造**红线；PG 为唯一事实来源。
- **商品库** —— 价格/库存走商品表（**绝不**走 RAG——防过期资损红线）。
- **人工接管** —— 工单生命周期、上下文移交包、坐席 API、静默修复、用户端+坐席端**实时 WebSocket**。
- **内容安全护栏** —— 注入防护、辱骂分级、输出泄漏护栏，全部 LLM 拼接点防注入包裹。
- **记忆系统** —— 短期窗口 + LLM 会话摘要 + 长期事实（`local` 或 `mem0`）。
- **鉴权与多租户** —— 每租户 API Key、scope 分离、滑动窗口限流、幂等键。
- **可观测** —— Prometheus 指标、Langfuse 链路追踪、质量看板（物化视图）、数据回流、决策回放。
- **成本控制（Stage 17）** —— 语义缓存、租户 token 预算熔断、模型分级路由。
- **A/B 实验（Stage 18）** —— 确定性分桶、白名单参数覆盖、按变体切分对比 SQL。
- **多语言地基（Stage 19）** —— 面向用户文案收口 `t()`；提示词让模型用用户语言作答。

### 分层架构

```
请求 → API 路由 → ChatService → LangGraph
  load_session_state → preprocess → guardrail_check → intent_classify
    → slot_extract → confirmation_parse → dialog_state_resolve → skill_resolve
      → [ response_generate | rag_answer | product_answer | tool_invoke | action_execute ]
        → save_turn（落消息 + 决策日志）
```

- 路由层只接参数，业务逻辑在 service / 决策层。
- `db_session` 经图 config 注入，保证 state 可序列化。
- 所有外部访问（DB / Redis / LLM / Milvus / MCP）都有超时并**故障放行**——降级而非打断主链路。

### 已实现阶段

| 阶段 | 主题 |
|------:|------|
| 01–02 | 基础框架 · 聊天核心表 |
| 03–04 | 聊天主链路 · LLM 接入（SetFit + 工厂 + 润色） |
| 05–06 | 工具层与确认门 · RAG/FAQ/向量库（+ 解析、路由、检索 v2） |
| 07–08 | 人工接管与工单 · 鉴权多租户加固 |
| 09 | 可观测与评估（Prometheus / 质量看板 / CI 门禁 / SSE） |
| 10 | 多意图 · 任务治理 · 记忆 |
| 11–12 | MCP 工具服务 · Langfuse 链路追踪 |
| 13–14 | 生产加固 · 内容安全护栏 |
| 15–16 | 体验闭环（WS / CSAT / 生命周期）· 知识库运营后台 |
| 17–18 | LLM 成本控制 · A/B 实验框架 |
| 19 | 多语言地基（i18n） |

### 快速开始

依赖 **Python 3.12 + uv**、**PostgreSQL** 与 **Redis**（必需）、**Milvus 2.5+**（可选——`KB_ENABLED=false` 可关闭）。

```bash
docker compose up -d                  # PG + Redis（--profile kb 加 Milvus）
cp .env.example .env                  # 开发模式零配置可跑
uv sync                               # 安装依赖
uv run alembic upgrade head           # 建表
uv run uvicorn app.main:app --reload  # 启动
curl http://localhost:8000/api/health
```

发一条消息（开发模式，tenant 从请求体取）：

```bash
curl -X POST http://localhost:8000/api/chat/sessions/demo-1/messages \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"t1","user_id":"u1","message":"我要退款，订单号 SO12345678"}'
```

启用知识库（开发伪向量）后重建索引：

```bash
# .env 中：KB_ENABLED=true，EMBEDDING_PROVIDER=hash，FAQ_HIT_THRESHOLD=0.6，RAG_MIN_SCORE=0.2
uv run python -m app.kb.reindex --tenant t1
```

### 质量门禁

```bash
uv run ruff check app tests scripts && uv run mypy app && uv run pytest
```

当前状态：**ruff 干净 · mypy 干净 · 261 项测试通过。** CI 见 `.github/workflows/ci.yml`（含意图 / 多意图 / RAG 评估门禁）。

### 文档入口

| 入口 | 内容 |
|---|---|
| `AGENTS.md` / `CLAUDE.md` | AI 协作全局规则 / 阶段进度与目录结构 |
| `docs/architecture/roadmap.md` | 总体路线图（阶段状态 + backlog） |
| `docs/architecture/system_overview.md` | 分层架构与 LangGraph 主链路 |
| `docs/requirements/stage-xx-*/` | 各阶段需求 + 实现记录附录 |
| `docs/api/chat_api.md` | API 契约（REST / SSE / WebSocket） |
| `docs/database/chat_tables.md` | 表设计（DDL 见 `sql/ddl/`，生成物勿手改） |
| `docs/ops/local_dev_and_runbook.md` | 本地开发与运维手册 |

### 现存问题 / 已知限制

- **真实模型标定** —— 开发模式用 `EMBEDDING_PROVIDER=hash`（确定性伪向量，仅开发，生产禁用）。检索阈值、语义缓存泛化、token 预算 / 模型分级划分都需要**真实 embedding + 真实流量标定**。
- **LLM 端点** —— 真实 LLM 润色 / 难例二判仅做过冒烟；无 API Key 时全部走规则/模板。
- **无前端** —— 坐席工作台、知识库运营界面、实验界面按设计是**纯 API**；只交付后端 + CLI。
- **多语言仅输出侧** —— i18n 覆盖面向用户文案与「用用户语言作答」。**输入理解侧**（意图分类多语言训练集、护栏词表、槽位正则）仍以中文为主。
- **A/B 显著性与作用域** —— 框架负责切数据、算比率与样本量，但**显著性结论需真实流量**；实验的意图域作用域目前仅记录（分桶发生在意图分类之前）。
- **运维/部署项** —— Grafana 看板、cron 定时任务（空闲会话关闭、知识库定时、质量视图刷新）、坐席实时推送（网关层）、SetFit 门禁的带模型 CI runner 未在此接入。

### 技术栈

Python 3.12（uv pin）· SQLAlchemy 2.x async + asyncpg · Alembic · redis.asyncio · Pydantic v2 + pydantic-settings · LangChain / LangGraph · Milvus · Prometheus · Langfuse · FastMCP。

---

## License

Not yet specified. / 暂未指定。
