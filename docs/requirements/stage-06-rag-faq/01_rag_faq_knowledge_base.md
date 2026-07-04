# Stage 06 需求：RAG / FAQ / 向量知识库（Milvus 后端）

> 前置阅读：`docs/chat/intent_taxonomy.md`（FAQ.GENERAL 意图与 6.3 边界裁决）、`docs/architecture/roadmap.md` 3.4 节。
> 版本说明：v2.1 曾规划 pgvector + Elasticsearch 双后端同时实现；
> **v3 决策：向量数据库先用 Milvus**，检索层保留后端抽象（`VectorStoreBackend` 协议），
> pgvector / ES 降级为后续可选后端（新增一个 backend 文件即可接入）。
> **状态：✅ 已实现（2026-07-02，实现记录见文末附录）。**
> 注：本阶段提前于 Stage 04/05 落地（用户决策）；LLM 生成路径已预留，
> 未配置 API Key 时用「FAQ 标准答案 + 摘录式回答」离线运行。

---

## 1. 阶段目标

建立租户隔离的知识库与检索增强生成（RAG）能力，覆盖两类场景：
① 平台政策/规则类问答（FAQ.GENERAL 意图，纯 RAG）；
② 长尾问题兜底（META.UNKNOWN 且无进行中任务时先过一层知识库）。
核心质量要求：**检索不到就拒答，绝不编造**；每次回答可溯源（检索轨迹与引用落决策日志）。

架构原则：
- **PostgreSQL 是知识库唯一事实来源**（原文、分块、embedding 全落 PG）；
  Milvus 只是「可随时重建的索引视图」，`python -m app.kb.reindex` 可从 PG 全量重建；
- 上层只依赖 `VectorStoreBackend` 协议，不感知具体后端；
- 知识库故障不允许打断聊天主链路（rag_answer 节点内全量兜底降级）。

## 2. 实现内容

1. **向量后端抽象**（`app/kb/backends/base.py`）
   - `VectorStoreBackend` 协议：index_chunks / index_faqs / delete_document / delete_faq /
     search_chunks / search_faqs / health；tenant_id 必传、实现内部强制过滤；
   - score 统一为余弦相似度语义（0~1），跨后端阈值可互换；
   - `rrf_fuse`：多路召回 RRF 融合公共函数。

2. **Milvus 后端**（`app/kb/backends/milvus_backend.py`）
   - pymilvus `AsyncMilvusClient`（全 async，MILVUS_TIMEOUT 超时）；
   - collection：`kb_chunk_v1` / `kb_faq_v1`（string 主键 + COSINE 向量 +
     动态字段承载 tenant_id/document_id），首次使用自动创建；
   - Milvus 只存 id + 向量 + 过滤字段，命中内容由 PG 水合；
   - tenant_id 过滤表达式做白名单转义，防注入。

3. **知识库表**（PG，见 `docs/database/chat_tables.md` 第 7 节）
   - `kb_document`（含 needs_reindex 标记）/ `kb_chunk`（embedding_json JSONB 存向量）/
     `faq_entry`（标准问答 + hit_count）；
   - `chat_decision_log` 新增 `retrieval_json` 列。

4. **Embedding 客户端**（`app/kb/embedding.py`）
   - `EMBEDDING_PROVIDER=openai`：OpenAI 兼容接口（langchain-openai，带超时）；
   - `EMBEDDING_PROVIDER=hash`：本地确定性伪向量（jieba 分词 + 特征哈希 + L2 归一），
     **仅开发/测试**——相似措辞相似向量，可离线跑通全链路，但无真实语义能力；
   - 维度 EMBEDDING_DIM 与 Milvus collection 绑定，变更需 reindex。

5. **摄取管道**（`app/kb/ingest.py` + `app/api/routes/kb.py`）
   - 清洗（去 HTML）→ 分块（段落/句子边界，KB_CHUNK_SIZE/OVERLAP）→ 批量 embedding
     → 写 PG（同事务）→ 写 Milvus；
   - Milvus 写失败不回滚 PG，标记 `needs_reindex=true` 告警，reindex 兜底；
   - 文档更新整篇重建分块；删除为软停用（PG disabled + Milvus 删索引）；
   - 管理 API：`POST /api/kb/documents`、`POST /api/kb/faqs`、`DELETE /api/kb/documents/{id}`，
     配置 KB_ADMIN_TOKEN 后需带 `X-KB-Admin-Token` 头（Stage 08 前过渡）。

6. **两级检索**（`app/kb/retriever.py`）
   - FAQ 精确层：问题向量相似度 ≥ FAQ_HIT_THRESHOLD → 直接返回标准答案（hit_count+1）；
   - 文档层混合检索：Milvus 向量召回 + PG 关键词召回（jieba 分词 ILIKE，只查 active 文档）
     → RRF 融合 → PG 水合内容与标题；
   - 完整轨迹（含未达阈值的分数）记录到 RetrievalTrace，**拒答轮次也落库**。

7. **回答生成**（`app/kb/answerer.py`）
   - FAQ 命中 → 标准答案原文（零幻觉）；
   - 文档命中且向量 top1 ≥ RAG_MIN_SCORE →
     有 API Key：LLM 依据片段生成（system prompt 含「资料不足必须说不知道」+ 数字不得改写）；
     无 Key / LLM 失败：**摘录式降级**（引用最相关分块原文 + 来源，绝不改写）；
   - 未达阈值 → 拒答（None），调用方走澄清/核实话术。

8. **主链路接入**（`app/chat/graph/builder.py` 条件路由 + `nodes/rag_answer.py`）
   - `skill_resolve` 后条件分支：FAQ.GENERAL 意图，或 META.UNKNOWN 兜底且无 active_task
     → `rag_answer`，否则 `response_generate`；两者汇入 `save_turn`；
   - 新增 FAQ.GENERAL 意图（规则关键词：政策/规定/规则/保修/会员/积分；
     完整识别 Stage 04 由 LLM 分类器承担）；
   - RAG 回答的 `answer_source` 与 `citations` 写入 chat_message.metadata_json；
   - rag_answer 节点内任何异常降级为模板回复，不打断主链路；
   - `/api/health/ready` 增加 `kb_milvus` 依赖状态（down 不影响整体 ready，只降级 RAG）。

## 3. 本阶段不做

- 知识库管理后台 UI（只有 API）；多模态（图片/表格解析）；在线增量爬取；
- pgvector / Elasticsearch 后端（接口已预留，需要时新增 backend 文件 + factory 登记）；
- rerank 模型（RRF 先行）；PG↔Milvus 双写强一致（reindex 兜底，接受秒级滞后）。

## 4. 配置项（.env.example 已同步）

```text
KB_ENABLED / KB_BACKEND=milvus / MILVUS_URI / MILVUS_TOKEN / MILVUS_TIMEOUT
EMBEDDING_PROVIDER=openai|hash / EMBEDDING_MODEL / EMBEDDING_DIM / EMBEDDING_TIMEOUT
FAQ_HIT_THRESHOLD=0.88 / RAG_MIN_SCORE=0.60 / RAG_TOP_K=5
KB_CHUNK_SIZE=500 / KB_CHUNK_OVERLAP=50 / KB_ADMIN_TOKEN
```

> 阈值默认按真实语义 embedding 校准；`EMBEDDING_PROVIDER=hash`（开发模式）下
> 相似度整体偏低，建议降为 `FAQ_HIT_THRESHOLD=0.6`、`RAG_MIN_SCORE=0.2~0.25`。
> 接入真实 embedding（Stage 04 后）必须用评估集重新标定阈值。

## 5. 验证方式（已全部通过，2026-07-02）

1. `POST /api/kb/documents` 摄取退换货政策 + 2 条 FAQ → Milvus collection 自动创建，返回分块数。
2. 「会员积分怎么用」→ FAQ.GENERAL 意图 → FAQ 精确命中，返回标准答案原文。
3. 「你们无理由退换的政策是什么」→ 文档层混合检索（向量+关键词双路召回）→ 摘录式回答含来源引用；
   chat_message.metadata_json 记录 `{answer_source, citations}`。
4. 「运费谁承担」（无规则关键词）→ META.UNKNOWN 兜底过知识库 → FAQ 命中回答。
5. 「今天天气如何」→ 拒答走澄清话术，不编造；decision_log.retrieval_json 记录 refused=true 及全部命中分数。
6. 租户 t2 提问不命中 t1 知识。
7. 业务链路回归：退款→补槽→确认门三轮不受影响。
8. `python -m app.kb.reindex --tenant t1` 从 PG 全量重建 Milvus 索引成功。
9. 单元测试：分块器 / hash embedding / RRF / 回答策略（含拒答与摘录降级）12 个用例通过；
   ruff / mypy 通过。

## 6. 遗留与后续

```text
1. 检索评估集（docs/testing/rag_eval_set.md，≥30 组）在接入真实 embedding 后建设，
   同时标定 FAQ_HIT_THRESHOLD / RAG_MIN_SCORE。
2. Stage 04 完成后：FAQ.GENERAL 由 LLM 分类器识别（规则关键词只是过渡）；
   RAG 生成自动切 LLM 路径（代码已支持，配 OPENAI_API_KEY 即生效）。
3. Skill 的 rag_fallback: true（READ 类工具无结果转检索）依赖 Stage 05 工具层，届时接入。
4. 中文全文检索升级（pg_jieba / Milvus BM25 sparse）待评估数据驱动决策，
   当前关键词路为 jieba 分词 + ILIKE。
```

---

## 附录：实现记录（2026-07-02）

| 模块 | 文件 |
|---|---|
| 后端协议 + RRF | `app/kb/backends/base.py` |
| Milvus 后端 | `app/kb/backends/milvus_backend.py`、`factory.py` |
| Embedding | `app/kb/embedding.py`（openai / hash 双实现） |
| 清洗分块 | `app/kb/chunker.py` |
| 摄取 | `app/kb/ingest.py` |
| 检索编排 | `app/kb/retriever.py`（FAQ 层 + 混合层 + 轨迹） |
| 回答 | `app/kb/answerer.py`（FAQ/LLM/摘录/拒答） |
| 重建 CLI | `app/kb/reindex.py` |
| 管理 API | `app/api/routes/kb.py` |
| 主链路 | `app/chat/graph/nodes/rag_answer.py`、`builder.py`（条件路由） |
| 模型/仓储 | `app/models/kb_*.py`、`faq_entry.py`、`app/repositories/kb_*.py`、`faq_entry_repository.py` |
| 迁移 | `alembic/versions/20260702_*_add_kb_tables_*.py` |
| 测试 | `tests/kb/`（chunker / embedding / rrf / answerer） |

本地 Milvus（standalone，内嵌 etcd + 本地存储）：

```bash
docker run -d --name milvus-standalone --security-opt seccomp:unconfined \
  -e ETCD_USE_EMBED=true -e ETCD_DATA_DIR=/var/lib/milvus/etcd \
  -e ETCD_CONFIG_PATH=/milvus/configs/embedEtcd.yaml -e COMMON_STORAGETYPE=local \
  -v <本地目录>/embedEtcd.yaml:/milvus/configs/embedEtcd.yaml \
  -v milvus_kb_data:/var/lib/milvus \
  -p 19530:19530 -p 9091:9091 milvusdb/milvus:v2.5.10 milvus run standalone
```

（embedEtcd.yaml 内容见 `docs/ops/local_dev_and_runbook.md`。）
