# 监控与告警（Stage 25，后端侧）

> 配置目录 `deploy/monitoring/`；启用方式：
> `.env` 配置 `GRAFANA_ADMIN_PASSWORD` 后
> `docker compose -f docker-compose.prod.yml --profile monitoring up -d`。
> Grafana: `http://<host>:3000`（admin / 你配置的密码），
> 看板「AI 客服后端 · 服务与链路健康」自动装载。

## 1. 看板面板一览（数据源全部为 /metrics 既有指标）

| 面板 | 指标 | 看什么 |
|---|---|---|
| 轮次延迟 P50/P95 | chat_turn_duration_seconds | 服务健康的第一信号 |
| 轮次速率（分支） | 同上 count by branch | 流量与回复分支构成 |
| 意图决策来源 | intent_decisions_total | SETFIT/LLM/规则/降级占比漂移 |
| RAG 结局 | rag_retrievals_total | faq_hit/rag_answer/refused/degraded |
| LLM 调用/失败 | llm_calls_total / llm_failures_total | 供应商健康 |
| token 与预算熔断 | llm_tokens_total / llm_budget_exceeded_total | 成本与熔断趋势（聚合） |
| KB 分段耗时 P95 | kb_stage_duration_seconds | embed/vector/keyword/rerank/llm 热点 |
| 护栏/纠偏 | guardrail_blocks_total / direction_correction_total | 攻击与错向信号 |
| 转人工/确认门/诊断 | handoff_tickets_total / confirm_gate_total / diagnose_agent_total | 质量综合 |
| 缓存/限流/反馈 | semantic_cache_total / rate_limited_total / chat_feedback_total | 辅助运营 |

## 2. 告警规则（deploy/monitoring/alerts.yml）

| 规则 | 条件（默认阈值） | 级别 | 说明 |
|---|---|---|---|
| ApiDown | up==0 持续 1m | critical | 实例失联 |
| HighTurnLatencyP95 | P95>8s 持续 10m | warning | **待 B3 压测标定** |
| LlmFailureSpike | 失败率>30% 持续 5m | critical | 链路已降级，排查供应商/Key |
| LlmBudgetExceededSpike | 熔断速率>0.1/s 持续 10m | warning | 租户明细见第 4 节 |
| GuardrailBlockSpike | 拦截>0.5/s 持续 10m | warning | 注入攻击或词表误拦，按 rule 下钻 |
| RagRefusalRateHigh | 拒答+降级>50% 持续 15m | warning | KB 覆盖或 Milvus/embedding 异常 |
| RateLimitSpike | >1/s 持续 5m | warning | 流量激增或配额过紧 |
| HandoffSpike | 建单>0.5/s 持续 15m | warning | bot 质量劣化综合信号，按 reason 下钻 |

通知渠道：默认只在 Prometheus/Grafana 内可见；对接 webhook 用
`alertmanager.yml.example`（复制填地址 → compose 加 alertmanager 服务 →
取消 prometheus.yml 中 alerting 段注释）。

## 3. 告警演练（上线前执行一次）

```bash
# 1. ApiDown：停掉 api，1 分钟后 Prometheus /alerts 页应出现 firing
docker compose -f docker-compose.prod.yml stop api && sleep 90
curl -s http://localhost:9090/api/v1/alerts | python3 -m json.tool | grep -A2 ApiDown
docker compose -f docker-compose.prod.yml start api
# 2. cron 心跳：kill 调度器进程但保容器（或改 SCHEDULER_HEARTBEAT_FILE 指向只读路径），
#    观察 docker ps 中 cron 变 unhealthy 并被 restart 拉起
# 3. cron 任务失败告警：临时把 ALERT_WEBHOOK_URL 指向测试接收端，
#    CRON_QUALITY_VIEWS_INTERVAL=5 且停掉 PG → 观察 webhook 收到 JSON
```

## 4. 租户级明细（基数整改后的查询口径）

Prometheus 指标**不带 tenant label**（Stage 25 整改，遵守「多租户不进 label」）。
租户明细走两条既有通道：

```bash
# 当日各租户 token 用量（budget 模块 Redis 日计数）
redis-cli --scan --pattern 'llm_budget:*' | while read k; do echo "$k $(redis-cli get $k)"; done
```

```sql
-- 按租户切分的轮次/兜底/延迟：quality_daily 与 decision_log（Stage 09 口径）
SELECT tenant_id, day, turns, fallback_turns, p95_latency_ms
FROM quality_daily WHERE day >= current_date - 7 ORDER BY tenant_id, day;
```

## 5. 保留期与容量

Prometheus 默认本地 TSDB 保留 15 天（`--storage.tsdb.retention.time` 按需加参）；
指标全部为低基数（label 值 ≤ 意图域/purpose/outcome 枚举），单实例足够。
长期趋势走 quality_daily（PG 物化视图，不受 TSDB 保留期限制）。
