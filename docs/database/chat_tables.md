# 聊天核心表设计（v2）

本文件定义聊天系统的 PostgreSQL 核心表。Stage 02 已落地 4 张核心表；
v2 补齐字段类型定义、枚举取值域、外键决策与索引说明（与 `app/models/` 现实现一致）。

---

## 1. 总原则

```text
1. 表结构通过 SQLAlchemy Model 定义。
2. 通过 Alembic migration 创建和变更。
3. 不手工直接创建核心业务表。
4. 所有表必须有 tenant_id，所有业务查询索引以 tenant_id 打头。
5. JSON 字段使用 PostgreSQL JSONB；会被应用原地修改的 JSONB 列必须用
   MutableDict/MutableList 包装（保证 SQLAlchemy 变更检测生效）。
6. 决策过程必须记录，方便后续训练和排查。
7. 【外键决策】核心表之间不建数据库外键：消息/日志表高频写入、后续可能分库分表，
   引用完整性由应用层保证（load_session_state 校验会话归属）。这是有意选择，
   新表沿用此约定并在文档注明。
8. 【软删除决策】消息与决策日志是 append-only 审计数据，不做软删除；
   会话生命周期用 chat_session.status 表达；知识库类表（Stage 06）用 status 字段停用。
```

## 2. 公共枚举取值域（单一定义，各文档引用勿再复制）

```text
chat_session.status   : active / closed / handoff（closed 会话来新消息自动重开，Stage 15）
chat_message.role     : user / assistant / system / agent（Stage 07 坐席回复）
chat_message.status 与 chat_decision_log.status（单轮处理状态 TurnStatus）:
    NEEDS_SLOT / NEEDS_CONFIRM / CONFIRMED / DONE / HANDOFF / ABORTED / FALLBACK / FAILED
    （Stage 07 扩展：HANDOFF_SILENT —— 人工接管期间 bot 静默轮次）
chat_handoff_ticket.reason（转人工触发原因，Stage 07；Stage 14 增 ABUSE）:
    USER_REQUEST / SKILL_RULE / PAYMENT_ISSUE / REPEATED_UNKNOWN / EXECUTION_FAILED / MANUAL / ABUSE
chat_handoff_ticket.status: PENDING → ASSIGNED → RESOLVED / CLOSED
chat_dialog_state.state（会话状态机 DialogStateValue）:
    IDLE / COLLECTING / CONFIRMING / DONE / ABORTED / HANDOFF / FAILED
    （Stage 05+ 扩展：AUTH_CHECK / EXECUTING / SUSPENDED / BLOCKED）
decision_source:
    RULE_KEYWORD / RULE_SLOT_ONLY / RULE_CONFIRM_GATE / RULE_FALLBACK /
    LLM / LLM_FALLBACK / VECTOR_ASSIST（Stage 04+）
```

> 注意：`status`（单轮处理状态）与 `state`（会话状态机）是两个枚举，不要混用。
> 枚举用 VARCHAR 承载 + 应用层常量约束，不用 PG ENUM 类型（避免加值需要 DDL）。

---

## 3. chat_session

用途：保存一次用户会话。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | VARCHAR(36) | PK | UUID 字符串；当前允许调用方传入，Stage 08 起强制服务端发号 |
| tenant_id | VARCHAR(64) | NOT NULL | 租户 ID |
| user_id | VARCHAR(64) | NOT NULL | 用户 ID（外部系统标识） |
| channel | VARCHAR(32) | NOT NULL | web/app/shop/whatsapp |
| status | VARCHAR(16) | NOT NULL DEFAULT 'active' | 见第 2 节枚举；转人工时由 save_turn 联动更新 |
| metadata_json | JSONB | NULL | 渠道侧扩展 |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL | 时间戳 |

索引：

```text
ix_chat_session_tenant_user    (tenant_id, user_id)
ix_chat_session_tenant_status  (tenant_id, status)
ix_chat_session_created_at     (created_at)          -- 运维用途，接受不带租户前缀
```

---

## 4. chat_message

用途：保存每条用户和 AI 消息（append-only，无 updated_at）。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | VARCHAR(36) | PK | UUID |
| tenant_id | VARCHAR(64) | NOT NULL | 租户 ID |
| session_id | VARCHAR(36) | NOT NULL（无 FK，见总原则 7） | 会话 ID |
| role | VARCHAR(16) | NOT NULL | user/assistant/system/agent（坐席，Stage 07） |
| content | TEXT | NOT NULL | 消息正文（请求层限长 4000 字符） |
| intent | VARCHAR(64) | NULL | 最终意图（DOMAIN.ACTION） |
| status | VARCHAR(32) | NULL | 单轮处理状态（TurnStatus） |
| slots_json | JSONB | NULL | 本轮槽位 |
| trace_id | VARCHAR(64) | NULL | 链路追踪 ID |
| metadata_json | JSONB | NULL | 扩展（Stage 06 起记录 answer_source/citations） |
| created_at | TIMESTAMPTZ | NOT NULL | 创建时间 |

索引：

```text
ix_chat_message_tenant_session_created  (tenant_id, session_id, created_at)
ix_chat_message_tenant_intent           (tenant_id, intent)
ix_chat_message_trace_id                (trace_id)   -- 运维排查用，接受不带租户前缀
```

---

## 5. chat_dialog_state

用途：保存当前会话状态机（每会话一条）。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | VARCHAR(36) | PK | UUID |
| tenant_id | VARCHAR(64) | NOT NULL | 租户 ID |
| session_id | VARCHAR(36) | NOT NULL | 会话 ID |
| state | VARCHAR(16) | NOT NULL DEFAULT 'IDLE' | 状态机状态，见第 2 节 |
| active_task_json | JSONB (MutableDict) | NULL | 当前任务 `{intent, kind, required_slots, collected_slots}`；Stage 05 起改存 task_id 引用 + 轻量快照 |
| task_stack_json | JSONB (MutableList) | NULL | 挂起任务栈（Stage 05 启用） |
| context_stacks_json | JSONB (MutableDict) | NULL | 上下文对象栈（Stage 05 启用） |
| version | INTEGER | NOT NULL DEFAULT 1 | 乐观锁（SQLAlchemy version_id_col）；冲突由 ChatService 转 409 CONCURRENT_UPDATE |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL | 时间戳 |

约束与索引：

```text
uq_chat_dialog_state_tenant_session  UNIQUE (tenant_id, session_id)  -- 隐含索引，覆盖主查询路径
（按 state 统计的索引留到 Stage 07 有真实查询需求时再加，避免无谓写放大）
```

---

## 6. chat_decision_log

用途：保存每轮 AI 决策过程（append-only）。失败轮次也必须留痕：
业务事务回滚时，由 ChatService 用**独立事务**写入带 error_json 的记录。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | VARCHAR(36) | PK | UUID |
| tenant_id | VARCHAR(64) | NOT NULL | 租户 ID |
| session_id | VARCHAR(36) | NOT NULL | 会话 ID |
| message_id | VARCHAR(36) | NULL | 用户消息 ID（失败轮次消息未落库时为空） |
| original_text | TEXT | NOT NULL | 原始文本 |
| normalized_text | TEXT | NULL | 归一化文本 |
| intent_result_json | JSONB | NULL | `{pred_label, confidence, decision_source, top_k, final_intent}` |
| slot_result_json | JSONB | NULL | 槽位抽取结果 |
| selected_skill | VARCHAR(64) | NULL | 命中的 Skill |
| status | VARCHAR(32) | NULL | 单轮处理状态 |
| decision_source | VARCHAR(32) | NULL | 见第 2 节枚举 |
| graph_trace_json | JSONB | NULL | 图执行轨迹 |
| latency_json | JSONB | NULL | 各阶段耗时（当前 total_ms，Stage 04 起分节点与 llm_ms/tokens） |
| error_json | JSONB | NULL | `{code, type}`——只存错误码与异常类型，不存详情（防敏感信息） |
| created_at | TIMESTAMPTZ | NOT NULL | 创建时间 |

索引：

```text
ix_chat_decision_log_tenant_session_created  (tenant_id, session_id, created_at)
ix_chat_decision_log_tenant_skill            (tenant_id, selected_skill)
ix_chat_decision_log_tenant_status           (tenant_id, status)
ix_chat_decision_log_created_at              (created_at)
```

Stage 06 已落地：新增 `retrieval_json JSONB` 列——记录查询词、FAQ/分块命中（id+score+来源路）、
是否拒答/降级、引用列表。**拒答轮次也必须留痕**（命中分数是调阈值与排查的关键数据）。

---

## 7. 知识库表（Stage 06，✅ 已落地，Milvus 后端）

设计原则：**PG 是知识库唯一事实来源**——原文、分块、embedding 全落 PG；
Milvus 只存「id + 租户/文档标识 + 向量」，是可用 `python -m app.kb.reindex` 随时重建的索引视图。
embedding 以 JSONB float 数组存储（不依赖 pgvector 扩展；未来接 pgvector 后端时再加 vector 列）。

### 7.1 kb_document

| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id | VARCHAR(36)/(64) | PK / 租户 |
| source_type | VARCHAR(32) | faq/policy/product/manual |
| title | VARCHAR(256) | 文档标题（引用展示用） |
| raw_content | TEXT | 原始内容（重建分块依据；Stage 16 后=草稿工作内容） |
| status | VARCHAR(16) | 状态机（Stage 16）：draft/pending_review/published/archived（兼容旧 active/disabled） |
| published_version | INTEGER | 当前线上版本号（Stage 16，可空）；**生效判据=非空且未 archived**（编辑不影响线上） |
| effective_from / expire_at | TIMESTAMPTZ | 定时生效/失效（Stage 16，可空，kb_schedule cron 自动 publish/archive） |
| needs_reindex | BOOLEAN | 向量后端写失败标记，reindex 兜底 |
| metadata_json | JSONB | 分类/商品 ID 等过滤维度 + review_log 审计（Stage 16） |
| created_at / updated_at | TIMESTAMPTZ | 时间戳 |

索引：`(tenant_id, status)`、`(tenant_id, source_type)`。

**kb_document_version（Stage 16，append-only 版本历史）**：id/tenant_id、document_id、version（同文档自增）、
title/raw_content/source_type（版本快照）、editor、note、created_at。索引 `(tenant_id, document_id, version)`。
每次 create/edit/rollback 追加一行；rollback = 把历史版本内容记为新版本并重新发布。

### 7.2 kb_chunk（append-only，文档更新整篇重建）

| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id | VARCHAR(36)/(64) | PK / 租户 |
| document_id | VARCHAR(36) | 所属文档（无 FK，见总原则 7） |
| chunk_index | INTEGER | 分块序号 |
| content | TEXT | 分块内容 |
| embedding_json | JSONB | 向量（float 数组，维度=EMBEDDING_DIM） |
| token_count | INTEGER | 预留 |
| metadata_json | JSONB | 过滤维度 |
| created_at | TIMESTAMPTZ | 创建时间 |

索引：`(tenant_id, document_id)`。

### 7.3 faq_entry

| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id | VARCHAR(36)/(64) | PK / 租户 |
| question | VARCHAR(512) | 标准问题 |
| answer | TEXT | 标准答案（命中直接返回，零幻觉） |
| question_embedding_json | JSONB | 问题向量 |
| category | VARCHAR(64) | 分类 |
| status | VARCHAR(16) | active / disabled |
| hit_count | INTEGER | 命中次数（原子自增，运营观测） |
| needs_reindex | BOOLEAN | 向量后端写失败标记（Stage 13，对齐 kb_document），reindex 兜底 |
| created_at / updated_at | TIMESTAMPTZ | 时间戳 |

索引：`(tenant_id, status)`、`(tenant_id, category)`。

### 7.4 Milvus 侧（非 PG，索引视图）

```text
kb_chunk_v1：id(string PK) + vector(COSINE, dim=EMBEDDING_DIM) + 动态字段 tenant_id/document_id
kb_faq_v1  ：id(string PK) + vector(COSINE) + 动态字段 tenant_id
所有检索强制 tenant_id term 过滤；表达式做白名单转义防注入。
```

---

## 8. 任务与工具审计表（Stage 05，✅ 已落地）

### 8.1 chat_task（任务生命周期）

| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id / session_id | VARCHAR | PK / 租户 / 会话 |
| intent / skill_id | VARCHAR(64) | 任务意图与技能 |
| status | VARCHAR(16) | COLLECTING / CONFIRMING / EXECUTING / DONE / ABORTED / FAILED / SUSPENDED（挂起入栈） |
| collected_slots_json | JSONB (MutableDict) | 已收集槽位 |
| confirmed_at / executed_at | TIMESTAMPTZ | 确认/执行时间（审计关键点） |
| result_json | JSONB | 执行结果（工单号等） |
| version | INTEGER | 乐观锁。注意：ActionExecutor 用独立事务落 EXECUTING 标记后，主事务更新前必须 refresh 行版本 |
| created_at / updated_at | TIMESTAMPTZ | 时间戳 |

索引：`(tenant_id, session_id)`、`(tenant_id, status)`。
`chat_dialog_state.active_task_json` 存轻量快照 + `task_id` 引用；行状态由 save_turn 统一同步。

### 8.2 chat_tool_call（工具调用审计，append-only）

| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id / session_id / task_id | VARCHAR | 标识（task_id 可空） |
| tool_id | VARCHAR(64) | 工具标识 |
| request_json | JSONB | 入参（**必须脱敏**：手机号打码） |
| response_json | JSONB | 返回数据（失败为空） |
| ok / error_code / latency_ms | BOOL/VARCHAR/FLOAT | 结果与耗时 |
| created_at | TIMESTAMPTZ | 创建时间 |

索引：`(tenant_id, session_id)`、`(tenant_id, tool_id)`。

---

### 8.3 user_memory（Stage 10，✅ 已落地）

用户长期记忆（自研 LocalMemoryProvider 存储；mem0 模式下长期记忆在 mem0 侧，本表不用）。

| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id / user_id | VARCHAR | 标识（严格 tenant+user 隔离） |
| kind | VARCHAR(16) | profile 画像 / preference 偏好 / fact 事实 |
| content | TEXT | 记忆内容（LLM 抽取，内容级去重） |
| source_session_id | VARCHAR(36) | 来源会话 |
| status | VARCHAR(16) | active / disabled（合规清除入口） |
| created_at / updated_at | TIMESTAMPTZ | 时间戳 |

索引：`(tenant_id, user_id, status)`。
会话摘要不在本表：存 `chat_session.metadata_json.memory_summary`（含已覆盖消息数，增量续写）。

> 关联修订（Stage 10）：`chat_message.created_at` 默认值改为 `clock_timestamp()`（语句级时间戳）——
> now() 是事务时间戳，同一轮的用户消息与 AI 回复同事务落库会得到相同 created_at，
> 历史排序先后不稳定（migration `df385246e05d`）。

---

## 9. 索引审计记录（2026-07-02，代码查询路径 ↔ 索引逐一核对）

结论：全部高频查询路径均有匹配索引；本次审计新增 2 个、移除 1 个。

### 9.1 本次变更（migration `4f5b61d63ede`）

| 变更 | 索引 | 原因 |
|---|---|---|
| ➕ 新增 | `ix_kb_chunk_content_trgm`（GIN, gin_trgm_ops） | RAG 关键词召回路 `content ILIKE '%kw%'` 是中缀模糊匹配，btree 无法加速；实测 5 万行下 GIN 命中 1.35ms（顺扫约 60ms+）。**依赖 pg_trgm 扩展**（PG13+ trusted，迁移中自动创建）；zh_CN.UTF-8 locale 下中文三元组正常生成（已验证） |
| ➕ 新增 | `ix_product_item_name_trgm`（GIN, gin_trgm_ops） | 商品名检索 `name ILIKE '%kw%'` 同上 |
| ➖ 移除 | `ix_product_item_tenant_name`（btree） | 唯一使用 name 的查询是 ILIKE 中缀匹配，btree 对该形态完全无效，属无效写放大 |

### 9.2 高频查询路径覆盖核对（摘要）

```text
chat_message  历史分页(tenant,session,created_at 倒序+游标) → ix_..._tenant_session_created ✅
chat_dialog_state 按会话读写                              → uq_..._tenant_session ✅
chat_task     按 id + 租户校验                            → PK ✅
kb_chunk      按文档删除/按租户列取                        → ix_..._tenant_document（tenant 前缀）✅
kb_document   list_active(tenant,status)                  → ix_..._tenant_status ✅
faq_entry     get_by_ids / list_active                    → PK / ix_..._tenant_status ✅
product_item  get_by_code(tenant,code)                    → ix_..._tenant_code ✅
chat_session  list_by_user(tenant,user)                   → ix_..._tenant_user ✅
              （排序列 created_at 未入索引：单用户会话量小，暂不扩展）
```

### 9.3 保留的"当前无查询"的分析型索引（有意保留，非遗漏）

```text
chat_message.tenant_intent / trace_id、chat_session.tenant_status、
chat_decision_log 全部索引、chat_task.tenant_status/tenant_session、
chat_tool_call 全部索引、faq_entry.tenant_category、裸 created_at 两处
—— 服务于运营统计 / 审计排查 / Stage 07-09 规划查询；写放大可接受，
   若压测发现写入瓶颈优先从裸 created_at 索引裁剪。
```

---

## 10. 人工接管表（Stage 07，✅ 已落地）

### 10.1 chat_handoff_ticket（转人工工单）

用途：转人工工单。核心设计是 `context_json` **上下文移交包**（任务栈快照 / 已收集槽位 /
最近 8 条消息摘要，统一脱敏）——坐席打开工单即懂上下文，不让用户复述。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | VARCHAR(36) | PK | UUID |
| tenant_id | VARCHAR(64) | NOT NULL | 租户 |
| session_id | VARCHAR(36) | NOT NULL | 会话 ID |
| user_id | VARCHAR(64) | NOT NULL | 用户 ID |
| reason | VARCHAR(32) | NOT NULL | 触发原因（枚举见第 2 节） |
| source_intent | VARCHAR(64) | NULL | 触发时的意图码 |
| status | VARCHAR(16) | NOT NULL DEFAULT 'PENDING' | PENDING→ASSIGNED→RESOLVED/CLOSED |
| assignee | VARCHAR(64) | NULL | 坐席标识（claim 时写入） |
| context_json | JSONB | NULL | 上下文移交包（脱敏） |
| resolved_at | TIMESTAMPTZ | NULL | 解决时间 |
| created_at / updated_at | TIMESTAMPTZ | NOT NULL | 通用时间戳 |

索引：
- `ix_chat_handoff_tenant_status (tenant_id, status)`——坐席队列查询（status 过滤 + created_at 倒序）
- `ix_chat_handoff_tenant_session (tenant_id, session_id)`——按会话取工单
- `uq_chat_handoff_open_session (tenant_id, session_id) WHERE status IN ('PENDING','ASSIGNED')`
  ——**部分唯一索引承载幂等**：同会话同时最多一张未关闭工单（应用层 ensure_ticket 先查后建，
  索引兜底并发窗口）

配套联动（不加新列，复用已有表）：
- `chat_session.status = handoff`——bot 静默开关（load_session_state 短路，resolve 归还后恢复 active）
- `chat_message.role = agent`——坐席回复消息
- `chat_dialog_state.context_stacks_json.unknown_streak`——连续兜底计数（REPEATED_UNKNOWN 触发依据）
- `chat_decision_log.retrieval_json.handoff`——建单轮记录 `{reason, ticket_id, created}`

---

## 11. 可观测与反馈（Stage 09，✅ 已落地）

### 11.1 chat_feedback（用户反馈，append-only）

用途：用户对 AI/坐席回复的点赞/点踩。down 评价自动进入数据回流待审导出
（`scripts/export_review_set.py`，经 trace_id 关联同轮用户消息）。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | VARCHAR(36) | PK | UUID |
| tenant_id | VARCHAR(64) | NOT NULL | 租户 |
| session_id | VARCHAR(36) | NOT NULL | 会话 ID |
| message_id | VARCHAR(36) | NOT NULL | 被评价的消息（必须属于该会话且 role∈assistant/agent，路由层双校验防跨会话投毒） |
| rating | VARCHAR(8) | NOT NULL | up / down |
| comment | TEXT | NULL | 补充说明 |
| created_at | TIMESTAMPTZ | NOT NULL | 创建时间 |

索引：`ix_chat_feedback_tenant_rating (tenant_id, rating, created_at)`（回流导出按 down 过滤）；
`uq_chat_feedback_message (tenant_id, message_id) UNIQUE`（同消息重复评价更新而非新增）。

### 11.2 chat_csat（会话满意度，Stage 15，append-only）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id | VARCHAR(36)/(64) | PK / NOT NULL | UUID / 租户 |
| session_id | VARCHAR(36) | NOT NULL | 会话 ID |
| user_id | VARCHAR(64) | NOT NULL | 用户 ID |
| score | INTEGER | NOT NULL | 1-5（口语映射见 app/chat/csat.py） |
| comment | TEXT | NULL | 附言（预留） |
| trigger | VARCHAR(32) | NOT NULL | handoff_resolve / session_close |
| created_at | TIMESTAMPTZ | NOT NULL | 创建时间 |

索引：`(tenant_id, created_at)`、`(tenant_id, session_id)`。
询问标记走 `chat_dialog_state.context_stacks_json.csat_pending`（一次性）；
低分（<=2）会话自动进 export_review_set 待审导出。

### 11.3 quality_daily（物化视图，非表）

按 租户×天 聚合 `chat_decision_log`：turns/sessions/done/fallback/handoff/failed/
low_conf（confidence<0.6）/rag_refused/p50/p95 延迟。
Stage 15 起含 csat_avg/csat_count（chat_csat 日聚合 LEFT JOIN）。
高频看板查询不扫原表；`scripts/refresh_quality_views.py` 用 REFRESH CONCURRENTLY 刷新
（依赖 `uq_quality_daily (tenant_id, day)` 唯一索引，不锁读）。
指标口径与查询 SQL 的单一定义见 `docs/ops/quality_queries.md`。

字段定义见对应阶段需求文档，落地时回填本文件。
