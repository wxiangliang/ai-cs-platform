# 质量看板查询（Stage 09）

> 数据源：`chat_decision_log` / `chat_session` / `chat_handoff_ticket` / `chat_feedback` /
> `chat_tool_call` 与物化视图 `quality_daily`。
> 所有 SQL 可直接执行；把 `:tenant` / 时间范围替换为实际值。
> BI/Grafana 看板由运维按环境搭，本文件是指标口径的单一定义。

## 0. 物化视图 quality_daily（高频日聚合，先刷再查）

```bash
uv run python scripts/refresh_quality_views.py    # CONCURRENTLY 刷新，不锁读
```

```sql
-- 近 30 天日报：轮次/会话量、兜底率、失败率、P95 耗时
SELECT day, turns, sessions,
       round(fallback_turns::numeric / NULLIF(turns, 0), 4)  AS fallback_rate,
       round(failed_turns::numeric  / NULLIF(turns, 0), 4)   AS failure_rate,
       round(low_conf_turns::numeric / NULLIF(turns, 0), 4)  AS low_conf_rate,
       p50_latency_ms, p95_latency_ms
FROM quality_daily
WHERE tenant_id = :tenant AND day >= current_date - 30
ORDER BY day DESC;
```

## 1. 一次解决率

口径：会话内**无转人工工单**且**无连续 2 轮 UNKNOWN 兜底**即视为 bot 一次解决。

```sql
WITH session_flags AS (
    SELECT d.session_id,
           bool_or(t.id IS NOT NULL)            AS has_handoff,
           max(cnt.streak)                      AS max_unknown_streak
    FROM chat_decision_log d
    LEFT JOIN chat_handoff_ticket t
        ON t.tenant_id = d.tenant_id AND t.session_id = d.session_id
    LEFT JOIN LATERAL (
        -- 连续 FALLBACK 最大连击（窗口分组法）
        SELECT max(c) AS streak FROM (
            SELECT count(*) AS c FROM (
                SELECT status,
                       row_number() OVER (ORDER BY created_at)
                       - row_number() OVER (PARTITION BY (status = 'FALLBACK') ORDER BY created_at) AS grp
                FROM chat_decision_log
                WHERE tenant_id = d.tenant_id AND session_id = d.session_id
            ) g WHERE status = 'FALLBACK' GROUP BY grp
        ) s
    ) cnt ON true
    WHERE d.tenant_id = :tenant AND d.created_at >= now() - interval '7 days'
    GROUP BY d.session_id
)
SELECT count(*)                                                        AS sessions,
       count(*) FILTER (WHERE NOT has_handoff
                          AND coalesce(max_unknown_streak, 0) < 2)     AS resolved_first_time,
       round(count(*) FILTER (WHERE NOT has_handoff
                          AND coalesce(max_unknown_streak, 0) < 2)::numeric
             / NULLIF(count(*), 0), 4)                                 AS first_resolution_rate
FROM session_flags;
```

## 2. 转人工率与 reason 分布

```sql
-- 转人工率 = 建单会话数 / 总会话数
SELECT
    (SELECT count(DISTINCT session_id) FROM chat_handoff_ticket
      WHERE tenant_id = :tenant AND created_at >= now() - interval '7 days')::numeric
    / NULLIF((SELECT count(*) FROM chat_session
      WHERE tenant_id = :tenant AND created_at >= now() - interval '7 days'), 0)
    AS handoff_rate;

-- reason 分布与处理时效
SELECT reason, count(*) AS tickets,
       count(*) FILTER (WHERE status = 'RESOLVED')                       AS resolved,
       round(avg(EXTRACT(EPOCH FROM (resolved_at - created_at)) / 60)
             FILTER (WHERE resolved_at IS NOT NULL), 1)                  AS avg_resolve_minutes
FROM chat_handoff_ticket
WHERE tenant_id = :tenant AND created_at >= now() - interval '7 days'
GROUP BY reason ORDER BY tickets DESC;
```

## 3. 意图分布与低置信率

```sql
SELECT intent_result_json ->> 'final_intent'                              AS intent,
       count(*)                                                          AS turns,
       count(*) FILTER (
           WHERE (intent_result_json ->> 'confidence')::float < 0.6)     AS low_conf,
       round(avg((intent_result_json ->> 'confidence')::float), 3)       AS avg_conf,
       string_agg(DISTINCT decision_source, ',')                         AS sources
FROM chat_decision_log
WHERE tenant_id = :tenant AND created_at >= now() - interval '7 days'
  AND intent_result_json IS NOT NULL
GROUP BY 1 ORDER BY turns DESC;
```

## 4. RAG 拒答率

```sql
SELECT count(*)                                                             AS rag_turns,
       count(*) FILTER (WHERE (retrieval_json ->> 'refused')::boolean)      AS refused,
       round(count(*) FILTER (WHERE (retrieval_json ->> 'refused')::boolean)::numeric
             / NULLIF(count(*), 0), 4)                                      AS refusal_rate
FROM chat_decision_log
WHERE tenant_id = :tenant AND created_at >= now() - interval '7 days'
  AND retrieval_json ? 'refused';   -- 只统计走了检索的轮次
```

## 5. 确认门通过率

```sql
-- 确认门应答轮：CONFIRMING 状态下的 CONFIRM/DENY（含 LLM 解析改写）
SELECT count(*) FILTER (WHERE intent_result_json ->> 'pred_label' = 'META.CONFIRM') AS confirmed,
       count(*) FILTER (WHERE intent_result_json ->> 'pred_label' = 'META.DENY')    AS denied,
       round(count(*) FILTER (WHERE intent_result_json ->> 'pred_label' = 'META.CONFIRM')::numeric
             / NULLIF(count(*), 0), 4)                                              AS confirm_rate
FROM chat_decision_log
WHERE tenant_id = :tenant AND created_at >= now() - interval '7 days'
  AND intent_result_json ->> 'pred_label' IN ('META.CONFIRM', 'META.DENY');
```

## 6. 工具失败率（按 tool_id）

```sql
SELECT tool_id, count(*) AS calls,
       count(*) FILTER (WHERE NOT ok)                                    AS failures,
       round(count(*) FILTER (WHERE NOT ok)::numeric / count(*), 4)      AS failure_rate,
       round(avg(latency_ms), 1)                                         AS avg_latency_ms
FROM chat_tool_call
WHERE tenant_id = :tenant AND created_at >= now() - interval '7 days'
GROUP BY tool_id ORDER BY failure_rate DESC, calls DESC;
```

## 7. P95 轮次耗时（明细口径，物化视图外的即席查询）

```sql
SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY (latency_json->>'total_ms')::float) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY (latency_json->>'total_ms')::float) AS p95_ms,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY (latency_json->>'total_ms')::float) AS p99_ms
FROM chat_decision_log
WHERE tenant_id = :tenant AND created_at >= now() - interval '24 hours'
  AND latency_json ? 'total_ms';
```

## 8a. 会话满意度 CSAT（Stage 15）

```sql
-- 均分与分布（quality_daily 已含 csat_avg/csat_count 日聚合）
SELECT score, count(*) AS cnt, trigger
FROM chat_csat
WHERE tenant_id = :tenant AND created_at >= now() - interval '30 days'
GROUP BY score, trigger ORDER BY score;

-- 低分会话明细（自动进 export_review_set 待审）
SELECT c.created_at, c.score, c.trigger, c.session_id
FROM chat_csat c
WHERE c.tenant_id = :tenant AND c.score <= 2
ORDER BY c.created_at DESC LIMIT 50;
```

## 8. 用户反馈（点赞/点踩）

```sql
SELECT rating, count(*) AS cnt,
       round(count(*)::numeric / sum(count(*)) OVER (), 4) AS pct
FROM chat_feedback
WHERE tenant_id = :tenant AND created_at >= now() - interval '7 days'
GROUP BY rating;

-- 点踩明细（回流审核入口，等价于 export_review_set 的差评来源）
SELECT f.created_at, f.comment, m.content AS ai_reply, m.intent
FROM chat_feedback f JOIN chat_message m ON m.id = f.message_id
WHERE f.tenant_id = :tenant AND f.rating = 'down'
ORDER BY f.created_at DESC LIMIT 50;
```

## 运行时指标（Prometheus，与 SQL 口径互补）

`GET /metrics` 暴露：`chat_turn_duration_seconds`（意图域×分支直方图）、
`intent_decisions_total`（意图×来源）、`llm_calls_total`/`llm_failures_total`、
`rag_retrievals_total`（faq_hit/rag_answer/refused/degraded）、`confirm_gate_total`、
`action_executions_total`、`handoff_tickets_total`、`rate_limited_total`、`chat_feedback_total`。
多租户维度不进 label（基数控制），租户级分析用上面的 SQL。
