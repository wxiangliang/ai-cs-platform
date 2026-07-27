# Post-Stage 23 后端下一步规划（真实化 · 生产化 · 补全）

> 日期：2026-07-27
> 性质：**规划文档与需求池**，不是可直接执行的阶段需求文档。开发时必须按
> `docs/00_docs_management_standard.md` 拆成新 Stage 文档，并先更新
> `roadmap.md` / `CLAUDE.md` 阶段状态。
> 范围：**只覆盖后端服务**。前端（坐席工作台/KB 运营后台/用户端 UI/实验配置面板）
> 不在本文范围，另行规划。

---

## 1. 结论与定位

Stage 01-23 后，聊天后端的**功能建设阶段结束**：主链路、意图三层分类、确认门、
RAG 管道 v2、人工接管、鉴权、可观测、护栏、记忆、成本控制、A/B、i18n、
智能澄清、只读诊断 agent、方向纠偏均已落地，332 tests / ruff / mypy 全绿。

**当前系统的真实瓶颈不是缺功能，而是整个系统仍运行在"仿真态"**：
LLM 全链路降级、工具是 mock 数据、embedding 是 hash 伪向量、
分类器训练语料是构造的。下一步的主线只有一条：**真实化 → 生产化 → 数据飞轮**。
不再往聊天链路添加新能力（已有能力先验证再说）。

---

## 2. P0 批次 A：真实化联调（打开系统上限，最优先）

### A1. 真实 LLM 联调（建议 Stage 24）

```text
内容：
- 配置真实 OPENAI_API_KEY / OPENAI_BASE_URL / CHAT_MODEL(_FAST/_SMART)；
- 逐路径验证（每条都有独立开关，可灰度）：润色 → 意图二判 → 槽位兜底 →
  确认门解析 → RAG 生成 → 记忆摘要/事实抽取 → 查询改写 → 智能澄清(21) →
  只读诊断(22，开 DIAGNOSE_AGENT_ENABLED)；
- 跑通评估门禁：tests/eval 意图集 + RAG 32 组（真实 embedding 高档阈值 0.80）；
- 阈值标定：TURN_LLM_BUDGET_SECONDS 按真实时延回调、CLARIFY/软确认阈值观察；
- 成本基线：token/轮 × 意图分布，验证分级路由与预算熔断实际效果。
验收：全部 LLM 路径在真实模型下有人工抽检记录；eval 门禁绿；
     Langfuse 上能看到完整调用链；成本/轮有数。
依赖：无（第一件事）。
```

### A2. 真实 embedding + Milvus 生产化（建议并入 Stage 24 或独立 24-02）

```text
内容：EMBEDDING_PROVIDER=openai + 全量 reindex；FAQ_HIT_THRESHOLD /
  RAG_MIN_SCORE / SEMANTIC_CACHE_THRESHOLD 用标定集重标（hash 伪向量下的
  0.6/0.2 必然失效）；Milvus 部署形态（standalone→HA）与备份策略；
  RAG 高档门禁（0.80）转为 CI 必过。
验收：RAG 评估集高档通过；语义缓存命中率/误命中率有数。
依赖：A1（生成路径要一起验）。
```

### A3. 真实业务系统对接（建议 Stage 25）

```text
内容：MCP 服务端替换 mock——订单/物流/售后真实系统接入
  （只改 scripts/run_mcp_server.py 工具函数内部，Stage 11 已验证抽象层）；
  写工具补业务幂等键、外部状态回查、失败补偿策略；
  TOOL_MCP_FALLBACK=fail 生产验证（mock 不冒充事实）；
  Stage 22 READONLY_TOOLS 白名单与真实工具目录同步。
验收：mock 与真实系统对拍通过；写操作幂等演练（重放/并发确认）通过。
依赖：业务方系统就绪；可与 A1 并行。
```

### A4. 意图数据飞轮启动（建议 Stage 26，持续运行）

```text
内容：灰度真实流量 → export_review_set 回流（低置信/二判/FALLBACK/差评/
  CSAT 低分）→ 人工审核 → build_intent_dataset --extra 合并重训 →
  eval 门禁 → 发布。建立真实语料准确率基线（构造语料 0.94 无参考意义）；
  SetFit CI 门禁上带模型 runner。
  同步观察 Stage 21/23 的新口径：澄清成功率（澄清轮次后下一轮脱离 UNKNOWN
  比例）、任务否定率、中置信采纳占比——决定阈值与词表调整。
验收：完成第一轮"回流→重训→上线"闭环；真实准确率基线入档。
依赖：A1 + 灰度流量。
```

---

## 3. P0 批次 B：生产部署与运维（与批次 A 并行）

### B1. 部署编排与定时任务（建议 Stage 27）

```text
内容：生产部署清单（docker compose 生产 profile 或 K8s manifests：
  api 多副本 + PROMETHEUS_MULTIPROC_DIR、PG、Redis、Milvus、MCP 服务）；
  三个 cron（close_idle_sessions / kb_schedule / refresh_quality_views）
  进正式调度器（K8s CronJob 等）+ 失败重试 + 告警 + 运行日志；
  启动探活/就绪探针口径复核（Milvus health 已修复为复用连接）。
验收：一键部署文档可复现；cron 断跑有告警。
```

### B2. 监控告警落地

```text
内容：Grafana 看板（轮次 P95、意图分布、fallback 率、rag 结局、
  llm 调用/预算熔断、kb_stage 分段、direction_correction、diagnose）；
  告警规则（错误率、P95、连接池耗尽、预算熔断激增、护栏拦截激增）；
  修复 post-stage-19 指出的指标基数问题：llm_tokens_total{tenant} /
  llm_budget_exceeded_total{tenant} 高基数 label → 聚合指标 + 租户明细走 SQL。
验收：看板上线；告警演练触发一次。
```

### B3. 压测与容量标定

```text
内容：事务拆分（post-stage-20）后的真实 QPS 基线压测；
  DB_POOL_SIZE / 限流参数 / MEMORY_TASK_CONCURRENCY 按压测结果标定；
  长会话/高并发同会话（409 路径）演练。
验收：容量基线入档；限流参数有依据。
```

---

## 4. P1 批次 C：后端能力补全（真实化跑顺后）

| 项 | 说明 | 前置 |
|---|---|---|
| C1 真流式 SSE | 生成端 token 流接入 delta 事件（协议已预留，meta/delta/done 不变）；体感延迟收益最大的单项 | A1 |
| C2 多渠道适配层（后端侧） | Channel Adapter：微信/企微 webhook 接入、统一消息规范、签名验签、重试去重、附件处理——纯后端，不含渠道前端 | A1-A3 |
| C3 安全合规 | 数据保留策略、用户数据删除/导出 API、审计查询权限模型（角色细分含 kb_editor/kb_reviewer 预留位） | 无 |
| C4 坐席链路补口 | CLOSED 状态 API（Stage 07 遗留）、WS 心跳/连接数网关化 | B1 |
| C5 多语言输入侧 | 意图分类多语言训练集、护栏词表、槽位正则、自动语言检测、skill.name 多语言（Stage 19 输出侧已完成） | A4 |
| C6 LLM 理解调用合并 | 二判+槽位+确认门解析 → 单次结构化输出（延迟/成本减半）；必须有真实 LLM eval 门禁护航防质量回退 | A1+A4 |

---

## 5. P2：延后 / 按需

```text
- 语义缓存迁 Milvus（numpy 化后 Redis 方案痛点已消除，收益不紧迫）；
- 关键词路 pg_trgm GIN 索引（需超管权限，部署侧决策；ILIKE 全表扫仍是关键词路慢点）；
- quality_daily 物化视图扩列（错向/澄清口径，等真实流量一并做）；
- 语义缓存闲聊节点接入、月度预算窗口（Stage 17 遗留）；
- GUARDRAIL_PROVIDER=external 扩展位、护栏词表运营扩充（Stage 14 遗留）；
- A/B 意图域作用域、实验配置迁 DB（Stage 18 遗留）；
- 离线 deep agent（回流样本聚类/FAQ 候选生成/质量周报，三层演进第三层）；
- mem0 深化 / 多模态（roadmap 3.6 backlog，按业务需求触发）。
```

---

## 6. 建议执行顺序（里程碑）

```text
M1（真实化）：A1 → A2 —— 系统上限打开，所有已建能力得到验证
M2（对接）  ：A3 + B1/B2 并行 —— 可对外服务的最小生产形态
M3（飞轮）  ：A4 灰度 + B3 压测 —— 质量与容量都有真实基线
M4（补全）  ：C1 真流式 → C2 渠道 → C3 合规 →（数据充分后）C6 调用合并
```

每个里程碑完成后回看 P2 清单，按数据决定是否提级。
**原则不变：每项开工前按规范写 Stage 文档；写操作红线、可降级、可审计
三条纪律适用于以上全部项。**
