# Stage 06-04 需求：检索管道 v2（Hybrid Retriever 完整化）

> 前置阅读：`01_rag_faq_knowledge_base.md`、`03_retrieval_routing_and_product.md`。
> **状态：✅ 已实现（2026-07-03，实现与验证记录见文末）。**
> 背景：对照客服系统最优 Retriever Pipeline 参考方案逐项核对现状，
> 补齐缺失环节。核心原则不变：**LLM/模型知识永远不高于业务数据库与知识库**。

---

## 1. 参考方案 ↔ 现状对照（采纳决策表）

| 参考方案环节 | 现状核对 | 本次处置 |
|---|---|---|
| Query Normalization | 仅全半角/空白归一 | ✅ **新增**：繁简转换（zhconv）、同义词扩展（内置词表可扩展，扩展词只进关键词路）、型号/单号识别；拼写纠错暂缓（需专门模型，收益/成本比低） |
| Intent+Slot 选择 Retriever | ✅ 已有（检索路由矩阵 R1-R5：商品→商品库优先、政策→FAQ+向量、订单→工具、模糊→FAQ+向量） | 保持 |
| 多路召回 | 已有向量+关键词+FAQ 三路，但窄召回（top5） | ✅ **加宽**：各路 `RAG_RECALL_TOP_K`（默认 20）召回，融合/重排后截 `RAG_TOP_K` |
| 结果融合 | ✅ 已用 RRF（参考方案推荐的方案二） | ✅ **升级为动态加权 RRF**（方案二强化）：precise 查询（含型号/单号——向量对其不可靠）关键词路权重 1.5；semantic 查询向量路权重 1.2 |
| 过滤（tenant/enabled 等） | ✅ tenant 强制、status=active 过滤已有 | language/effective_time 字段位于 metadata_json，过滤钩子随租户需求启用（记录在案） |
| **Rerank** | ❌ 无 | ✅ **新增** Reranker 协议：`RERANKER_PROVIDER=off|local`（本地 CrossEncoder，默认 BGE reranker，懒加载线程池，失败自动降级 RRF 序）。默认 off——CPU 环境先不强制，生产建议开 |
| **上下文组装（父子分块）** | ❌ 只有子块 + 标题路径注入 | ✅ **新增**：kb_chunk 加 `section_path` 列（标题路径连接串），命中子块按 (document, section) 聚合章节兄弟块为 `section_context`（行级去重、字符上限），生成与摘录都优先用章节上下文——「运费谁承担」这类分情形答案不再断章 |
| 答案校验（grounding/引用/红线/转人工） | ✅ 已有（拒答阈值、citations、guardrails、转人工路径） | 保持；输出结构（used_sources/confidence）已落 decision_log 与 message metadata |
| **冲突检测** | ❌ 无 | ✅ **轻量版**：top1/top2 向量分差 < `RAG_AMBIGUITY_DELTA` 且异文档 → trace.ambiguous；摘录式回答附「不同商品/情形政策可能不同」提示；LLM 生成路径由多资料并列呈现自然处理。完整的「互斥结论→主动澄清」依赖 LLM 判定，列入后续（Stage 09 数据驱动后做） |
| 业务优先级 | ✅ 已有（实时工具/商品库 > FAQ > 知识库文档 > 模板；价格库存禁走 RAG 红线） | 保持 |
| 相似度阈值 | ✅ 已有（FAQ_HIT/RAG_MIN + 拒答） | 保持；rerank 分数阈值待真实模型标定后启用 |
| ES/BM25 稀疏检索 | 关键词路当前为 PG trgm+jieba（06-01 决策：Milvus 为向量库、ES 为可选后端） | 保持——`VectorStoreBackend` 抽象已预留 ES 接入位，量级需要时切换 |

## 2. 管道全景（实现后）

```text
用户输入
 → Query 归一化（繁简/同义词/型号识别 → precise|semantic 判型）
 → 检索路由 R1-R5（意图×状态决定查商品库/工具/FAQ/RAG，补槽确认门不检索）
 → FAQ 精确层（阈值命中直接返回标准答案）
 → 文档层：向量(Milvus) + 关键词(PG trgm，含扩展词/型号) 各宽召回 top20
 → 动态加权 RRF 融合
 → Rerank（可选 CrossEncoder）→ top5
 → 水合 + 父子分块章节上下文（行级去重）+ 歧义检测
 → LLM 生成（grounding 约束+记忆事实）/ 摘录降级（歧义附提示）
 → 拒答保护 → 引用与全轨迹落 decision_log.retrieval_json
```

## 3. 配置（.env.example 已同步）

```text
RAG_RECALL_TOP_K=20           # 每路召回宽度
RERANKER_PROVIDER=off         # off|local（生产建议 local，首次运行下载 RERANK_MODEL）
RERANK_MODEL=BAAI/bge-reranker-base
RAG_AMBIGUITY_DELTA=0.05      # 歧义检测分差阈值
RAG_SECTION_CONTEXT_CHARS=1200  # 章节上下文字符上限
```

## 4. 验证记录（2026-07-03，全部通过）

- 单测 5 个（累计 87）：繁简转换、同义词双向扩展、型号识别与判型、加权 RRF
  关键词路提权、rerank off 透传；
- e2e：繁体「你們的保修政策是什麼」命中简体知识库；「什么情况不在保修范围」
  返回章节聚合上下文（段落+保修期表格，行级去重无重复）；retrieval_json 记录
  query_type/expanded_terms/reranked/ambiguous；FAQ/商品/多意图全量回归无回退。
- 修复：表格块引导句与章节聚合的重复问题（行级去重）。

## 5. 遗留

```text
1. Rerank 真实效果验证：RERANKER_PROVIDER=local 首次运行下载 BGE reranker（~1.1GB），
   建议生产开启后用检索评估集对比 RRF-only；rerank 分数阈值届时标定。
2. 拼写纠错、租户级同义词词表（从配置/库加载）、language/effective_time 过滤启用
   ——按租户需求排期。
3. 冲突检测完整版（互斥结论→主动澄清追问）依赖 LLM 判定与真实数据，Stage 09 后做。
4. ES/BM25 作为可选检索后端保留（VectorStoreBackend 抽象已就位）。
```
