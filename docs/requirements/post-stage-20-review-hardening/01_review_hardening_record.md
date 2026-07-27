# Post-Stage 20 全链路 Review 加固与 RAG 强化（实现记录）

> 日期：2026-07-27
> 性质：**实现记录**（修订清单），不是待执行需求文档。本轮为全链路代码 review 后的
> 六批整改：意图控制层修复、正确性 bug、并发容量、延迟优化、演进项、RAG 强化
> （参考 [Tencent WeKnora](https://github.com/Tencent/WeKnora) 的分块/检索/引用设计）。
> 全部改动零 migration、零 API 协议变更；验证基线：全量 pytest 274 → **300 passed**
> （净增 26 个回归测试），ruff / mypy 全绿。

---

## 1. 意图控制层修复（表达多样性误判）

背景：规则控制层优先级最高且短路 SetFit，裸子串匹配导致确定性误判
（「你是真人吗」→误建人工工单；「算了，还是帮我退款吧」→退款诉求被吞；
「订单被取消了是怎么回事」→误开取消流程）。

| 修复 | 文件 | 说明 |
|---|---|---|
| 纯放弃判定 | `app/chat/intent/rule_classifier.py` | META.ABORT 从裸子串改为 `_is_pure_abort`：去掉放弃词后剩余字符须全是语气/连接成分。长句夹带诉求（退款等）放行语义层；长句纯放弃（「算了，然后都不用了」）仍判 ABORT |
| 身份先于转人工 | 同上 | META.BOT_IDENTITY 判定顺序提前，「你是真人吗」不再被「真人」子串误吞成转人工 |
| 取消正则排除被动式 | 同上 | `_CANCEL_ORDER_RE`：「取消」前排除「被/已」，「订单…取消」中间不得隔「被/已」——被动/完成式是状态咨询不是取消请求 |
| 评估集固化 | `tests/eval/test_intent_eval.py` | CONTROL_CASES 增 5 例；新增 `test_control_layer_passthrough_cases`（6 个含控制关键词的业务咨询必须放行语义层） |

## 2. 第一批：正确性 bug

| 修复 | 文件 | 说明 |
|---|---|---|
| 语义缓存跨用户泄漏 | `app/kb/answerer.py`、`app/chat/cache/semantic_cache.py`、`nodes/rag_answer.py` | `RagAnswer.personalized` 标记（LLM 生成且注入了用户长期事实）；红线收口在 `store()`：个性化回答不进租户共享缓存 |
| metadata_json 并发写覆盖 | `app/chat/memory/local_provider.py`、`chat_session_repository.py` | `_maybe_summarize` 改三段式：读（短事务）→ LLM（不持连接）→ 写（新增 `merge_metadata`：JSONB `\|\|` 顶层合并 + `memory_summary_covered` CAS）。并发 locale 写不再被冲掉；并发摘要任务先到者生效 |
| 向量召回不过滤发布状态 | `app/kb/retriever.py`、`kb_document_repository.py` | 生效过滤（published_version 非空且未 archived）提前到 rerank/截断**之前**，死文档不挤占 top_k、有回填；顺带 chunk 双查合一、文档标题批量取（新增 `get_by_ids`） |
| 摘要每轮触发 LLM | `app/core/config.py`、`local_provider.py` | 新增 `MEMORY_SUMMARY_STEP=6`：超阈值后增量累积到步长才滚动续写（原实现每轮 1 次摘要 + 1 次事实抽取）；首次摘要不受限 |

## 3. 第二批：并发容量

| 改动 | 文件 | 说明 |
|---|---|---|
| 请求事务拆分（收益最大） | `nodes/load_session_state.py`、`app/kb/answerer.py` | 利用「Session commit 后连接立即还池」（已实证）：load 结束提交一次、RAG 检索完进 LLM 前提交一次。**整个 LLM 中段连接池占用为 0**（此前一条连接被整轮持有，30 连接池 ≈ 10 QPS 上限）。dialog_state 乐观锁在 save_turn 提交时把关，409 语义不变 |
| 轮级 LLM 时间预算 | 新模块 `app/chat/llm/deadline.py`、`factory.py`、`answerer.py`、`chat_service.py` | `TURN_LLM_BUDGET_SECONDS=40`：本轮全部 LLM 调用共享 deadline；耗尽走既有降级路径（与无 Key 同路径）；单次调用以剩余预算为外层 `wait_for` 超时。后台记忆任务 `clear_turn_budget()` 防被过期 deadline 饿死。0=关闭 |
| 后台记忆任务并发闸 | `app/chat/memory/scheduler.py` | `MEMORY_TASK_CONCURRENCY=3`：Semaphore 限流（按事件循环懒建），不再 N QPS = N 个任务抢连接池/LLM 额度 |

## 4. 第三批：延迟优化

| 改动 | 文件 | 说明 |
|---|---|---|
| embedding 去重 4→1 | `nodes/rag_answer.py`、`retriever.py`、`answerer.py`、`semantic_cache.py` | 查询向量节点入口算一次，缓存查/写、FAQ 层、文档层全程复用（各接口加可选 `query_vec`/`emb` 参数，不传自算=零回归） |
| 语义缓存不阻塞事件循环 | `semantic_cache.py` | 比对改 numpy 矩阵点积 + `asyncio.to_thread`（原为事件循环内 200×512 纯 Python 循环，期间所有并发请求被卡）；写入 lpush/ltrim/expire 合一次 pipeline |
| RAG 生成收口 factory | `answerer._generate` → `chat_completion(purpose="rag")` | 删手搓 ChatOpenAI（每轮 TLS 握手 + fd 泄漏 + 绕过指标/Langfuse/预算）。新增 `rag` 用途档（temp 0.3、smart tier） |
| 检索两路并行 | `retriever.search_chunks` | 向量路（Milvus）∥ 关键词路（PG）gather——只有关键词路碰 AsyncSession，并发安全 |
| 章节上下文去 N+1 | `kb_chunk_repository.list_sections` | (document_id, section_path) 一次 `tuple_ IN` 批量查询替代逐个查 |
| 分段延迟指标 | `app/core/metrics.py` | `kb_stage_duration_seconds{stage=embed/vector/keyword/rerank/sections/llm}`——优化收益可在线上验证 |
| 冷启动预热 | `app/main.py` lifespan | jieba 词典 + 本地重排模型 `to_thread` 预热，不再打在首个检索请求上 |

## 5. 第四批：演进项（可验证部分）

| 改动 | 文件 | 说明 |
|---|---|---|
| blocked 条件边 | `app/chat/graph/builder.py` | load_session_state（接管静默/CSAT）与 guardrail_check（拦截）后短路直达 response_generate，不再空跑 5-7 个透传节点；节点内 blocked 防御检查保留 |
| 多意图段并行分类 | `app/chat/intent/multi_intent.py` | 段级分类 `asyncio.gather`，耗时从各段之和降为最大值 |
| trace.reranked 记实际行为 | `retriever.py` | 以 `rerank_score` 是否写入为准（模型加载失败静默降级时不再谎报 True） |
| 歧义检测分数同源 | `retriever.py` | 在生效候选集上按**向量分**取前二判定（原为「按重排名次取前二、比向量分」两体系混用） |
| A/B 轮次绕过语义缓存 | `nodes/rag_answer.py` | 实验命中轮 lookup/store 全跳过，防变体经共享缓存互相污染 |
| Milvus 客户端竞态 | `app/kb/backends/milvus_backend.py` | `_get_client`/`_ensure_collection` 加 asyncio.Lock（注意不可重入，客户端获取在锁外）；同步客户端单例复用 + `to_thread`（health 探活不再每秒新建 TCP + 阻塞事件循环） |

## 6. RAG 强化（WeKnora 对齐）

已具备无需重做：父子分块（section_path 章节聚合，Stage 06-04）、多路召回 + 动态加权 RRF。

| 新增 | 文件 | 说明 |
|---|---|---|
| 含糊查询改写（先重写再检索） | 新模块 `app/kb/query_rewrite.py`、`nodes/rag_answer.py` | 指代/省略式查询（「刚才那个多少钱」）结合近期对话 LLM 改写成独立检索查询。启发式触发（清晰查询零调用）、无 Key 失效、输出治理（首行/限长/同文不改）。**改写轮次绕过语义缓存**（结果依赖对话上下文，进共享缓存必错答）；`retrieval_json.rewritten_from` 留痕 |
| 引用溯源细化 | `answerer.py`、`retriever.py` | prompt 要求引用标注 `[n]`；生成后解析映射回命中块——citations 只列实际被引用文档、chunk id 落 `trace.cited`（引用浮层数据基础）；解析不到回退全部命中 |
| rerank 后段落清洗 | `answerer._build_context` | 跨命中块行级去重（同章节多命中 section_context 高度重叠）；整块重复保留编号占位（编号与命中下标严格对应，不破坏溯源） |
| 关键词召回 BM25-lite 细排 | `kb_chunk_repository.rank_keyword_matches`（纯函数） | 主序仍命中词数（零语义回归）；并列用加权分细排：长词/型号权重≈长度/2、chunk 长度对数归一 |

## 7. 新增配置项

| 配置 | 默认 | 说明 |
|---|---|---|
| `TURN_LLM_BUDGET_SECONDS` | 40.0 | 轮级 LLM 时间预算，0=关闭 |
| `MEMORY_TASK_CONCURRENCY` | 3 | 后台记忆任务并发上限 |
| `MEMORY_SUMMARY_STEP` | 6 | 摘要滚动步长（新增消息数） |
| `RAG_QUERY_REWRITE_ENABLED` | true | 含糊查询 LLM 改写（无 Key 自动失效） |

新增指标：`kb_stage_duration_seconds{stage}`。新增协议/接口（向后兼容）：
`SemanticCacheProvider.lookup/store` 可选 `emb`、`RagAnswer.personalized`、
`RetrievalTrace.cited`、retriever/answerer 可选 `query_vec`、LLM `Purpose` 增 `"rag"`。

## 8. 明确暂缓项（及原因）

| 项 | 原因 |
|---|---|
| 真流式 SSE、LLM 理解调用合并（二判+槽位+确认门→一次结构化输出） | 需真实 LLM Key 验证；调用合并还需意图评估门禁护航防质量回退。协议已预留（meta/delta/done 不变） |
| 语义缓存迁 Milvus | Milvus 未启动无法测试；numpy 化后 Redis 方案痛点已消除 |
| 自适应三层分块（WeKnora） | 改分块器需全量重建索引 + 真实语料评估；现有结构感知切分已覆盖主要收益 |
| Prompt 模板在线编辑（WeKnora） | 与「防注入/禁编造硬约束不可运营改动」红线有张力；建议只把话术部分模板化，纳入 KB 运营前端规划 |
| ReAct 循环/final_answer（WeKnora） | 不适用：本项目刻意采用确定性 LangGraph + 确认门，不让 LLM 驱动工具循环 |
| 关键词路 pg_trgm GIN 索引 | `CREATE EXTENSION` 需超管权限，留给部署侧决策（`ILIKE '%kw%'` 无索引全表扫仍是关键词路慢点） |

## 9. 验证与注意事项

- 全量 pytest：**300 passed**；2 个失败为基线已有的环境问题（`test_rag_eval_gate` 需 Milvus；stage16 keyword 测试对执行顺序敏感的 Redis 初始化问题）。
- 事务拆分后语义变化：load 阶段的自愈写（隐式建会话/closed 重开/任务过期标记/locale）在轮次失败时**不再回滚**（均幂等/良性）；RAG 的 FAQ 命中计数同理。
- 查询改写与引用标注的**模型侧效果**需接真实 LLM 后跑 `tests/eval/test_rag_eval.py`（32 组）验证——离线测试只锁工程行为。
- 新增回归测试分布：`tests/eval`（控制层）、`tests/stage17`（缓存红线）、`tests/stage20`（并发/步长）、`tests/kb`（生效过滤/去重/溯源/清洗/细排/改写）、`tests/llm`（轮预算）、`tests/capacity`（连接释放）、`tests/graph`（条件边/实验绕缓存）。
