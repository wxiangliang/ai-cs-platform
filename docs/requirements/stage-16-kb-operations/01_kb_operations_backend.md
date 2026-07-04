# Stage 16 需求：知识库运营后台（后端）

> 前置：无（Stage 06 知识库表与 API 已全）。本阶段是纯后端逻辑，不依赖真实模型/外部系统，
> 可做到生产级并有测试。运营界面（前端）不在本阶段——只交付 API 与流程。
> 来源：roadmap 3.6 backlog「知识库运营后台」。

---

## 1. 阶段目标

把知识库从「能写能查」升级为「可运营治理」：文档有版本与审核发布流程、
能定时生效/失效、有命中率反馈闭环——让运营敢改、能追溯、知道哪些内容有用。

## 2. 本阶段要做什么

### 2.1 文档版本与草稿-发布状态机

现状：`kb_document.status` 只有 `active/disabled`，改文档即整篇重建分块、立即对用户生效——
运营改错无回退、无审核。

- 状态机：`draft`（草稿，不参与检索）→ `pending_review`（待审）→ `published`（生效）
  → `archived`（下线，保留可回滚）；`published` 才进检索（retriever 的 status 过滤扩展）；
- 版本：文档编辑生成新版本（`kb_document_version` 表：doc_id/version/raw_content/editor/created_at），
  `published` 指向某个版本；回滚 = 把历史版本重新 publish；
- 分块与向量只在 **publish 时**重建（草稿编辑不动索引，省算力也避免半成品被检索）；
  publish 复用现有 `upsert_document` 的分块+embedding+建索引管线。

### 2.2 审核流

- 提交审核（draft→pending_review）、通过（→published，触发重建索引）、驳回（→draft + 意见）；
- 审核动作落审计（谁、何时、什么动作、意见）；
- 权限：编辑与审核分离（admin scope 内细分 `kb_editor`/`kb_reviewer`，或复用 admin + 动作记录人）。
  v1 可先只记录操作人，不强制双人；设计上预留角色位。

### 2.3 定时生效/失效

- `effective_from` / `expire_at`（可空）：到点自动 publish / archive；
- 定时任务 `scripts/kb_schedule.py`（幂等，cron，复用 `close_idle_sessions` 模式）：
  扫到期文档切状态并重建/移除索引；
- 到期失效的文档立即退出检索（status 过滤 + reindex 移除，与 needs_reindex 一致的兜底）。

### 2.4 命中率反馈闭环

- 文档/FAQ 命中统计：`faq_entry.hit_count` 已有；文档层给 `kb_document` 加 `hit_count`
  或从 `decision_log.retrieval_json.chunk_hits` 按 doc 聚合（后者不加列、可追溯，优先）；
- 运营视图 SQL（`docs/ops/kb_quality_queries.md`）：热门/零命中文档、拒答率高的查询词
  （从 decision_log `refused=true` 聚合，指向知识盲区）、FAQ 命中 TOP/长尾；
- 零命中/长期未命中文档 → 运营复审入口（导出 CSV，同 export_review_set 风格）；
- 与 Stage 09 已有的 FAQ 沉淀候选（高频已答问题）打通，形成「盲区发现→补充→审核→发布」闭环。

## 3. 本阶段不做什么

- 运营前端界面（API 就绪，界面另做）；
- 富文本/所见即所得编辑器（仍是 markdown/纯文本入库）；
- 文档间引用关系图谱、自动摘要生成。

## 4. 目录和文件要求

```text
app/models/kb_document_version.py + repository + migration
app/models/kb_document.py                # 加 effective_from/expire_at；status 扩状态机
app/services/kb_operations_service.py    # 版本/审核/发布/回滚/定时切换编排
app/api/routes/kb.py                     # 扩：提交审核/审核/发布/回滚/版本列表（admin scope）
app/kb/retriever.py                      # status 过滤扩展为「仅 published」
scripts/kb_schedule.py                   # 定时生效失效 cron
docs/ops/kb_quality_queries.md           # 命中率/盲区 SQL
tests/stage16/
```

## 5. 验证方式

1. 建文档→草稿不被检索→提交审核→通过发布→检索命中；驳回→回到草稿。
2. 改已发布文档→生成新版本→草稿态编辑不影响线上→发布后切换→回滚到旧版本生效。
3. 设 `expire_at` 为过去→跑 kb_schedule→文档 archived 且退出检索；`effective_from` 未来→到点自动上线。
4. 命中率 SQL：热门文档/零命中文档/高拒答查询词出数；盲区导出可用。
5. 全链路 ruff/mypy/pytest 绿；publish 复用现有 ingest 管线，检索行为对已发布文档零回归。

---

## 附录：实现记录（2026-07-05）

### A. 已实现清单

| 项 | 实现 | 说明 |
|---|---|---|
| 数据层 | `kb_document` 加 `effective_from`/`expire_at`/`published_version` 三列；`kb_document_version` 表 + repository；migration `3a1e7630141b` | 数据迁移：存量 active→published + 回填 published_version=1 与 v1 版本行，零回归 |
| 状态机 | draft → pending_review → published → archived（`kb_operations_service`） | **生效判据 = published_version 非空且未 archived**（不单看 status）——编辑已发布文档时 status 回 draft 但 published_version 不变，**线上仍服务旧版本** |
| 版本与回滚 | 每次 create/edit/rollback 追加 `kb_document_version` 快照；rollback 把历史版本内容记为新版本并发布 | 版本号同文档自增；回滚即重新发布 |
| 审核流 | submit/approve/reject，动作落 `metadata_json.review_log`（谁/何时/动作/意见，保留 30 条） | v1 只记录操作人，不强制双人；publish 复用 `upsert_document` 管线（传入 metadata 防清空审计） |
| 发布重建索引 | approve/rollback 复用 `kb_ingest_service.upsert_document`（整篇重建分块+embedding+建索引），切换 published_version | retriever/list_active 生效判据改 published_version-based |
| 定时生效/失效 | `scripts/kb_schedule.py`（cron）：effective_from 到点自动发布、expire_at 到点 archive，清标记幂等 | 单文档失败不中断整批 |
| 命中率闭环 | `docs/ops/kb_quality_queries.md`：热门/零命中文档、高拒答查询词（盲区）、FAQ TOP/长尾、审计日志——从 decision_log.retrieval_json + faq hit_count 聚合，不加列 | 与 Stage 09 FAQ 沉淀候选打通盲区闭环 |
| API | `routes/kb.py` 扩 8 个端点（admin scope）：draft/edit(PATCH)/submit/approve/reject/archive/rollback/versions | — |

### B. 验证记录

- **零回归**：全量 **219 passed**（基准 213 + 新增 stage16 6 例），既有 kb/检索测试全绿——status active→published 迁移 + 生效判据改动对已发布文档零影响。ruff/mypy 干净。
- **e2e（真实 Milvus）**：草稿(不生效)→提交→通过发布(pv=1，chunk 真建)→编辑(status 回 draft，线上不变)→再发布(pv=2)→回滚 v1(pv=3)→下线(archived)，HTTP 全流程通过。
- **修复**：publish 复用 upsert_document 时漏传 metadata 会清空 review_log 与文档元数据 → 已传入 doc.metadata_json 保留。

### C. 遗留

- 运营前端界面（API/流程已就绪）；编辑/审核角色细分（v1 只记操作人，预留 kb_editor/kb_reviewer scope）；富文本编辑器；文档引用图谱。
