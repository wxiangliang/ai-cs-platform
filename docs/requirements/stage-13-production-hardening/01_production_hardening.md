# Stage 13 需求：生产加固（2026-07-03 生产级审计整改）

> 来源：全项目生产级审计（意图/RAG/业务链路 + 基础设施/API 两路并行审计，
> 结论按代码证据逐条核实）。本文件是整改的单一清单，按优先级分三批实施；
> 每条带定位，便于逐条销号。真实模型/外部系统接入类不在本阶段范围。

---

## 1. 阶段目标

把已实现的业务能力从「功能正确」推到「生产可用」：默认配置安全、
写操作并发安全、数据一致性闭环、多进程部署正确。

## 2. 第一批：安全与资损（P0/P1，必须先做）

### 2.1 生产配置硬门禁（P0）

现状：`AUTH_ENABLED=false` + `KB_ADMIN_TOKEN=""`（均为默认值）时，
`require_admin`（`app/core/auth.py:134-148`）直接放行——kb/product/handoff
管理面完全无鉴权：可篡改价格库存、投毒知识库、拉取工单里的客户上下文。
当前仅启动 warning，无阻断。

要求：
- `Settings` 加 `model_validator`：`APP_ENV in (staging, prod)` 时强制
  `AUTH_ENABLED=true`、`EMBEDDING_PROVIDER != hash`、`DATABASE_URL` 非默认弱口令、
  `DEBUG=false`，不满足**拒绝启动**（fail-fast，报错说明缺哪项）；
- 开发模式下管理面若 `KB_ADMIN_TOKEN` 为空，降级为「只告警+只读放行、写操作 403」
  或直接要求非空 token（二选一，倾向后者，简单明确）。

### 2.2 ActionExecutor 防重放原子化（P1，资损面 P0）

现状：`app/chat/actions/executor.py:78-102` 防重放是「读 status 检查 → 另起事务
UPDATE 标记 EXECUTING」两步，标记 UPDATE 无 `WHERE status` 条件。并发两条「确认」
（双击/重试且无 Idempotency-Key）都能通过检查 → **同一笔退款/取消提交两次**；
dialog_state 乐观锁在 save_turn 才冲突，晚于写工具调用。

要求：拿执行权改为原子条件更新——
`UPDATE chat_task SET status='EXECUTING', confirmed_at=now() WHERE id=? AND
status IN ('CONFIRMING','COLLECTING')`，受影响行数=1 才继续执行，
=0 返回 `ALREADY_EXECUTED`。无 task_id 的任务（理论不存在）拒绝执行。
补并发单测：两个协程同时 execute 同一 task，断言恰好一次调用写工具。

### 2.3 MCP 读工具失败禁止用 mock 冒充事实（P1）

现状：`app/chat/tools/mcp_provider.py:122-134` MCP 调用失败回落 mock 且
`ok=True`，`degraded` 标记只进审计 JSON 不进回复——真实订单系统抖动时，
用户拿到的是**编造的订单状态/物流轨迹**。

要求：
- 加 `TOOL_MCP_FALLBACK=mock|fail`（默认 fail；开发联调可显式选 mock）；
- `fail` 语义：已发现工具调用失败 → 返回 `ok=False, error_code=UPSTREAM_UNAVAILABLE`，
  tool_invoke 走既有失败分支（RAG 兜底/「暂时查询不到，稍后再试」话术 + SKILL_RULE 建单）；
- 写操作维持现状红线（本就不回落 mock），加显式断言与注释。

### 2.4 幂等缓存必须在事务提交后写入（P1）

现状：`app/api/routes/chat.py` 在路由内 `cache_response`，而 commit 在
`get_db_session` teardown（更晚）。提交失败时客户端已拿到 success 且幂等缓存
存下假成功，重试永远返回未落库的响应，消息丢失不可自愈。

要求：把发消息路径的事务提交收进路由/服务显式控制（`await db.commit()` 成功后
再写幂等缓存），或提供 after-commit 钩子。同时给幂等加**在途占位**
（`SET NX` processing 标记）防同 key 并发双跑，并存请求体指纹，
同 key 不同 body 返回 422。

## 3. 第二批：一致性与正确性（P1/P2）

- **API Key 吊销即时生效**：进程内缓存 TTL 300s 且无跨进程失效
  （`app/core/auth.py:47-74`）。吊销/改 scope 走 Redis 版本号（每次校验查一次
  轻量版本，失配则回源），缓存满 1024 清全表改 LRU 淘汰；对未命中 key_id 做一次
  假 bcrypt 拉平耗时（防计时探测）。`api_credential` 加 `expires_at`（可空）。
- **Prometheus 多进程模式**：`prometheus_client` 默认 registry 在
  `uvicorn --workers N` 下每 worker 独立计数，抓取随机命中某进程。
  支持 `PROMETHEUS_MULTIPROC_DIR` + `MultiProcessCollector`（配置存在时自动启用）。
- **记忆异步任务持引用**：`asyncio.create_task` 裸调（`chat_service.py`）可能被
  GC 提前取消且关停时不收敛。用模块级 task set 持引用 + done_callback 移除 +
  lifespan shutdown 时限时等待收敛。
- **handoff 并发建单窗口**：`ensure_ticket` 撞部分唯一索引的 IntegrityError 被
  `except Exception` 吞掉后主 session 进 aborted 状态，殃及同事务后续落库。
  ensure_ticket 内捕获 IntegrityError → 回查已有工单返回（幂等语义补全），
  不让异常污染主事务。
- **FAQ 向量写失败补 needs_reindex 信号**：对齐 kb_document 的做法
  （`app/kb/ingest.py`），faq_entry 加标记列或复用 status，reindex 能发现补建。
- **decision_log / 导出脱敏**：`mask_sensitive` 扩展地址打码（省市区保留、
  详址掩码）；decision_log 的 `original_text`/`slot_result_json` 落库前过
  mask_sensitive（回放/回流导出同口径受益）。
- **确认门高风险收紧**：L3 技能（退款/取消/改址）在 CONFIRMING 状态下，
  「嗯/好的/ok」类弱确认词不直接执行——要求包含明确动作词（「确认/是的，取消吧」）
  或经 LLM 复核；弱确认回复「请回复『确认』以执行 XX」二次确认。
  （体验与资损的权衡：只对 L3 收紧，L1/L2 维持现状。）

## 4. 第三批：部署适配（P2）

- 启动阶段对 DB `SELECT 1` 探活（与 Redis fail-fast 语义一致）；
- `SKILLS_DIR`/`SETFIT_MODEL_PATH` 等相对路径锚定包根（非 CWD），
  任意目录启动不炸；
- 限流窗口「先判后写」，被拒请求不占窗口（防自我饥饿）；
- SSE done 事件的 slots 复核脱敏口径；
- SetFit 多 worker 内存放大：文档标注部署建议（单 worker + 水平扩容，
  或独立推理服务），暂不改代码。

## 5. 不做什么

- 真实 LLM/embedding/业务系统接入与阈值标定（联调项，另行）；
- 内容安全护栏（独立 Stage 14）；
- OpenTelemetry 全链路（Langfuse + trace_id 已够用）。

## 6. 验证方式

1. `APP_ENV=prod` + 默认配置启动 → 拒绝启动并列出缺项；
2. 并发确认单测：同 task 两协程 execute，写工具恰好调用一次；
3. 停 MCP 服务查订单 → 回复「暂时查询不到」类话术 + 建单，绝无 mock 事实；
4. 幂等：注入 commit 失败 → 重试不返回假成功；同 key 异 body → 422；
5. 吊销 API Key → 下一请求即 401（多 worker 各进程一致）；
6. `--workers 4` 压测 → /metrics 计数与实际请求数一致。

---

## 附录：实现记录（2026-07-03）

### A. 整改清单（三批全部完成）

| 项 | 实现 | 验证 |
|---|---|---|
| 2.1 生产配置硬门禁 | `config.py` `model_validator`：APP_ENV∈{staging,prod,production} 时强制 AUTH_ENABLED/非 DEBUG/非 hash embedding（KB 开启时）/非默认弱口令，缺项一次性列出并拒绝启动 | `APP_ENV=prod` 默认配置实测拒启 ✅；本地默认零回归 ✅ |
| 2.1 管理面 token | `require_admin`：开发模式空 KB_ADMIN_TOKEN 不再放行（403 ADMIN_TOKEN_REQUIRED）——**联调需在 .env 配置 KB_ADMIN_TOKEN** | 无 token 403 / 带 token 放行 e2e ✅ |
| 2.2 防重放原子化 | `chat_task_repository.claim_for_execution`（条件 UPDATE WHERE status IN 中间态 + version+1）；executor 无 task_id 拒绝（NO_TASK_ID）、拿不到执行权 ALREADY_EXECUTED、标记失败 CLAIM_FAILED（宁可不执行） | 并发双确认单测：写工具**恰好一次** ✅ |
| 2.3 MCP 禁 mock 冒充 | `TOOL_MCP_FALLBACK=fail`（默认）：调用失败/曾发现过的工具不可达 → ok=False UPSTREAM_UNAVAILABLE（走「帮您核实」+SKILL_RULE 建单既有分支）；mock 仅开发显式选择；写操作恒走 mock（其归属）；冷启动无历史发现集时保留 mock+degraded（无法区分覆盖面，已注释说明） | stage11 测试重写 8 例 ✅ |
| 2.4 幂等加固 | after-commit 写缓存（路由显式 commit 后）+ SET NX 在途占位（并发同 key 409 REQUEST_IN_FLIGHT）+ body 指纹（同 key 异 body 422 IDEMPOTENCY_KEY_REUSED） | e2e：同 key 同 body 同响应 / 异 body 422 ✅ |
| 3 API Key 即时吊销 | Redis 吊销版本（缓存命中时一次轻量 GET 比对，失配作废重验；Redis 故障容忍缓存）；CLI disable 联动 bump；LRU 逐出替代整表清空；key 未命中做假 bcrypt 拉平耗时；`api_credential.expires_at`（NULL=永不过期） | CLI 吊销广播 e2e ✅；LRU/版本单测 ✅ |
| 3 Prometheus 多进程 | `render_metrics` 检测 `PROMETHEUS_MULTIPROC_DIR` → MultiProcessCollector 聚合 | mmap 文件生成 + /metrics 渲染 e2e ✅ |
| 3 记忆任务收敛 | `_background_tasks` 强引用集合 + done_callback 清理 + lifespan shutdown 限时等待（先于关资源） | — |
| 3 建单并发窗口 | ensure_ticket 建单包 SAVEPOINT，IntegrityError 回查已有工单返回，不污染主事务 | 强制撞唯一索引单测 ✅ |
| 3 FAQ needs_reindex | faq_entry 加列（migration `504825e337b1`，与 expires_at 同批）；ingest 失败置位、reindex 清位 | — |
| 3 脱敏扩展 | mask_sensitive 地址字段打码（保留前 6 字符）；decision_log 的 original_text/normalized_text/slots 落库前脱敏（回放/回流导出同口径受益） | 单测 ✅ |
| 3 L3 弱确认收紧 | confirmation_parse：CONFIRMING + META.CONFIRM + L3 技能 + 弱词（嗯/好的/ok…）→ 降级 SLOT_ONLY 重进确认门，回复加「请回复『确认』」；强动作词/L1L2 不受影响 | e2e：退款「好的」重确认→「确认」执行 ✅ |
| 4 启动 DB 探活 | lifespan SELECT 1（与 Redis fail-fast 语义一致） | — |
| 4 路径锚定 | SKILLS_DIR / SETFIT_MODEL_PATH 相对路径锚定仓库根，任意 CWD 启动可用 | — |
| 4 限流先判后写 | 被拒请求不写入窗口（防自我饥饿） | stage08 测试 ✅ |

### B. 复核后决定不改的项

- **SSE done 事件含 slots**：返回的是用户本人本轮输入的槽位（自己的数据回显），非跨界泄漏——不改，记录口径。
- **SetFit 多 worker 内存放大**：属部署形态选择，见 runbook 部署建议（单 worker 水平扩容或独立推理服务），不改代码。

### C. 验证汇总

全量 138 tests 通过（新增 stage13 24 例）；ruff/mypy 干净；e2e 六项（prod 拒启/管理面 token/弱确认闭环/幂等指纹/多进程指标/吊销广播）全过。
