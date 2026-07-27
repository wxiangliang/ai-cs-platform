# Stage 23：对话方向纠偏（走错方向的识别、逃生与度量）

## 1. 阶段目标

意图误判开错流程后，用户当前缺乏轻量纠偏通道（post-stage-20 review 审计确认的
三个盲区）：

1. **任务中途否定被吞**：COLLECTING 下「不对，我不是要退货」分类为 UNKNOWN →
   `_advance_task` 把同一个槽位问题再问一遍；用户只能说「算了」（清空一切）、
   硬说出新意图、或熬满 3 轮追问上限；
2. **误开任务复活**：被打断挂起的任务在新任务结束后自动恢复续问——若它本就是
   误判开出的，错误方向二次伤害；
3. **中置信盲区**：读意图 0.40-0.60 置信直接采纳开答/开任务，无任何确认引导，
   是错方向的主产地（低置信已有 Stage 21 澄清，高置信基本可靠）。

本阶段给三个盲区各补一条确定性防线，并建立错向度量口径。

---

## 2. 本阶段要做什么

```text
1. 任务中途否定识别：规则控制层新增 COLLECTING 状态的任务否定判定
   （「不是要/不是想/搞错了/理解错」等模式 + 裸「不对/不是」短句；
   含数字不判——那是槽位纠正不是方向否定），产出 META.DENY
   （decision_source=RULE_TASK_DENY）；
2. 状态机 META.DENY 扩展：COLLECTING 下否定 → 仅终止当前任务
   （status=ABORTED，栈照常恢复，不像 META.ABORT 清空一切），
   ResolveResult 新增 denied_task 供回复渲染；
3. 否定后重定向话术：response_generate 对 denied_task 轮次回复
   「好的，先不办理{name}了。您想咨询或办理什么？」（i18n）；
4. 零进度挂起任务恢复降调：save_turn 续办提示对 collected_slots 为空的
   恢复任务改用「还没开始办理…如不需要直接回复『不用了』」话术
   （配合第 1 条，否定即可干净退出）；
5. 中置信软确认：新开任务（NEEDS_SLOT、无 task_id、非恢复）且
   SETFIT/LLM 来源置信 < INTENT_SOFT_CONFIRM_THRESHOLD(0.60) →
   追问话术前加「您是想{name}对吗？如果不是，直接说您的需求即可。」；
6. 度量：direction_correction_total{kind=task_denied|soft_confirm} 指标 +
   docs/ops/quality_queries.md 新增第 9 组错向监控 SQL
   （任务否定率/中置信采纳占比/ABORTED 率）。
```

---

## 3. 本阶段不做什么

```text
1. 不加新意图码（复用 META.DENY，taxonomy 不动——DENY 语义本就是
   「否定当前进行的事」，本阶段只是把适用状态从 CONFIRMING 扩到 COLLECTING）；
2. 不改分类器模型与阈值；不动确认门（CONFIRMING 的 DENY 行为不变）；
3. 不做 quality_daily 物化视图结构变更（口径先以可执行 SQL 落文档，
   视图扩列等真实流量后一并做）；
4. 软确认不阻塞流程（不是「先回答是不是再继续」的硬确认门——只是
   追问话术带上意图复述，用户扫一眼即可纠偏，零额外轮次）。
```

---

## 4. 技术要求

```text
1. 否定判定是确定性规则（上下文敏感，仅 COLLECTING），与确认门应答
   （仅 CONFIRMING）同款纪律：状态收窄 + 长度护栏（≤12 字）+ 含数字排除；
2. 全部改动零 LLM 依赖（纠偏通道必须在无 Key 时也完整可用）；
3. 话术走 i18n（zh/en 双语言包）；
4. 被否定任务的 chat_task 行照常标 ABORTED（save_turn 既有同步逻辑覆盖）；
5. 决策日志可区分：RULE_TASK_DENY 来源 + denied_task 落 graph 状态。
```

---

## 5. 目录和文件要求

```text
app/
  chat/
    intent/rule_classifier.py   # COLLECTING 否定判定 + RULE_TASK_DENY
    intent/types.py             # DecisionSource.RULE_TASK_DENY
    state/manager.py            # META.DENY 的 COLLECTING 分支
    state/types.py              # ResolveResult.denied_task
    graph/state.py              # GraphState.denied_task
    graph/nodes/dialog_state_resolve.py  # denied_task 透传
    graph/nodes/response_generate.py     # 重定向话术 + 软确认前缀
    graph/nodes/save_turn.py             # 零进度恢复降调话术
  locales/zh.py / en.py         # task.denied_redirect / resume.suspended_optional / intent.soft_confirm
  core/config.py                # INTENT_SOFT_CONFIRM_THRESHOLD
  core/metrics.py               # direction_correction_total
docs/ops/quality_queries.md     # 第 9 组：错向监控
tests/stage23/test_direction_correction.py
```

---

## 6. 测试与验收

```text
1. COLLECTING 下「不是要退货」「不对」→ META.DENY(RULE_TASK_DENY)；
   含数字（「不对，A12345678」）不触发；IDLE 状态不触发；CONFIRMING 行为不变；
2. 状态机：COLLECTING 否定仅终止当前任务、栈照常恢复、denied_task 回传；
3. 否定轮回复为重定向话术；有挂起任务时重定向 + 续办提示共存；
4. 零进度恢复任务的续办提示为降调话术；有进度的维持原话术；
5. 软确认：SETFIT 0.45 新开任务追问带意图复述前缀；
   置信 ≥0.60 / RULE 来源 / 恢复任务不加前缀；
6. 无 Key 全链路可用（纠偏是纯规则通道）；全量回归零失败（不含已知环境项）。
```

---

## 附录：实现记录（2026-07-27）

- 否定判定（`rule_classifier._is_task_deny`，仅 COLLECTING）：模式命中后做
  **残差判定**（与纯放弃同纪律）——否定表达之后残余 ≤3 字才判否定，
  「不是要退货，我想查物流」放行语义层拆新意图；含数字不判（槽位纠正）；
  裸「不对/不是」限极短句。decision_source=RULE_TASK_DENY。
- 状态机（`manager.py`）：META.DENY 增 COLLECTING 分支——仅终止当前任务
  （status=ABORTED、chat_task 行由 save_turn 既有逻辑标 ABORTED）、
  栈照常恢复（不像 META.ABORT 清空一切）、`ResolveResult.denied_task` 回传。
- 回复（`response_generate`）：denied_task → `task.denied_redirect` 重定向话术
  （graph_trace `response_generate:task_denied`）；中置信软确认——
  NEEDS_SLOT + 新开任务（无 task_id 且非恢复）+ SETFIT/LLM 来源 +
  置信 < 0.60 → 追问前加 `intent.soft_confirm` 意图复述前缀。
- 恢复降调（`save_turn._resume_note`）：零进度（collected_slots 空）挂起任务
  改用 `resume.suspended_optional`（"还没有开始办理…如不需要回复
  「不是要办这个」"——退出话术与否定通道闭环）。
- 度量：`direction_correction_total{kind=task_denied|soft_confirm}` +
  `quality_queries.md` 第 9 组错向监控 SQL（否定率/中置信采纳占比/日报）。
- 全链路零 LLM 依赖（纠偏通道无 Key 完整可用）。
- 测试：`tests/stage23/` 11 例；全量 332 passed。
- 遗留：quality_daily 视图扩错向列（等真实流量一并做）；
  en 语言包三个新键（骨架语言包按需补）。
