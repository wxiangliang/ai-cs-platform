# Stage 20 需求：记忆摘要 v2（结构化交接摘要 + 上下文纪律固化）

> 背景：对照业界长链路 Agent 的上下文工程实践（四层防线 L1-L4 / 单一表示原则 /
> 结构化交接文档）做了一次差距评审，结论：
> 本系统是「每轮一次 8 节点线性图 + 至多一次小工具调用」的客服形态，**不存在**
> 长链路 Agent 的单次执行内上下文爆炸问题——L1（大结果外置 refId）、L2（单条结果
> 语义压缩）、L4（DataBus 按需取回）解决的膨胀模式在本链路不发生，**不建**。
> Stage 10 的「摘要 + 近期窗口」已是 L3（对话压缩）同款思路，且所有注入点有硬性
> 字符上界（窗口 8 条×200 字、摘要 ≤500 字、事实 ≤5×200 字），单轮 prompt token
> 上界为常数。本阶段只补三个真实缺口：**摘要结构化防丢关键事实**、
> **单一表示原则从「实现巧合」固化为「测试保护」**、**大工具结果红线写进对接约定**。
> 核心红线沿用：记忆是增强层，LLM 故障/无 Key 一律降级不打断主链路。

---

## 1. 阶段目标

长会话（超过摘要阈值后）不丢关键业务事实：把 `memory_summary` 从 150 字自由叙述
升级为固定 schema 的结构化摘要，保证订单号/商品/未决事项等具体值在多轮压缩后仍然
在场；同时把「同一条消息不得以摘要+原文两种形态进同一 prompt」的单一表示约束
用回归测试锁死，防止后续调参打破。

## 2. 本阶段要做什么

### 2.1 结构化会话摘要（核心）

- `memory_summary` 由自由文本改为固定字段 JSON（存储仍在
  `chat_session.metadata_json.memory_summary`，值为 JSON 字符串或 dict）：

  ```json
  {
    "request": "用户核心诉求（一句话）",
    "entities": ["ORD20260701123", "白色空气净化器 AP-300"],
    "progress": "处理进展（已查到物流卡在中转仓 / 已提交取消申请等）",
    "pending": ["等用户提供收货地址"],
    "answered": ["运费险规则已答复"]
  }
  ```

  - `entities`：**具体值清单**（订单号/单号/商品名/金额），摘要 prompt 中加硬约束
    「保留具体单号、金额、商品名原文，禁止概括泛化（如『某订单』『相关商品』）」——
    这是本阶段最重要的一条：LLM 天然倾向抽象摘要，而后续轮次需要的是具体 ID；
  - `answered`：已答复事项，供润色层避免重复啰嗦、也防再问同题时口径漂移；
  - 增量续写语义不变：既有摘要（JSON）+ 对话增量 → 更新后的 JSON，
    `memory_summary_covered` 游标机制照旧。
- 注入格式：`get_context` 返回前把 JSON 渲染为紧凑中文行（如
  `诉求：…；涉及：ORD…、…；进展：…；待办：…`），下游（润色/RAG）**零改动**——
  `session_summary` 对外仍是一个字符串；
- 解析降级：LLM 输出不是合法 JSON → 按旧版自由文本原样存储（标记
  `summary_format: "text"`），不重试不报错；读取兼容两种格式（存量会话是纯文本）；
- 字段与总长上界：JSON 序列化后仍受 500 字上限约束，`entities`/`pending`/`answered`
  各限 5 条，超出丢最旧——上界是编译时约束，不靠 LLM 自觉。

### 2.2 单一表示原则固化（测试为主，代码为辅）

- 现状：摘要只覆盖近期窗口之前的消息（`cut = total - MEMORY_SHORT_TERM_TURNS`），
  摘要与窗口零重叠——但这是实现巧合，无测试保护；
- 新增回归测试：构造超阈值会话，断言 `get_context` 返回的
  `session_summary` 覆盖区间与 `recent_turns` 无交集（用 `memory_summary_covered`
  游标与窗口起点比对）；调 `MEMORY_SHORT_TERM_TURNS`/`MEMORY_SUMMARY_THRESHOLD`
  任意组合均成立；
- `local_provider._maybe_summarize` 处加中文注释，显式声明该不变式
  （「摘要覆盖区间与短期窗口互斥——同一消息禁止以两种形态进同一 prompt」）。

### 2.3 大工具结果对接红线（doc-only）

- 在 `docs/requirements/stage-11-mcp-integration/` 文档追加一条对接约定：
  真实业务系统的工具若返回大结构（如订单列表、物流全程明细），MCP 服务端
  必须返回**面向回复的结论字段**（如 `summary` + 关键字段），原始大 JSON
  不得进入 `tool_facts` / 润色 prompt；需要完整数据时落库存引用，
  按需另查（对应业界 L1「大结果外置」模式）。本阶段不实现代码，只立约定。

## 3. 本阶段不做什么

```text
1. 不建 L1/L2/L4 三层（refId 外置存储 / 单条结果语义压缩 / DataBus）——
   本链路无此膨胀模式，等真实工具接入且实测出现大结果再议；
2. 不做 prompt 预算事前估算——现有字符硬上界已是更强的编译时预算；
3. 不改 mem0 provider（结构化摘要仅 local provider；mem0 托管自身摘要策略）；
4. 不做摘要的向量化检索/跨会话摘要合并；
5. 不加新表、不加 migration（存储沿用 chat_session.metadata_json）。
```

## 4. 技术要求

```text
1. 下游接口零改动：MemoryContext.session_summary 对外仍是 str，
   prompts.py / answerer.py 不感知 JSON 结构；
2. 全部改动向后兼容：存量纯文本摘要可读、无 Key 时摘要停用短期窗口照常（现状不变）；
3. 摘要 JSON 解析失败一律降级纯文本存储，禁止抛出打断 remember()（best-effort 语义不变）；
4. 摘要更新写入前照旧过输出护栏（Stage 14 check_output）。
```

## 5. 目录和文件要求

```text
app/chat/memory/local_provider.py     # 摘要 prompt 改结构化 schema + JSON 解析/降级 + 渲染注入
app/chat/memory/base.py               # （如需）MemoryContext 注释更新，接口不变
tests/stage20/                        # 结构化摘要解析/降级/上界 + 单一表示不变式测试
docs/requirements/stage-11-mcp-integration/  # 追加大结果对接红线一节
docs/README.md / CLAUDE.md            # 阶段进度与文档索引更新
```

## 6. 具体实现要求

### 6.1 摘要 prompt（示意）

system 要点：客服对话摘要器；输出**只有一个 JSON 对象**（给出 schema 与字段说明）；
「既有摘要 JSON + 对话增量 → 更新后 JSON」；硬约束：具体单号/金额/商品名原文保留，
禁止概括泛化；各数组字段 ≤5 条；总长 ≤500 字。用户增量文本照旧经 150 字/条截断，
经 `wrap_user_input` 防注入包裹（现状已有的护栏不回退）。

### 6.2 渲染函数

`_render_summary(summary: dict | str) -> str`：dict → 紧凑单行中文；str → 原样返回。
空字段跳过。放 local_provider 内部，不进公共接口。

## 7. 代码质量要求

```text
1. 核心方法、不变式必须有中文注释（尤其单一表示不变式）。
2. 不要超范围实现（不建 L1/L2/L4，不动 mem0，不加表）。
3. 记忆链路保持 best-effort：任何异常只记日志，不打断主链路。
```

## 8. 验证方式

```bash
uv run pytest tests/stage20 tests/stage10   # 新增用例 + Stage 10 记忆用例零回归
uv run pytest                               # 全量零回归
uv run ruff check app && uv run mypy app
```

```text
1. 超阈值会话：摘要为合法 JSON，含 entities 具体单号原文（mock LLM 返回验证存取链路）；
2. LLM 返回非 JSON → 降级纯文本存储，remember 不抛错；存量纯文本摘要注入正常；
3. 单一表示不变式：多组窗口/阈值参数下摘要覆盖区间与 recent_turns 无交集；
4. entities/pending/answered 超 5 条被截断，序列化超 500 字被拒写（保留旧摘要）；
5. 无 API Key：摘要停用、短期窗口照常（现状零回归）。
```

---

## 9. Codex 执行提示词

```text
请先阅读根目录 AGENTS.md，
再阅读本文件。

本次只实现 Stage 20：记忆摘要 v2。
严格按文档实现，不要超范围实现（明确不建 L1/L2/L4 上下文防线、不动 mem0、不加 migration）。
完成后说明新增文件、修改文件、启动方式、验证方式，并在本文件追加实现记录附录。
```

---

## 附录：实现记录（2026-07-21）

### A. 已实现清单

| 项 | 实现 | 说明 |
|---|---|---|
| 结构化摘要 | `local_provider.py`：`_SUMMARY_SYSTEM` schema prompt + `_parse_summary`（JSON 提取）+ `_normalize_summary`（只留声明字段、数组各限 5 条丢最旧） | 存 `metadata_json.memory_summary`（dict）+ `summary_format="json"`；「具体单号/金额/商品名保留原文，禁止概括泛化」为 prompt 硬约束 |
| 渲染注入 | `_render_summary(dict|str) -> str`：dict → 紧凑单行中文（诉求/涉及/进展/待办/已答复），str 原样 | `MemoryContext.session_summary` 对外仍是 str，润色/RAG 侧**零改动**；存量纯文本摘要（Stage 10 旧格式）兼容可读 |
| 解析降级 | LLM 输出非法 JSON / 归一化后为空 → 按纯文本存储（截 500 字，`summary_format="text"`），不重试不抛错 | best-effort 语义不变；无 Key 时摘要停用、短期窗口照常（现状零回归） |
| 总长上界 | 归一化后序列化仍超 500 字 → **拒写保留旧摘要**，covered 游标不推进（下轮重试） | 上界是编译时约束，不靠 LLM 自觉 |
| 单一表示不变式 | `_maybe_summarize` 注释显式声明「摘要覆盖区间 [0, cut) 与短期窗口互斥」；`tests/stage20` 参数化回归测试（3 组窗口/阈值组合 + 增量续写不重复纳入已覆盖消息） | 从「实现巧合」固化为「测试保护」 |
| 防注入补齐 | 摘要的对话增量经 `wrap_user_input` 包裹（原实现未包，本次按 Stage 14 收口原则补上）；`_extract_facts` 的局部 import 提升为模块级 | 写入前输出护栏（check_output）照旧 |
| MCP 大结果红线 | `stage-11-mcp-integration/01_mcp_tool_service.md` 新增第 5 节对接约定（doc-only） | 服务端返回结论字段，原始大 JSON 不进 tool_facts/润色 prompt；完整数据落库存引用 |

### B. 验证记录

- `tests/stage20/test_structured_summary.py` 10 例全过 + `tests/stage10` 18 例零回归（真实 PG）。
- 全量 pytest：**273 passed**；2 个失败（`test_rag_eval_gate`、stage16 keyword 检索）为 **Milvus 未启动的环境问题**，
  在无本次改动的基线（git stash）上同样失败，非回归。
- `ruff check app tests` / `mypy app`（168 files）干净；`alembic/` 有 1 处**存量** F401（初始提交带入，CI 只查 app/tests/scripts，未动）。

### C. 遗留

- 结构化摘要质量（字段填充准确性、entities 召回）依赖真实 LLM，标定待联调；fake LLM 只验证存取/降级/上界链路。
- `answered` 字段目前只注入 prompt 供润色参考，未做「重复问题直答」联动（可与语义缓存协同，后续再议）。
- mem0 provider 不感知结构化 schema（托管自身摘要策略，按文档明确不动）。
