# Stage 21：智能澄清（意图不明轮次的针对性澄清问句）

## 1. 阶段目标

意图不明（META.UNKNOWN + FALLBACK）轮次当前回复固定澄清模板，浪费了两份已有信息：
SetFit top_k 候选（系统其实知道用户「大概率在问退款或退货」）与近期对话上下文
（用户刚提过的商品/订单）。本阶段把固定模板升级为 **LLM 生成的针对性澄清问句**
（「您是想查退款进度，还是想申请退货？」），让用户一次点选就回到确定性轨道，
降低 UNKNOWN 连击转人工率。

定位（post-stage-20 review 三层演进的第一层）：**单次 LLM 调用，不是 agent 循环**。
含糊输入的正解是问一个好问题，不是让模型对含糊输入做多步探索。

---

## 2. 本阶段要做什么

```text
1. 新增 app/chat/skills/llm_clarifier.py：generate_clarify_question()
   —— 输入用户消息 + intent top_k + 近期对话（memory），输出一句澄清问句。
2. 接入两个 UNKNOWN 澄清出口：
   a) response_generate：status=FALLBACK 且 final_intent=META.UNKNOWN；
   b) rag_answer 拒答回落路径（R2 兜底检索不到时），同条件。
3. 触发条件收窄：top_k 中存在 ≥1 个业务候选（过滤 META.*/CHITCHAT.*）
   且置信分 ≥ 下限，才值得生成针对性问句；否则维持固定模板。
4. 生成失败/无 Key/输出不合格 → 降级固定模板（零回归）。
5. 决策留痕：graph_trace 记 "response_generate:clarify" / "rag_answer:clarify"，
   供 quality_daily 统计澄清成功率（澄清轮次后的下一轮是否脱离 UNKNOWN）。
6. 配置开关 CLARIFY_LLM_ENABLED（默认 true，无 Key 自动失效）。
```

---

## 3. 本阶段不做什么

```text
1. 不做 agent 循环 / 多步工具调查（第二层「只读诊断 agent」另立阶段）。
2. 不动 unknown_streak 转人工安全网：澄清轮 status 仍为 FALLBACK、
   intent 仍为 META.UNKNOWN，连续 2 轮照常建单——智能澄清是体验增强，
   不是兜底替代。
3. 不做澄清选项的按钮化/卡片化（渠道层能力，等前端/多渠道阶段）。
4. 不改意图分类器本身；top_k 只读消费。
```

---

## 4. 技术要求

```text
1. LLM 调用统一走 factory.chat_completion(purpose="classify")：
   自动获得分级路由（fast tier）、租户预算熔断、轮级时间预算、指标、Langfuse。
2. 用户消息经 wrap_user_input() 防注入包裹（Stage 14 收口纪律）。
3. 生成输出必须过 guardrail_engine.check_output()；违规回退模板。
4. 输出治理：取首行、长度上限（80 字符）、空输出回退模板。
5. 候选意图的可读描述从 app/chat/intent/catalog.py 的 INTENT_DESCRIPTIONS
   程序化生成，禁止手抄意图清单进 prompt（既有约束）。
6. 提示词遵循 Stage 19 约定：「回复语言与用户消息一致」，不翻译提示词。
7. 全链路无 Key 可运行：降级路径行为与 Stage 20 基线完全一致。
```

---

## 5. 目录和文件要求

```text
app/
  chat/
    skills/
      llm_clarifier.py        # 新增：澄清问句生成（单一收口）
    graph/nodes/
      response_generate.py    # 接入出口 a
      rag_answer.py           # 接入出口 b
  core/config.py              # CLARIFY_LLM_ENABLED
tests/
  stage21/
    test_smart_clarification.py
```

---

## 6. 具体实现要求

### 6.1 generate_clarify_question()

```text
签名：async def generate_clarify_question(
          user_text: str, top_k: list[dict], memory: dict | None, locale: str | None
      ) -> str | None

流程：
1. 开关/llm_available 检查，不满足返回 None；
2. 从 top_k 过滤业务候选：label 不以 META./CHITCHAT. 开头，
   score ≥ CLARIFY_MIN_CANDIDATE_SCORE（0.15，宽松下限——低置信正是本场景），
   取前 2 个；为空返回 None；
3. prompt：候选意图描述（INTENT_DESCRIPTIONS 投影）+ 近期对话（最多 4 轮）
   + wrap_user_input(用户消息)；要求输出一句友好澄清问句，
   给出最多两个方向选项，保留具体商品/单号原文，只输出问句本身；
4. 输出治理：首行 / ≤80 字符 / 非空；check_output 违规返回 None；
5. 返回问句字符串。
```

### 6.2 接入点行为

```text
response_generate（在 task_gave_up 判定之后、render_reply 之前）：
  status == FALLBACK and final_intent == META.UNKNOWN
  → q = await generate_clarify_question(...)
  → q 非空：直接返回 {reply: q, graph_trace: ["response_generate:clarify"]}
    （不再过 polish——问句本身已是 LLM 输出，二次润色浪费且可能改坏选项）
  → q 为空：走原模板路径，行为不变。

rag_answer（拒答回落 render_reply 之前，仅 final_intent == META.UNKNOWN）：
  同上；answer_source 维持 "refused"（count_rag 指标口径不变），
  trace_dict 增加 "clarify": true。
```

### 6.3 配置

```text
CLARIFY_LLM_ENABLED: bool = True     # 无 Key 自动失效
CLARIFY_MIN_CANDIDATE_SCORE: float = 0.15
```

---

## 7. 测试与验收

```text
1. UNKNOWN + 业务候选 + fake LLM → 回复为生成问句，graph_trace 带 :clarify；
2. 无 Key → 回复为原固定模板（零回归）；
3. top_k 全为 META/CHITCHAT 或低于分数下限 → 模板；
4. 输出护栏违规（fake 违规输出）→ 模板；
5. prompt 含候选描述与近期对话、用户消息经防注入包裹；
6. 澄清轮 unknown_streak 照常累计（连续 2 轮 UNKNOWN 仍建单）；
7. 全量回归零失败（不含已知环境项）。
```

---

## 附录：实现记录（2026-07-27）

- `app/chat/skills/llm_clarifier.py`：`generate_clarify_question()` 单一收口。
  候选过滤 `_business_candidates`（排除 META./CHITCHAT.、分数 ≥ 0.15、取前 2）；
  候选描述从 `catalog.INTENT_DESCRIPTIONS` 程序化生成；近期对话最多 4 轮；
  用户消息 `wrap_user_input` 包裹；输出治理（首行/≤80 字/非空）+ `check_output` 护栏。
- 接入出口：`response_generate`（FALLBACK+UNKNOWN，直接返回不过 polish）、
  `rag_answer` 拒答回落（answer_source 维持 refused，trace 标 clarify）。
  graph_trace 记 `response_generate:clarify` / `rag_answer:clarify`。
- 配置：`CLARIFY_LLM_ENABLED=true`、`CLARIFY_MIN_CANDIDATE_SCORE=0.15`。
- 附带修复：`llm_clarifier`/`query_rewrite` 对纯空白 LLM 输出的
  `splitlines()[0]` 越界防御（真实 chat_completion 不会返回纯空白，防御性）。
- 测试：`tests/stage21/` 9 例（候选过滤/无候选零调用/无 Key 零回归/
  上下文进 prompt/输出治理/护栏回退/两出口行为）。全量 309 passed。
- 验收项 6（unknown_streak 照常累计）：澄清路径不改 status/intent，
  save_turn 计数逻辑零改动，由既有 stage07 连击测试覆盖。
- 遗留：澄清成功率统计 SQL（澄清轮次后下一轮脱离 UNKNOWN 的比例）待
  quality_daily 加口径；真实 LLM 下的问句质量需人工评估。
