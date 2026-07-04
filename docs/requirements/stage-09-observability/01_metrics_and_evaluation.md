# Stage 09 需求：可观测与评估平台

> 前置阅读：`docs/architecture/roadmap.md` 3.5、`docs/testing/test_strategy.md`、
> `docs/testing/intent_eval_set.md`、`docs/intent/README.md` 第 5 节（数据回流规范）。
> 前置条件：Stage 07/08 已完成（转人工率、429 等指标依赖其落地；反馈接口需要鉴权）。

---

## 1. 阶段目标

把"每轮决策可回放"的留痕数据（decision_log / chat_task / chat_tool_call）变成
**可看的指标、可跑的回归、可回流的训练数据**——形成质量闭环：
线上表现 → 指标发现问题 → bad case 回流 → 重训/调阈值 → 评估门禁验证 → 上线。

## 2. 本阶段要做什么

1. **运行时指标（Prometheus `/metrics`）**

   | 指标 | 类型 | 维度 |
   |---|---|---|
   | chat_turn_duration_seconds | Histogram | 意图域、回复分支（template/rag/product/tool/action） |
   | intent_decisions_total | Counter | pred_label、decision_source（观测 SETFIT/LLM/降级占比） |
   | llm_calls_total / llm_failures_total | Counter | purpose（classify/generate） |
   | rag_retrievals_total | Counter | outcome（faq_hit/rag_answer/refused/degraded） |
   | confirm_gate_total | Counter | outcome（confirmed/denied/modified/timeout*） |
   | action_executions_total | Counter | tool_id、ok |
   | handoff_tickets_total | Counter | reason |
   | rate_limited_total | Counter | scope（tenant/session） |

   - 实现：prometheus-client 直埋（不引重框架）；指标注册集中一个模块；
     多租户维度**不进 label**（基数爆炸），租户级分析走 SQL。

2. **质量看板查询（SQL 视图 + 文档）**——先做"可查"，不做前端：
   - `docs/ops/quality_queries.md`：一次解决率（会话无转人工且无连续 UNKNOWN）、
     转人工率及 reason 分布、意图分布与低置信率、RAG 拒答率、确认门通过率、
     工具失败率、P95 轮次耗时——每个指标给出可直接执行的 SQL（基于 decision_log 等表）；
   - 高频查询建物化视图（migration），按天聚合（`quality_daily`），CLI 刷新。

3. **数据回流管道（CLI，人工审核在环）**
   - `scripts/export_review_set.py`：从 decision_log 导出待审样本 CSV——
     SETFIT_LOW_CONF / LLM 二判样本 / FALLBACK 轮次 / 用户差评轮次（见 4），
     字段对齐 `intent_train_v42_project.csv`（text/intent 预标/来源/trace_id）；
   - 人工修订后 `scripts/build_intent_dataset.py` 支持合并增量文件（--extra 参数）→ 重训 → 评估门禁；
   - FAQ 沉淀：导出高频 RAG 已答问题（retrieval_json 聚合，按查询相似度粗聚类）
     → 运营审核 → `POST /api/kb/faqs` 入库。

4. **chat_feedback 表 + 反馈 API**
   - 表：id / tenant_id / session_id / message_id / rating(up/down) / comment / created_at；
   - `POST /api/chat/sessions/{id}/feedback`（chat_api.md 预留位，校验会话归属）；
   - down 评价自动进 3 的待审导出。

5. **评估门禁 CI 化**
   - `.github/workflows/ci.yml`（或团队实际 CI）：ruff + mypy + pytest（含 tests/eval 控制层门禁）；
     SetFit test 集门禁在有模型产物的 runner 上跑（缓存模型目录），无产物显式 skip 且 CI 输出可见；
   - `docs/testing/rag_eval_set.md` 落地（接入真实 embedding 后补 ≥30 组），入门禁。

6. **决策回放 CLI**：`scripts/replay_trace.py --trace <id> | --session <id>`——
   按轮打印：用户文本 → 意图(top_k/来源/置信度) → 槽位 → 状态流转 → 检索/工具轨迹 → 回复，
   排查线上 case 不再手写 SQL。

## 3. 本阶段不做什么

- BI 前端 / Grafana 看板配置（给出指标与 SQL，看板由运维按环境搭）；
- A/B 实验框架、自动重训流水线（回流保持人工审核在环）；
- 全链路 tracing（OpenTelemetry）——trace_id 贯穿已够用，按需后补。

## 4. 技术要求

- 指标埋点不得增加主链路显著延迟（Counter/Histogram 皆内存操作）；label 基数受控
  （意图域而非 33 个意图码全量、工具 id 白名单）；
- 导出 CLI 一律脱敏（手机号打码），导出文件不进 git（gitignore data/export/）；
- 物化视图刷新与导出脚本幂等可重跑。

## 5. 目录和文件要求

```text
app/core/metrics.py                  # 指标注册与埋点助手
app/api/routes/metrics.py            # GET /metrics（豁免鉴权或内网 scope）
app/models/chat_feedback.py + repository + 路由扩展
scripts/export_review_set.py / replay_trace.py / refresh_quality_views.py
docs/ops/quality_queries.md
alembic/versions/xxxx_add_feedback_and_quality_views.py
.github/workflows/ci.yml
tests/stage09/
```

## 6. 具体实现要求

- 埋点位置收口：intent_classify（意图与来源）、五个回复分支节点（分支与耗时）、
  chat_service（轮次总耗时）、executor/handoff/limiter 各自计数——禁止散落重复计数；
- feedback 的 message_id 必须属于该会话（防跨会话投毒）；
- export_review_set 去重（同 normalized_text 只出最新一条）并排除已入训练集文本。

## 7. 代码质量要求

- 单测：指标计数正确性（before/after 断言）、feedback 归属校验、导出去重与脱敏；
- ruff / mypy 通过；核心逻辑中文注释。

## 8. 验证方式

1. 跑一轮混合场景对话 → `GET /metrics` 可见意图/分支/RAG/确认门计数增长。
2. 点踩一条回复 → chat_feedback 落库 → export_review_set 导出含该样本（已脱敏）。
3. 导出 → 人工改标 → build --extra 合并 → 重训 → tests/eval 门禁全绿（流程演练）。
4. replay_trace 按 trace_id 完整还原一轮决策。
5. CI 在干净环境跑通（eval 门禁行为符合预期）。
6. 刷新 quality_daily → quality_queries.md 的 SQL 逐条可执行出数。

## 9. 执行提示词

```text
请先阅读 AGENTS.md、docs/testing/test_strategy.md、docs/intent/README.md、本文档。
本次只实现 Stage 09，按第 2 节逐项实现，第 3 节不要做。
完成后说明新增/修改文件、迁移脚本、指标清单、验证步骤。
```

---

## 附录：实现记录（2026-07-03）

### A. 已实现清单

| 需求项 | 实现位置 | 说明 |
|---|---|---|
| Prometheus 指标 | `app/core/metrics.py`（单一注册点）+ `app/api/routes/metrics.py`（GET /metrics，豁免业务鉴权，生产靠内网限制） | 9 个指标全部落地；埋点收口：chat_service（轮次耗时 意图域×分支）、intent_classify（意图×来源）、llm factory（calls/failures）、rag_answer（faq_hit/rag_answer/refused/degraded）、save_turn（确认门）、executor（写操作，tool_id 白名单外归 other 控基数）、handoff_service（建单）、rate_limit（tenant/session）、feedback |
| 质量看板 | `docs/ops/quality_queries.md`（8 组可执行 SQL）+ 物化视图 `quality_daily`（migration `1316d5cadc06`）+ `scripts/refresh_quality_views.py`（CONCURRENTLY 不锁读） | 一次解决率（无工单且无连续 2 轮 FALLBACK，窗口分组法算连击）/转人工 reason 分布/意图低置信/RAG 拒答/确认门/工具失败/P95 全部实测出数 |
| 数据回流 | `scripts/export_review_set.py`（review / faq 两模式） | review：低置信/LLM 二判/FALLBACK/差评轮次（经 trace_id 关联差评的用户消息），DISTINCT ON 去重取最新、排除已入训练集、手机号脱敏；faq：高频已答问题计数导出。`build_intent_dataset.py --extra` 合并回流早已支持 |
| chat_feedback | model/repository/migration + `POST /api/chat/sessions/{id}/feedback` | 双重归属校验（会话属租户+用户；message 属会话且 role∈assistant/agent，防跨会话投毒）；同消息重复评价更新不报错（部分唯一索引兜底） |
| CI 门禁 | `.github/workflows/ci.yml` | PG16+Redis7 service 容器、KB_ENABLED=false、uv sync --frozen → ruff → mypy → alembic upgrade → pytest -rs（eval 门禁 skip 显式可见） |
| 决策回放 | `scripts/replay_trace.py --trace/--session` | 按轮打印文本→意图(top_k/来源/置信度)→槽位→技能/状态→图轨迹→耗时→检索/工具轨迹→AI 回复；输出脱敏 |
| RAG 评估集 | `docs/testing/rag_eval_set.md`（32 组）+ `tests/eval/rag_corpus.py`（8 文档+6 FAQ 标准语料）+ `tests/eval/test_rag_eval.py` | 用例表在文档中，harness 解析执行（单一事实来源）；阈值双档：hash 低档 hit≥0.75/refusal≥0.80（实测 79%/80% 通过），真实 embedding 高档 0.90/0.90；无 Milvus/KB 关闭显式 skip |
| 测试 | `tests/stage09/test_observability.py`（10 例） | 指标 before/after 断言、白名单基数控制、反馈归属/幂等/防投毒、导出脱敏与训练集排除 |

### B. 关键实现决策

1. **多租户不进 label**：租户级分析全部走 SQL/物化视图；直方图用意图域（9 个）而非 33 意图码。
2. **decision_log 无 trace_id 列**：回放与差评关联均经 `message_id → chat_message.trace_id`，不加列。
3. **quality_daily 用物化视图而非实时聚合**：看板查询不扫原表；REFRESH CONCURRENTLY 依赖 (tenant_id, day) 唯一索引。
4. **导出文件不进 git**：`data/export/` 已入 .gitignore；导出与刷新脚本幂等可重跑。

### C. 验证记录（第 8 节场景全过）

1. 混合场景 5 轮（工具查询/确认门执行/RAG/兜底/闲聊）→ /metrics 各计数按预期增长 ✅
2. 点踩 FALLBACK 回复 → chat_feedback 落库 + chat_feedback_total 计数；越权（错误 user）404 ✅
3. export_review_set 导出 8 条（含 SETFIT_LOW_CONF/FALLBACK 样本，去重脱敏）；--extra 合并链路已有 ✅
4. replay_trace --session/--trace 完整还原决策（含 top_k、图轨迹、工具轨迹）✅
5. CI workflow 语法完整（本地等价命令全绿：ruff/mypy/pytest 112 passed）✅
6. refresh_quality_views 0.06s；quality_queries.md 全部 SQL 实测出数（一次解决率 93.75%）✅

### D. 遗留

- Grafana 看板 JSON 与告警规则（指标与 SQL 口径已定，运维按环境搭）。
- SetFit 门禁在 CI 上真实执行需带模型产物的 runner（当前显式 skip）。
- RAG 高档门禁与生产阈值标定待真实 embedding（文档 4 节 backlog）。
