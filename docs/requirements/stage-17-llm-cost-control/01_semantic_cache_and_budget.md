# Stage 17 需求：LLM 成本控制（语义缓存 + 预算熔断 + 分级路由）

> 前置：语义缓存与框架**不依赖真实 LLM**（用现有 `embedding_client` 抽象，hash 模式可验证逻辑，
> 真实 embedding 换即用）；预算阈值/tier 划分的**标定**待真实用量。
> 来源：roadmap 3.6 backlog「LLM 成本控制」。核心红线沿用：LLM 是增强层，故障/超限一律降级不打断主链路。

---

## 1. 阶段目标

在不牺牲回答质量的前提下压 LLM 成本与延迟：高频同类问题走语义缓存直答、
租户 token 有预算与熔断、简单轮次走小模型。三块可独立开关、独立降级。

## 2. 本阶段要做什么

### 2.1 语义缓存（同问直答，最先做、最独立）

- `SemanticCacheProvider` 协议 + 实现：对**可缓存轮次**（FAQ/RAG 生成、闲聊等无副作用、
  非补槽/确认门/写操作）用查询 embedding 找历史高相似问答，相似度 ≥ `SEMANTIC_CACHE_THRESHOLD`
  直接返回缓存答案，省一次 LLM/检索调用；
- 存储：向量 + 答案缓存（v1 用 Redis + 现有 embedding；租户隔离；TTL）；命中率/节省计入指标；
- **红线**：只缓存无副作用的回答；价格/库存/订单等事实**永不缓存**（可能过期，资损）——
  按 answer_source 白名单（rag_llm/rag_extract/faq/chitchat 可缓存，tool/action/product 禁）；
- 失效：知识库 publish（Stage 16）时按租户清相关缓存，避免答旧内容；
- 降级：缓存后端故障 → 直接走正常链路（fail-open）。

### 2.2 租户 token 预算与熔断

- LLM 调用统一收口在 `app/chat/llm/factory.py`——加 usage 计数（从 provider 返回的
  token usage 取，Langfuse 已在采集，此处独立累计供熔断）；
- `tenant_llm_budget` 配置/表：日/月 token 上限；Redis 滑动累计；
- 超限熔断：达阈值后 LLM 增强层**全部降级到模板/规则**（润色/二判/槽位兜底/生成都跳过），
  主链路照常出模板答案——与「无 API Key」降级同路径，复用现成机制；
- 指标：`llm_tokens_total{tenant,purpose}`、`llm_budget_exceeded_total{tenant}`；接近阈值告警口径写文档。

### 2.3 模型分级路由

- `CHAT_MODEL` 升级为按 tier 配置：`CHAT_MODEL_FAST`（小模型，简单轮）/ `CHAT_MODEL_SMART`（大模型，难例）；
- 路由规则：意图二判/槽位抽取/短回复润色走 fast；RAG 生成/复杂润色走 smart；可 config 覆盖；
- 收口在 factory 的 `get_chat_model(purpose)`——已按 purpose 分，扩展为按 purpose 选 tier；
- 降级：未配置 fast 时全部回落 smart（行为不变）。

## 3. 本阶段不做什么

- 缓存答案的自动质量校验/人工审核（v1 靠相似度阈值 + 白名单保守缓存）；
- 跨租户共享缓存（严格租户隔离）；
- 精确成本核算与账单（只做 token 计量与熔断，计费属运营系统）。

## 4. 目录和文件要求

```text
app/chat/cache/semantic_cache.py         # SemanticCacheProvider 协议 + Redis 实现 + 工厂
app/chat/llm/factory.py                  # usage 收口 + 预算熔断 + 分级路由
app/chat/llm/budget.py                   # 租户 token 累计与熔断判定
app/chat/graph/nodes/                    # rag_answer/response_generate 接语义缓存（命中短路）
app/core/config.py                       # SEMANTIC_CACHE_* / LLM_BUDGET_* / CHAT_MODEL_FAST 等
app/core/metrics.py                      # 缓存命中/token/熔断指标
tests/stage17/
```

## 5. 验证方式

1. 语义缓存：同义问法（「退款多久到账」/「多久能退到钱」）第二次命中缓存直答、省 LLM 调用；
   价格/库存/订单类**不进缓存**（重复问每次走实时）；知识库 publish 后相关缓存失效。
2. 预算熔断：把租户日预算调到极小→超限后 LLM 增强全部降级模板、主链路仍正常出答案；`llm_budget_exceeded_total` 计数。
3. 分级路由：配 fast/smart→二判/槽位走 fast、RAG 生成走 smart（日志/trace 可见 model）；未配 fast 时回落 smart 零回归。
4. 缓存/预算后端故障→全部 fail-open 走正常链路；无 API Key 时全模块自动失效。
5. 全链路 ruff/mypy/pytest 绿。

---

## 附录：实现记录（2026-07-04）

### A. 已实现清单

| 项 | 实现 | 说明 |
|---|---|---|
| 分级路由 | `factory._model_for_purpose(purpose)`：classify→`CHAT_MODEL_FAST`，generate→`CHAT_MODEL_SMART`，留空逐级回落 `CHAT_MODEL` | `get_chat_model` 建模型时取 tier；RAG 生成（`answerer._generate`，自带 temp/prompt）改用 `CHAT_MODEL_SMART or CHAT_MODEL`；未配 fast 零回归 |
| 预算熔断 | `app/chat/llm/budget.py`：租户 contextvar（入口 `set_current_tenant`）+ Redis 按 UTC 日 `INCRBY` 累计 + `is_over_budget` 判定 | 达日预算 → `chat_completion` 与 `_generate` 双收口在调用前 return None（与无 Key 同降级路径），主链路照常出模板答案 |
| usage 收口 | `account_llm_result(purpose, result)`：从 `usage_metadata`/`response_metadata.token_usage` 取 token → 计指标 + 累计预算 | `chat_completion` 成功后与 RAG 生成后各调一次，best-effort |
| 语义缓存 | `app/chat/cache/semantic_cache.py`：`SemanticCacheProvider` 协议 + `RedisSemanticCache`（每租户封顶列表 LPUSH/LTRIM + Python cosine） | 复用 `embedding_client`（hash 可验证，真实 embedding 换即用）；命中 ≥ `SEMANTIC_CACHE_THRESHOLD` 直答 |
| 缓存白名单 | `is_cacheable_source`：仅 `faq/rag_llm/rag_extract/chitchat` 可缓存；`product/tool/action/refused` 禁 | **红线**：价格/库存/订单等事实永不缓存 |
| 缓存接入 | `rag_answer` 节点：进入先 `lookup` 命中短路，产出可缓存来源答案后 `store` | v1 只接 rag_answer（FAQ/RAG，最高价值）；chitchat 已在白名单预留、暂未挂节点 |
| 缓存失效 | 知识库 `_publish`（Stage 16）后按租户 `invalidate`，避免答旧内容；TTL 兜底 | — |
| 指标 | `llm_tokens_total{tenant,purpose}`、`llm_budget_exceeded_total{tenant}`、`semantic_cache_total{outcome=hit/miss/store}` | — |
| 配置 | `CHAT_MODEL_FAST/SMART`、`LLM_BUDGET_ENABLED`、`LLM_BUDGET_DAILY_TOKENS`、`SEMANTIC_CACHE_ENABLED/THRESHOLD/TTL/MAX_PER_TENANT` | 三块可独立开关，**均默认关闭 = 零回归** |

### B. 验证记录

- **零回归**：全量 **241 passed**（基准 219 + stage17 新增 22 例），ruff/mypy 干净。
- **fail-open**：预算/缓存后端故障 → `is_over_budget`/`lookup` 均放行走正常链路（有单测）；无 API Key 时全模块随 `llm_available()`/`OPENAI_API_KEY` 判空自动失效。
- **租户隔离**：缓存 key `semcache:{tenant}`、预算 key `llm:budget:{tenant}:{日期}`，均有隔离单测。

### C. 遗留

- 缓存相似度用 hash 伪向量只能验证机制（同/异问命中判定）；**真实语义泛化（同义不同字）需切真实 embedding + 标定 `SEMANTIC_CACHE_THRESHOLD`**。
- 预算日/月阈值、fast/smart tier 的具体划分标定待真实用量（当前只提供机制与开关）。
- 缓存暂只接 `rag_answer`；闲聊（`response_generate`）节点接入、月度预算窗口、接近阈值告警看板待后续。
- v1 语义缓存 lookup 为 O(N) 全列表扫描（N≤`MAX_PER_TENANT`）；高流量可换向量库/RediSearch 后端（协议已抽象，换实现即可）。
