# Stage 26：意图决策加固（补槽守护 / margin 判据 / 任务态切换门槛）

## 1. 阶段目标

对「会话决策引擎」方案的评审结论（2026-07-30 讨论定稿）：**现有
「规则控制层 → SetFit → LLM 二判 → 状态机」的分层短路架构不需要重做**——
该方案约六成内容（消息作用识别、任务关系判断、UNKNOWN 独立层、LLM 候选裁决、
多意图拆任务、决策证据落库）已由 Stage 03/05/10/21/23 实现。真正的缺口是
**任务进行态下的优先级与置信度判定不够严谨**，共四处（均已在代码中核实）：

1. **补槽轮次缺「回答当前问题」优先假设（实际 bug）**：COLLECTING 下
   `_is_slot_only` 只认裸值/「槽位名+空格+值」，「订单号**是**12345678」
   「**是**12345678」不算纯槽位 → 掉进 SetFit 全表分类；若被高置信判成
   `ORDER.QUERY_STATUS`，状态机规则 8 挂起退款任务、误开订单查询。
   系统没有先验证「当前输入能否满足 pending slot」就允许业务意图接管。
2. **margin 信息浪费**：SetFit 已返回 top_k（top3+概率），混合分类器只看
   top1 绝对阈值。top1=0.78/top2=0.76 与 top1=0.78/top2=0.12 决策可信度
   完全不同，却走同一路径。
3. **UNKNOWN 续接过宽（`manager.py` 规则「UNKNOWN+有任务→并入续接」）**：
   等订单号时用户问「你们支持开发票吗」，若被判 UNKNOWN 则被吞掉、
   原话重问（ask_count+1）；且 UNKNOWN 轮规则抽槽照跑，
   「我有12345678个问题」的数字串可能污染 order_id。
4. **Stage 23 软确认两处 quirk**：(a) 写意图采纳线 0.60 == 软确认线 0.60，
   写意图**永远不触发软确认**——恰是最该复述确认的一类；(b) LLM 二判固定
   置信 0.7 ≥ 0.60，条件中 `DecisionSource.LLM` 分支为**死代码**。

同时明确**不采纳**方案中的：domain→intent→action 三层 taxonomy 重构、
手工加权融合公式、意图转移概率矩阵、独立 dialogue_act 节点/统一决策大 JSON、
条件任务编排（理由见第 3 节）。

---

## 2. 本阶段要做什么

按实施顺序四期，每期独立可验收：

```text
P1 补槽守护（pending-slot-aware extraction）——修实际 bug
   新增 app/chat/slots/pending_fill.py::try_fill_pending_slot()：
   已知当前任务只缺某槽位时做定向提取+校验，不做通用意图判断；
   接入混合分类器：控制语义之后、SetFit 之前（顺序红线见 4.1）；
   命中 → META.SLOT_ONLY（decision_source=RULE_PENDING_SLOT）走状态机
   规则 2 原样续接，证据落 intent_result.pending_fill。

P2 margin 判据——只改路由，不改标签
   hybrid_classifier 采纳判定升级为二维（top1 分数 × top1-top2 margin）：
   高分+大 margin → 直接采纳；高分+小 margin → LLM 二判（不可用时
   采纳 top1 但打 SETFIT_LOW_MARGIN 来源，由软确认接住）；低分 → 现行
   二判/UNKNOWN 路径不变。禁止因 margin 小自动改选 top2。
   margin 显式落 intent_result 供 SQL 分析。

P3 任务进行态切换门槛 + UNKNOWN 续接收紧
   状态机规则 8 前加切换守护：COLLECTING/CONFIRMING 下新业务意图
   须「更高置信+margin 达标」或「显式切换信号」才允许挂起切换，
   否则保留当前任务、回复二选一澄清（不填槽、不切换、计一次追问）；
   UNKNOWN+有任务续接收紧为「有续接证据才并入」，且只并入 pending
   槽位（防污染）；无证据 → 保留任务 + 二选一澄清。

P4 Stage 23 quirk 修复 + 专项回归评估集
   写意图软确认单列阈值（默认 0.75）；LLM 二判来源新开任务一律软确认
   （不看固定置信）；tests/stage26/ 链路行为回归集（用例见 5.2）。
```

---

## 3. 本阶段不做什么（评审明确否决项）

```text
1. 不做 domain→intent→action 三层 taxonomy 重构：29 类 test acc 0.94，
   平铺标签相似性问题在上百类时才痛；重构要动 taxonomy/训练集/技能注册表，
   风险远大于收益。但注意 test acc 不代表线上效果（近重复泄漏/会话跨集/
   OOS 未参与/多轮短回复未参与），补的是 P4 链路评估集而不是重构分类体系；
2. 不做手工加权融合（setfit×0.35+example×0.25+…）：无真实流量无法学权重，
   手工权重不可解释不可测试；现有分层短路每层可独立测试、决策可回放。
   未来有大量标注流量再考虑 meta-classifier；
3. 不做意图转移概率矩阵：无线上转移统计，先用「状态相关阈值+pending slot
   优先+显式切换信号」的最小可控版占位；
4. 不加独立 dialogue_act 分类模型/节点、不做统一决策大 JSON：classify_control
   / ResolveResult / decision_log 已承担该职责，做行为增强不做术语重构；
5. 不做条件任务编排（「没发货就退款」跨任务条件执行）：真实需求出现再说；
6. 不动确认门语义、不动 taxonomy 意图码、零 migration、零 API 协议变更；
7. 阈值本阶段给保守默认+可配，最终标定待真实流量（同 Stage 25 遗留 B3 纪律）。
```

---

## 4. 技术方案

### 4.1 P1 补槽守护 `try_fill_pending_slot`

**判定顺序红线**（评审确认，不得调换）：

```text
① 显式控制语义（确认门应答/任务中途否定/纯放弃/取消订单/转人工——现有控制层）
② pending-slot 定向提取（本期新增，仅 COLLECTING 且 active_task 有 missing 槽位时）
③ SetFit / LLM 二判 / 兜底（现行）
```

「订单号先不找了，帮我查下运费」必须先被 ① 的放弃/否定语义接走，
不能被 ② 误填。②失败不产生任何副作用，原样进入 ③。

**接口**（放 `app/chat/slots/pending_fill.py`，复用 `slots/patterns.py`）：

```python
def try_fill_pending_slot(
    text: str,
    pending_slot: str,            # 当前任务第一个缺失槽位
    collected: dict[str, Any],    # 已收集槽位（冲突检测用）
) -> PendingFillResult | None:
    # 命中返回 {slot, value, evidence}，未命中返回 None
```

**证据分级与采纳策略**（评审定稿）：

| evidence | 形态示例 | 采纳策略 |
|---|---|---|
| `explicit_slot_name` | 「订单号是12345678」「单号：12345678」 | 直接采纳（槽位名与 pending 槽位一致才算） |
| `pure_value` | 「12345678」 | 严格 fullmatch 才采纳（现行 `_is_slot_only` 语义并入） |
| `contextual_answer` | 「是12345678」「应该是12345678」「就是12345678」 | 采纳，但格式强校验（fullmatch 值主体） |
| `llm_extracted` | — | **本层不用 LLM**。LLM 槽位兜底保持在 slot_extract 原位置，且其结果不得作为高风险任务的续接依据 |

**防误填规则**（每条都要有反例测试）：

1. **类型冲突检测**：等 `order_id` 时遇到 `1[3-9]\d{9}` 的 11 位串 →
   判 phone 不判 order_id，不填、放行 ③（可能是 CORRECT_INFO 或新信息）；
2. **显式槽位名冲突**：消息说「手机号是…」而 pending 是 order_id → 不填；
3. **语境排除**：值后接量词/单位（「12345678**个**问题」）、前带金额符号 → 不填；
4. **含显式切换信号**（4.3 词表）且信号后有非槽位实质内容 → 不填，放行 ③。

**接入点**：`HybridIntentClassifier.classify` 与 `RuleIntentClassifier.classify`
增加 `pending_slot` / `collected_slots` 可选参数（协议向后兼容，默认 None）；
`intent_classify` 节点从 `state.active_task` 取值传入；`detect_multi_intent`
透传（拆段后仅主段做 pending fill）。命中返回：

```python
IntentResult(pred_label=META_SLOT_ONLY, confidence=0.9,
             decision_source=DecisionSource.RULE_PENDING_SLOT,
             pending_fill={"slot": ..., "value": ..., "evidence": ...})
```

状态机零改动（规则 2 现成续接）；`slot_extract` 对该来源直接采信
pending_fill 的键值（跳过全量抽取，防同轮其他数字串污染）。

### 4.2 P2 margin 判据

`hybrid_classifier.classify` SetFit 采纳段改为：

```python
margin = top1 - top2   # top_k 已有，len<2 时视为 margin=1.0

if conf >= threshold and margin >= settings.INTENT_MIN_MARGIN:
    采纳 SetFit（现行路径）
elif conf >= threshold:                      # 高分但模糊
    LLM 二判；不可用/失败 → 采纳 top1，
    decision_source=SETFIT_LOW_MARGIN（软确认接住，见 4.4）
else:                                        # 低分（现行路径不变）
    LLM 二判 → UNKNOWN
```

三条纪律（评审定稿）：**margin 只影响“是否二判”，绝不自动改选 top2**；
SetFit 概率未校准，`INTENT_MIN_MARGIN`（默认 0.10）与各阈值都是待标定值，
上线后按 decision_log（score/margin/state/预测 vs 真实）统计
覆盖率×准确率×二判比例后调整；混淆对级 margin 配置（REFUND.APPLY vs
QUERY_PROGRESS 类）本期不做，全局单值起步。
`intent_result` 新增 `margin` 字段随决策日志落库。

### 4.3 P3 切换守护与 UNKNOWN 收紧

**切换守护**（`DialogStateManager.resolve` 规则 8 前）——核心原则：
任务进行中，新意图成立不仅要「像新意图」，还要证明「不是当前问题的回答」：

```python
if active_task and current_state in (COLLECTING, CONFIRMING) \
        and new_intent != active_task.intent:
    if explicit_switch_signal(text) :          # 显式信号 → 普通阈值放行
        挂起切换（现行规则 8）
    elif conf >= switch_threshold(state) and margin >= INTENT_MIN_MARGIN:
        挂起切换（现行规则 8）
    else:
        保留当前任务，status=NEEDS_SLOT，ask_count+1，
        ResolveResult.switch_candidate = new_intent   # 供二选一澄清话术
```

- 阈值：`INTENT_SWITCH_THRESHOLD_COLLECTING=0.78`、
  `INTENT_SWITCH_THRESHOLD_CONFIRMING=0.85`（IDLE 不设守护，走现行采纳线）；
- 显式切换信号词表（另外/还有/顺便/对了/先不说这个/换个问题/先帮我/再帮我…）
  **不单独决定切换**：「另外一个订单号是12345678」含「另外」但能填 pending
  slot——P1 在分类前已把它接走，到达守护的轮次天然不含可填槽位值；
  信号词表放 `state/manager.py` 常量（与 taxonomy 无关，不进意图码）；
- 二选一澄清话术（i18n key `intent.switch_clarify`）：
  「我还在处理{当前任务}，您是要补充{缺失槽位}，还是想{新意图}？」；
- `direction_correction_total{kind=switch_guard}` 计数。

**UNKNOWN 续接收紧**（`manager.resolve` UNKNOWN 分支）：

```python
if intent == META_UNKNOWN and active_task:
    if slots.get(missing_slot):        # 续接证据：本轮抽到了 pending 槽位
        只并入 {missing_slot: value}   # 不整包 merge，防污染
        → _advance_task
    else:                              # 无证据：不填槽、不切换
        保留任务，status=NEEDS_SLOT，ask_count+1，
        unknown_with_task=True         # 澄清话术改二选一，联动 Stage 21
```

**续接证据排除严格校验型槽位**（实施中发现的补充规则）：order_id/phone
的合法应答必然已在分类层被补槽守护/纯槽位判定接走，能走到 UNKNOWN 的
同型数字串是通用正则误抽（「我有12345678个问题」→ ORDER_ID_BARE_RE
照样命中），一律不算证据；color 等无严格校验的槽位（补槽守护接不走）
仍可作为证据并入。严格槽位清单 = `pending_fill.STRICT_VALIDATED_SLOTS`
（与定向提取校验表同源，单一事实来源）。

CONFIRMING 下 UNKNOWN 行为微调：重发确认话术不变，但本轮误抽值不再
整包 merge（防已确认的 order_id 被「B999999999」类误抽覆盖后再确认）。

### 4.4 P4 软确认 quirk 修复

`response_generate` 软确认条件改为：

```python
需软确认 = 新开任务(NEEDS_SLOT、无 task_id、非恢复) and (
    decision_source == LLM                              # 难例二判来源一律复述
    or decision_source == SETFIT_LOW_MARGIN             # P2 新来源
    or (decision_source == SETFIT and conf < soft_threshold(intent))
)
# soft_threshold：写意图 INTENT_SOFT_CONFIRM_THRESHOLD_WRITE=0.75，
# 读意图沿用 INTENT_SOFT_CONFIRM_THRESHOLD=0.60
```

修复效果：写意图 0.60-0.75 区间新开任务有复述纠偏（此前死区）；
LLM 来源死条件消除（评审风险一定论：原「<0.60」描述准确但 LLM 分支
因固定 0.7 永不触发，属实现 quirk 非文档错误）。

### 4.5 配置新增（全部有默认值，`.env.example` 同步）

```text
INTENT_MIN_MARGIN=0.10
INTENT_SWITCH_THRESHOLD_COLLECTING=0.78
INTENT_SWITCH_THRESHOLD_CONFIRMING=0.85
INTENT_SOFT_CONFIRM_THRESHOLD_WRITE=0.75
```

均标注「待真实流量标定」；decision_log 已含标定所需字段
（score/margin/decision_source/state/graph_trace），无需新表。

---

## 5. 验收标准

### 5.1 行为验收

1. 退款任务等 order_id 时发「订单号是12345678」→ 续接退款不误开订单查询，
   decision_source=RULE_PENDING_SLOT；
2. 同状态发「手机号是13800138000」→ 不填 order_id，进正常分类链路；
3. 同状态发「订单号先不找了，帮我查下运费」→ 控制语义先行，
   任务终止/切换正常（不被 P1 误填）；
4. 同状态发「你们支持开发票吗」（模拟 UNKNOWN）→ 不填槽不吞问，
   回复二选一澄清，任务保留；
5. 同状态发高置信+大 margin 的真实新意图（如物流查询）→ 仍能正常挂起切换
   （守护不是禁切换）。注：投诉/闲聊等 META/CHITCHAT 类意图本就不进
   切换分支（直接回复不动任务，Stage 03 现行语义），不受守护影响；
6. 写意图 0.60-0.75 置信新开任务 → 追问话术带软确认前缀；
7. 全量既有测试零回归（默认配置下 IDLE 单任务路径行为不变）。

### 5.2 专项回归集（tests/stage26/，monkeypatch 分类结果驱动链路，不依赖模型）

```text
补槽不误切：12345678 / 订单号 12345678 / 订单号是12345678 / 是12345678 /
           应该是12345678 / 我的订单号为12345678
防误填反例：手机号是13800138000 / 金额是12345678 / 我有12345678个问题 /
           先不退款了 / 帮我查物流
margin 路由：高分大margin直采 / 高分小margin进二判 / 二判不可用打LOW_MARGIN
切换守护：低置信新意图不切+二选一澄清 / 高置信+margin达标可切 /
         显式信号词普通阈值可切 / CONFIRMING 更高门槛
UNKNOWN 收紧：有证据只并入pending槽位 / 无证据不填槽不切换 /
             数字串不污染 order_id
既有语义回归：CONFIRMING 是/不是 / 任务中途否定(Stage 23) / 纯放弃 /
             多意图拆任务 / 挂起任务恢复 / 追问超限放弃
```

---

## 6. 遗留（明确记录，不在本阶段做）

1. 全部阈值（采纳线/margin/切换线/软确认线）真实流量标定，含按
   dialog state 分层统计口径（SQL 进 quality_queries 待流量后补）；
2. 混淆意图对级 min_margin 配置；
3. examples-only 向量 TopK 交叉验证（第三信号源）；
4. 更贴线上的分类评估集（会话级拆分防泄漏、OOS 参与、多轮短回复参与）；
5. meta-classifier / 意图转移统计（需标注流量）。
