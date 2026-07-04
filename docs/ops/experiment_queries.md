# A/B 实验对比查询（Stage 18）

> 按 `chat_decision_log.experiment_json` 切分对比各变体效果。指标口径与 `quality_daily`
> 物化视图一致（见 `docs/ops/quality_queries.md`），这里额外按 **实验 × 变体** 维度拆分。
>
> **红线**：框架只负责把数据按变体切好、算好比率与样本量；**「够不够显著、要不要全量」由运营看数据决定**，
> 不自动下结论、不自动调参。

`experiment_json` 结构：

```json
{"assignments": [{"exp_id": "exp_rag", "variant": "low_thresh"}],
 "overrides": {"RAG_MIN_SCORE": 0.4}}
```

单实验取 `assignments[0]`；多实验并行时用 `jsonb_array_elements` 展开（见 §4）。

---

## 1. 变体核心指标对比（单实验）

一次解决率(DONE)、转人工率、拒答率、P95 时延、样本量——按变体分组：

```sql
SELECT
    experiment_json -> 'assignments' -> 0 ->> 'exp_id'   AS exp_id,
    experiment_json -> 'assignments' -> 0 ->> 'variant'  AS variant,
    count(*)                                             AS turns,
    count(DISTINCT session_id)                           AS sessions,
    round(avg((status = 'DONE')::int)::numeric, 4)       AS done_rate,
    round(avg((status = 'HANDOFF')::int)::numeric, 4)    AS handoff_rate,
    round(avg(((retrieval_json ->> 'refused')::boolean IS TRUE)::int)::numeric, 4)
                                                         AS rag_refused_rate,
    percentile_cont(0.95) WITHIN GROUP (
        ORDER BY (latency_json ->> 'total_ms')::float
    )                                                    AS p95_latency_ms
FROM chat_decision_log
WHERE experiment_json IS NOT NULL
  AND experiment_json -> 'assignments' -> 0 ->> 'exp_id' = :exp_id
  AND created_at >= :since
GROUP BY 1, 2
ORDER BY variant;
```

> `turns` / `sessions` 就是样本量——**样本太小先别看比率差异**（见 §3）。

## 2. 变体 CSAT 对比

CSAT 落在 `chat_csat`，需按 message/session 关联到该轮的变体。最简做法是把变体也带进
决策日志后按会话关联（会话内变体稳定，取该会话任一轮的变体即可）：

```sql
WITH sess_variant AS (   -- 每会话的变体（会话内确定性稳定，取一条即可）
    SELECT DISTINCT ON (session_id)
        session_id,
        experiment_json -> 'assignments' -> 0 ->> 'variant' AS variant
    FROM chat_decision_log
    WHERE experiment_json -> 'assignments' -> 0 ->> 'exp_id' = :exp_id
    ORDER BY session_id, created_at
)
SELECT sv.variant,
       round(avg(c.score)::numeric, 2) AS csat_avg,
       count(*)                        AS csat_count
FROM chat_csat c
JOIN sess_variant sv USING (session_id)
GROUP BY sv.variant
ORDER BY sv.variant;
```

## 3. 样本量与显著性口径（辅助判断，不替下结论）

比较 control vs variant 的某个比率（如 done_rate）是否可能真有差异，先看样本量，再看
两比例 z 检验的近似量。**这是给运营的判断辅助，不是自动结论**：

```sql
-- 输入两个变体的 成功数 x 与样本量 n，输出比率差与近似 z 值
WITH s AS (
    SELECT
        count(*) FILTER (WHERE variant = 'control')                        AS n_c,
        count(*) FILTER (WHERE variant = 'control' AND ok)                 AS x_c,
        count(*) FILTER (WHERE variant = :variant)                         AS n_v,
        count(*) FILTER (WHERE variant = :variant AND ok)                  AS x_v
    FROM (
        SELECT experiment_json -> 'assignments' -> 0 ->> 'variant' AS variant,
               (status = 'DONE')                                   AS ok
        FROM chat_decision_log
        WHERE experiment_json -> 'assignments' -> 0 ->> 'exp_id' = :exp_id
          AND created_at >= :since
    ) t
)
SELECT n_c, n_v,
       round((x_c::numeric / nullif(n_c,0)), 4) AS rate_c,
       round((x_v::numeric / nullif(n_v,0)), 4) AS rate_v,
       -- 两比例 z 近似（|z|≳1.96 约对应 95% 置信；样本小则不可信）
       round(
         ((x_v::numeric/nullif(n_v,0)) - (x_c::numeric/nullif(n_c,0)))
         / nullif(sqrt(
             ((x_c + x_v)::numeric / nullif(n_c + n_v,0))
             * (1 - (x_c + x_v)::numeric / nullif(n_c + n_v,0))
             * (1.0/nullif(n_c,0) + 1.0/nullif(n_v,0))
           ), 0)
       , 3) AS z_approx
FROM s;
```

经验口径（非硬门槛）：
- 每变体样本 **< 数百轮**：差异多半是噪声，继续观察，别急着下结论；
- `|z_approx| < 1.96`：未达常用 95% 置信，视作「暂无显著差异」；
- 达标且业务指标（如 done_rate↑、handoff_rate↓）方向一致 → 交运营决定是否全量。

## 4. 多实验并行（展开 assignments）

同一轮命中多个实验时用 `jsonb_array_elements` 展开，按 (exp_id, variant) 分组：

```sql
SELECT a ->> 'exp_id'  AS exp_id,
       a ->> 'variant' AS variant,
       count(*)        AS turns,
       round(avg((status = 'DONE')::int)::numeric, 4) AS done_rate
FROM chat_decision_log,
     jsonb_array_elements(experiment_json -> 'assignments') AS a
WHERE experiment_json IS NOT NULL
  AND created_at >= :since
GROUP BY 1, 2
ORDER BY 1, 2;
```

## 5. 决策回放对拍（定性）

对同类 query 在不同变体下的回答做人工对拍，用现有回放工具：

```bash
# 按会话回放某会话完整决策轨迹（含 experiment_json 变体）
uv run python scripts/replay_trace.py --session <session_id>
```

配合 §1 找到分属不同变体、问了相似问题的会话，人工对比回答质量。
