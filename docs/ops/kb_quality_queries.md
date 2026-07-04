# 知识库命中率与盲区查询（Stage 16）

> 数据源：`chat_decision_log.retrieval_json`（检索轨迹：查询词、命中分块 `chunk_hits[].doc`、
> 是否拒答 `refused`）、`faq_entry.hit_count`、`kb_document`。
> 所有 SQL 可直接执行；把 `:tenant` / 时间窗替换为实际值。
> 用途：让运营看到「哪些内容有用、哪些从没被命中、用户问了什么答不上来」，
> 形成「盲区发现 → 补充 → 审核 → 发布」闭环。

## 1. 热门文档（命中最多）

```sql
-- 从检索轨迹里展开 chunk_hits，按命中文档标题聚合
SELECT hit->>'doc' AS document, count(*) AS hits
FROM chat_decision_log d,
     jsonb_array_elements(d.retrieval_json -> 'chunk_hits') AS hit
WHERE d.tenant_id = :tenant AND d.created_at >= now() - interval '30 days'
  AND d.retrieval_json ? 'chunk_hits'
  AND hit->>'doc' IS NOT NULL AND hit->>'doc' <> ''
GROUP BY 1 ORDER BY hits DESC LIMIT 20;
```

## 2. 零命中文档（发布了却从没被命中，疑似冗余/标题不匹配）

```sql
-- 已发布文档中，近 30 天从未出现在任何 chunk_hits 里的
WITH hit_docs AS (
    SELECT DISTINCT hit->>'doc' AS document
    FROM chat_decision_log d,
         jsonb_array_elements(d.retrieval_json -> 'chunk_hits') AS hit
    WHERE d.tenant_id = :tenant AND d.created_at >= now() - interval '30 days'
      AND d.retrieval_json ? 'chunk_hits'
)
SELECT k.id, k.title, k.source_type, k.updated_at
FROM kb_document k
WHERE k.tenant_id = :tenant
  AND k.published_version IS NOT NULL AND k.status <> 'archived'
  AND k.title NOT IN (SELECT document FROM hit_docs WHERE document IS NOT NULL)
ORDER BY k.updated_at ASC;
```

## 3. 高拒答查询词（知识盲区——用户问了但答不上来）

```sql
-- 走了检索但拒答的查询，按归一化文本聚合（次数多=盲区，指向该补什么内容）
SELECT normalized_text AS query, count(*) AS refused_count
FROM chat_decision_log
WHERE tenant_id = :tenant AND created_at >= now() - interval '30 days'
  AND (retrieval_json ->> 'refused')::boolean IS TRUE
  AND normalized_text IS NOT NULL AND normalized_text <> ''
GROUP BY 1 ORDER BY refused_count DESC LIMIT 50;
```

## 4. FAQ 命中 TOP / 长尾

```sql
-- 高频 FAQ（价值高，优先维护）
SELECT question, hit_count, category
FROM faq_entry
WHERE tenant_id = :tenant AND status = 'active'
ORDER BY hit_count DESC LIMIT 20;

-- 长尾/零命中 FAQ（疑似冗余，可下线）
SELECT question, hit_count, category
FROM faq_entry
WHERE tenant_id = :tenant AND status = 'active' AND hit_count = 0
ORDER BY created_at ASC;
```

## 5. 文档运营审计（谁改了什么）

```sql
-- 审核动作历史（review_log 落在 metadata_json，Stage 16）
SELECT k.id, k.title, k.status, log->>'action' AS action,
       log->>'actor' AS actor, log->>'at' AS at, log->>'note' AS note
FROM kb_document k,
     jsonb_array_elements(k.metadata_json -> 'review_log') AS log
WHERE k.tenant_id = :tenant AND k.metadata_json ? 'review_log'
ORDER BY (log->>'at') DESC LIMIT 100;
```

## 盲区闭环

第 3 节的「高拒答查询词」+ Stage 09 的 `export_review_set --mode faq`（高频已答问题）
共同指向「该补什么 FAQ/文档」：运营据此新建草稿 → 提交审核 → 发布（Stage 16 流程），
补上后该查询词的拒答数应下降。定期跑第 3 节 SQL 观察闭环是否收敛。
