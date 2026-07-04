# Stage 18 需求：A/B 实验框架

> 前置：Stage 09 数据积累（decision_log 已可回放对比、quality_daily 已有指标口径）。
> 分流与变体注入是**确定性逻辑，现在可做**；显著性**结论**需真实流量积累（框架不等它）。
> 来源：roadmap 3.6 backlog「A/B 实验」。

---

## 1. 阶段目标

让「改提示词/调阈值/换召回策略」从拍脑袋变成可分流、可度量、可对比的实验：
同一改动在一部分流量上跑变体，用既有指标对比效果，有数据再全量。

## 2. 本阶段要做什么

### 2.1 实验配置与分桶

- `experiment` 配置（表或配置文件）：实验 id、状态（draft/running/stopped）、
  变体列表（control + N 个 variant）、分流比例、作用域（tenant/意图域）、
  可实验的参数（提示词版本、`RAG_MIN_SCORE`/`FAQ_HIT_THRESHOLD` 等阈值、
  `RERANKER_PROVIDER`、召回权重等**已存在的可配项**——实验只覆盖它们，不引新逻辑分支）；
- 分桶：`hash(experiment_id + tenant + session_id) % 100` 落变体——**确定性**（同会话稳定同变体，
  不会中途换体）、无状态、多进程一致；
- 分桶结果落 `decision_log`（新增 `experiment_json`：{exp_id, variant}），供事后按变体切分对比。

### 2.2 变体参数注入

- 变体只覆盖**已有配置项**（settings 的白名单子集）——运行时按分桶结果用变体值覆盖该轮的
  有效配置（不改代码路径，只改参数），把「实验能改什么」限死在安全集合内；
- 收口一个 `resolve_experiment(tenant, session)` → 生效变体 + 参数覆盖，在图入口注入 GraphState；
- 降级：实验配置缺失/解析失败 → control（默认参数），不影响主链路。

### 2.3 指标对比

- 对比口径复用现有资产：按 `experiment_json.variant` 切分 quality_daily 的
  一次解决率/转人工率/拒答率/CSAT/P95 等（`docs/ops/experiment_queries.md`）；
- 决策回放（replay_trace）已能按 trace 还原，实验期可对同类 query 在不同变体下的回答做人工对拍；
- **显著性检验**：给出样本量与置信度的判断口径（SQL/脚本），但「够不够显著」由真实流量决定——
  框架只负责把数据按变体切好、算好比率与样本量,不替运营下结论。

## 3. 本阶段不做什么

- 多臂老虎机/自动调参（只做固定比例 A/B，人工看数据决定）；
- 实验管理前端（配置用文件/表 + CLI，界面另做）；
- 引入实验专属的新代码分支（变体只能调已有可配项，防实验代码腐化主链路）。

## 4. 目录和文件要求

```text
app/experiments/config.py + resolver.py  # 实验配置加载 + 确定性分桶 + 参数解析
app/chat/graph/state.py                  # GraphState 加 experiment（变体+参数覆盖）
app/chat/graph/builder.py 或入口          # 图入口注入变体、覆盖该轮有效配置
app/chat/logging/decision_logger.py      # 落 experiment_json
docs/ops/experiment_queries.md           # 按变体切分的对比 SQL + 样本量口径
tests/stage18/
```

## 5. 验证方式

1. 配一个改 `RAG_MIN_SCORE` 的实验、50/50 分流→同一 session 稳定落同变体、不同 session 分布接近比例；
   decision_log 落 experiment_json。
2. 变体参数只在实验作用域生效、只覆盖白名单配置项；实验 stopped/缺失→回落 control 默认参数。
3. 按变体切分 quality_daily→control vs variant 的解决率/拒答率/CSAT 分别出数，附样本量。
4. 分桶多进程一致（同输入同变体）；实验配置损坏→control 兜底不打断主链路。
5. 全链路 ruff/mypy/pytest 绿；无实验运行时主链路零回归。

---

## 附录：实现记录（2026-07-04）

### A. 已实现清单

| 项 | 实现 | 说明 |
|---|---|---|
| 配置加载 | `app/experiments/config.py`：JSON 文件（`EXPERIMENTS_CONFIG_PATH`），按 mtime 缓存；`Experiment`/`Variant` 数据类 | 缺失/损坏/非法变体一律 fail-open 返回 `[]`；改文件自动重载 |
| 白名单 | `OVERRIDABLE_PARAMS = {RAG_MIN_SCORE, FAQ_HIT_THRESHOLD, RAG_RECALL_TOP_K, RERANKER_PROVIDER}` | 变体 `params` 在**配置解析层**就过滤非白名单键（含 AUTH/KEY 等危险项）并告警丢弃——实验只能调参数，绝不引新代码分支 |
| 确定性分桶 | `resolver.bucket_of = int(md5(f"{exp}:{tenant}:{session}"),16) % 100` | 不用内置 `hash()`（PYTHONHASHSEED 随机化）→ **多进程一致**、同会话稳定同变体、无状态 |
| 变体选择 | `pick_variant`：累计权重归一化到 100 映射 bucket | 支持 control + N variant、任意权重 |
| 参数注入 | `set_overrides` 写 contextvar → 白名单消费点经 `effective(name)` 读（实验覆盖优先，否则回落 settings） | 改动 4 个消费点（`kb/retriever.py`×3、`kb/answerer.py`、`kb/rerank.py`）；不改代码路径只改参数 |
| 作用域 | `scope.tenants`（空=全部）在 resolver 强制；`scope.intents` 已入模型（record-only，v1 不在入口强制——入口先于意图分类） | 租户作用域已测；意图域为记录位，遗留 |
| 落库 | `chat_decision_log.experiment_json`（JSONB，migration `a7c2f1e9d3b4`）：`{assignments:[{exp_id,variant}], overrides}` | 图入口 `resolve_experiment().to_log()` 写 state → decision_logger 落库；无命中为 None |
| 入口注入 | `chat_service.process_message`：`resolve_experiment` → `set_overrides` + `initial_state["experiment"]` | 与 Stage 17 租户 contextvar 同一入口收口 |
| 对比 SQL | `docs/ops/experiment_queries.md`：按变体切 done/handoff/refused/P95/CSAT + 两比例 z 近似 + 回放对拍 | 口径与 quality_daily 一致；**显著性结论留给运营**，框架只切数据算比率 |
| CLI | `scripts/experiments.py list|bucket|sample` | 上线前核对分流（不连库、纯配置/分桶检查） |

### B. 验证记录

- **零回归**：全量 **261 passed**（基准 241 + stage18 新增 20 例），ruff/mypy 干净；migration 应用后 `alembic check` 无 drift。
- **确定性**：分桶锚定 md5 已知值单测（防哈希实现被无意改动破坏跨进程一致）；同会话多次解析稳定同变体；1000 会话 50/50 抽样落 [400,600]。
- **安全**：非白名单参数（`AUTH_ENABLED`/`OPENAI_API_KEY`）在解析层被丢弃有单测；stopped/draft/作用域外→control；缺文件/坏 JSON→control fail-open。
- **注入**：`effective()` 覆盖优先、清空回落 settings 有单测；CLI 实测分桶/抽样一致。

### C. 遗留

- **意图域作用域**：入口分桶先于意图分类，`scope.intents` 目前仅记录、未在入口强制（可后置到消费点按最终意图门控，或接受「变体全轮分配、参数只影响检索路」的现状）。
- **显著性**：只提供样本量与两比例 z 近似口径,真实显著性结论需真实流量积累——**框架不替运营下结论**。
- 实验配置改 DB 表 + 管理前端（v1 用 JSON 文件 + CLI）；多臂老虎机/自动调参不做（防实验代码腐化主链路）。
