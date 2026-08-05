# Stage 27：Meta-classifier 会话决策器（v1 离线训练管线）

## 1. 阶段目标

Stage 26 把任务进行态的决策（续接/切换/二判/澄清/未知）落成了**手调阈值**
（`INTENT_MIN_MARGIN` / `INTENT_SWITCH_THRESHOLD_*`），并明确记录「全部阈值
待真实流量标定」。本阶段引入 **Meta-classifier**：用表格特征模型学习这个决策，
替代手调阈值的长期演进方向——**学的是「对当前任务做什么操作」，不是「消息
是什么意图」**（意图仍由 SetFit/LLM 二判负责，两者输入输出完全不同）。

选型评审结论（2026-07-30，采纳外部方案并结合本系统修正）：

| 方案 | 结论 | 本系统定位 |
|---|---|---|
| 规则系统 | **必须保留，模型前后硬约束** | `classify_control` 短路在前、状态机+确认门兜底在后，Meta 永远不能替代 |
| Logistic Regression | 采纳 | 可解释基线：若树模型赢不了它，说明数据/特征不够 |
| LightGBM | 采纳（主力候选） | 表格特征+类别混合+非线性交互的正解 |
| XGBoost | 采纳（对照） | 同数据同拆分同指标对比 |
| 浅决策树（depth≤4） | 采纳（**规则发现工具，不上线**） | 看首批分裂特征是否符合业务直觉、反查数据泄漏 |
| 小型 MLP | **不做** | 合成数据下大容量模型只会背生成规律；真实 decision_log 数万条+人工修正标签后再议 |
| 学习排序（LTR） | **不做** | 解决的是候选意图重排，不是任务操作决策；未来可作为 SetFit TopK 与 Meta 之间的一层 |

## 2. 本阶段要做什么

```text
1. 训练数据入库：data/电商客服_MetaClassifier_合成训练数据_v1.csv
   （12501 行，16 场景族，自带组安全 split——case_family 零跨集）；
2. 训练管线 scripts/train_meta_classifier.py：
   四模型（lr/tree/lgbm/xgb）同拆分同指标对比、业务代价指标、
   泄漏黑名单强制、产物落 models/meta_classifier_v1/（不进 git）；
3. 操作文档 docs/intent/meta_classifier_training.md
   （为什么训练/作用/数据字段/怎么跑/指标怎么读/重训流程）；
4. 应用路径设计（本文档第 4.5 节）：影子模式 → 阈值接管，
   v1 只做离线管线，不接线上。
```

## 3. 本阶段不做什么

```text
1. 不让模型驱动生产决策：合成 SetFit 分数 ≠ 真实分布，离线冠军只能
   以**影子模式**（只预测只记录，见 4.5 阶段 A）在线运行；接管决策的
   前置条件是特征替换为真实链路输出（decision_log 回流重训）+ 分歧分析达标；
2. 不替代规则与确认门：L3 写操作（退款/退货/换货/取消/改址/发票）无论
   Meta 输出什么置信度，确认门照走（状态机结构性保证，非模型自觉）；
3. 不做 MLP / 学习排序（理由见第 1 节表格）；
4. 不为 LR 手工造组合特征（树模型自动学交互，LR 就做纯基线）；
5. 不动 Stage 26 的阈值与守护逻辑（Meta 未上线前它们就是生产决策）。
```

## 4. 技术方案

### 4.1 部署域：模型只学规则接不走的那部分

数据交叉验证结论（`control_result × target_decision` 完全正交）：

| control_result | 行数 | 标签 | 本系统归属 |
|---|---|---|---|
| RULE_CONFIRM_GATE | 1296 | CONFIRM/DENY_CURRENT | 确认门规则（Stage 03） |
| RULE_KEYWORD | 2080 | CANCEL/CORRECT/TRANSFER/部分 ACCEPT | 控制层关键词（Stage 03/23） |
| RULE_SLOT_ONLY | 123 | CONTINUE_CURRENT | 纯槽位续接（Stage 03） |
| **NONE** | **9002** | **6 类决策** | **Meta-classifier 部署域** |

**训练主域 = `control_result==NONE` 的 9002 行、6 类**：
`CONTINUE_CURRENT / SWITCH_NEW / ACCEPT_NEW_INTENT / SEND_TO_LLM /
ASK_CLARIFICATION / UNKNOWN`。规则域行不进训练（运行时到不了 Meta 层，
混入只会虚增离线指标）；`--scope full` 保留 11 类全量作诊断用途。

已知域偏差（v2 真实化时重新切）：数据中部署域的 CONTINUE_CURRENT 全部
`slot_match=True`（槽位应答式），而本系统 Stage 26 起这类消息大多被补槽
守护 `RULE_PENDING_SLOT` 在分类层接走；真实链路到达 Meta 的 CONTINUE
更多是「守护拦截型 hold」（无槽位证据但也不该切换）。

### 4.2 特征契约与泄漏黑名单

**特征白名单**（与运行时可得信号一一对应，Stage 26 后全部已在链路内）：

| 特征 | 类型 | 运行时来源 |
|---|---|---|
| current_state | 类别 | dialog_state |
| has_active_task / suspended_task_count | 布尔/数值 | active_task / task_stack |
| active_intent / active_domain / pending_slot | 类别 | active_task |
| slot_match / slot_match_type / slot_value_type | 布尔/类别 | pending_fill（Stage 26） |
| explicit_switch_signal | 布尔 | SWITCH_SIGNAL_RE（Stage 26） |
| has_business_object | 布尔 | 待实现（v1 用数据列，运行时可由槽位/商品词近似） |
| setfit_top1/2_label、top1/2_score、margin | 类别/数值 | IntentResult.top_k / margin（Stage 26） |
| setfit_low_conf / setfit_ambiguous | 布尔 | 阈值派生（score/margin 的确定函数） |

**泄漏黑名单**（训练脚本硬编码排除并断言，实证依据）：

```text
llm_review_expected —— 标签近似改写：SEND_TO_LLM 行 100% 为 True（实测）；
message             —— 文本去重后仅 1273 条、单条最高重复 200 次，
                       任何文本派生特征都会背标签；
row_id / case_family_id / scenario_family / split / notes —— 生成元数据；
target_* / sample_weight / feature_source / control_result —— 标签侧与域过滤字段。
```

`sample_weight` 只作训练权重（难例 1.5），不作特征。

### 4.3 模型与训练纪律

- 拆分：沿用数据自带 split（train 9739 / validation 1419 / test 1343，
  case_family 组安全）；validation 用于 lgbm/xgb 早停，test 只做最终评估；
- LR：OneHot + 标准化管道，class 不平衡靠 sample_weight；
- 浅决策树：max_depth=4，导出文本规则（`tree_rules.txt`）——若首批分裂
  用了业务直觉外的字段即数据异常信号；
- LightGBM/XGBoost：原生类别特征、早停、保守容量（合成数据 1273 条独立
  消息，有效信息量远小于行数，防过拟合优先）；
- 依赖隔离：lightgbm/xgboost/pandas 进 `train` 依赖组
  （`uv sync --group train`），生产镜像不带；未装时脚本自动跳过对应模型，
  仅 sklearn（setfit 既有传递依赖）可跑 lr/tree。

### 4.4 评估指标：不只看 Accuracy

业务代价指标（决策错误代价不对称，全部落 metrics.json）：

| 指标 | 定义 | 代价权重（默认） |
|---|---|---|
| false_switch_rate | 真值 CONTINUE，预测 SWITCH/ACCEPT（误切吞任务上下文） | 5 |
| false_continue_rate | 真值 SWITCH/ACCEPT，预测 CONTINUE（吞新诉求） | 3 |
| llm_miss_rate | 真值 SEND_TO_LLM 未送二判（难例硬答） | 2 |
| unnecessary_llm_rate | 非难例送了 LLM（浪费成本） | 1 |
| clarification_rate | 预测澄清占比（过高=体验差） | 观察项 |
| macro_f1 / per-class F1 | 防大类掩盖小类 | 主对比指标 |

冠军选择：test 集代价加权错误最低、macro-F1 平手裁决。
**离线冠军 ≠ 上线依据**（合成分数），结论只用于验证管线与特征契约。

### 4.5 应用路径

```text
阶段 A 影子模式 —— ✅ 基础设施已实现（2026-07-30，与 v1 管线同批）：
  app/chat/intent/meta_shadow.py：dialog_state_resolve 内对每轮语义层
  决策做预测（控制层短路轮次不在部署域，与训练域对齐）——
  只观察不决策：结果落 decision_log.graph_trace_json.meta_shadow
  （decision/actual/agree/model）+ meta_shadow_total{decision,agree} 指标；
  产物缺失/依赖缺失/任何异常一律 fail-open 静默跳过（models/ 不进镜像
  的部署形态 = 自动停用）。META_SHADOW_ENABLED=true 默认开（零行为影响）。
  当前挂的是合成数据模型：分歧率数字仅验证管线，正式影子评估
  待真实特征表重训后进行；
阶段 B 阈值接管（未实施）：
  真实重训 + 下述门禁全过后，_switch_evidence_sufficient / margin 路由的
  硬阈值替换为 Meta 概率 + 安全约束（L3 确认门、pending slot 不可跳过、
  写操作不直通——全部结构性保留）；
回滚：META_SHADOW_ENABLED=false 关影子；接管期另设开关，默认关。
```

**接管门禁清单**（2026-08-05 评审固化——「分歧率达标」不够，上线前逐项过，
全过才允许进入分级接管；任一项不过就继续影子攒数据）：

| # | 门禁 | 口径 |
|---|---|---|
| 1 | 人工审核标签量达标 | reviewed_decision 行 ≥ 2000，且高风险类（SWITCH_NEW / CONTINUE_CURRENT）各 ≥ 300 |
| 2 | 时间切分评估通过 | `export --split-by time`（旧训新测）指标不比 session 切分显著退化——防表达/分布漂移下的虚高 |
| 3 | SWITCH_NEW precision 不低于当前策略 | 对照口径 = `map_actual_decision`；误切是最高代价错误（×5） |
| 4 | cost_weighted_error 显著低于当前策略 | 同一 test 集上模型 vs 链路实际决策对比 |
| 5 | 任务完成率 / 转人工率不劣化 | quality_daily 按影子分歧会话切分对照 |
| 6 | 高风险写意图误接受率不升 | L3 域（AFTERSALE/ORDER/PAYMENT）分层单看 |
| 7 | LLM 调用量在预算内 | SEND_TO_LLM 预测占比 vs 当前 llm 二判占比 |
| 8 | 概率完成校准 | isotonic/Platt（遗留 4）——confidence 用于阈值的前提 |
| 9 | 分层无严重退化 | 按租户 × 意图域 × 对话状态分层，任一层 cost 退化 >20% 即不过 |
| 10 | 灰度开关 + 回滚演练完成 | 接管开关默认关、一键回阈值路径实测过 |

**分级接管顺序**（每级观察 1-2 周、门禁 3-7 复查通过再进下一级；
SWITCH/CONTINUE 直接改写任务上下文、错误代价最高，所以放最后）：

```text
级 0  影子（现状，只记录）
级 1  只接管 SEND_TO_LLM     —— 错了代价=多花一次 LLM（×1），最安全的试点
级 2  接管 ASK_CLARIFICATION / UNKNOWN —— 错了代价=多问一句
级 3  接管 ACCEPT_NEW_INTENT —— 仅 IDLE 态开任务，无进行中上下文可吞
级 4  接管 SWITCH_NEW / CONTINUE_CURRENT —— 最后：误切×5 / 误吞×3 的高危决策
```

实际决策对照口径（`map_actual_decision`，分歧率 SQL 引用时以代码注释为准）：
守护拦截/UNKNOWN 保持 → CONTINUE_CURRENT；LLM 二判来源 → SEND_TO_LLM；
UNKNOWN 无任务 → UNKNOWN；任务中切换成功 → SWITCH_NEW；其余新开 →
ACCEPT_NEW_INTENT。UNKNOWN 与 ASK_CLARIFICATION 的边界为近似（链路里
两者都走 Stage 21 澄清），真实化后细分。

### 4.6 阶段 B 输出契约（2026-08-05 决策融合层评审固化，接管实施时照此做）

定位再确认：Meta-classifier 是**意图决策融合层**，不是文本分类器
（message 永在泄漏黑名单；输入=规则/SetFit/KNN/任务上下文的表格证据；
输出=对当前任务的操作决策）。接管时的输出对象：

```jsonc
{
  "decision": "SWITCH_NEW",            // 6 类之一，唯一权威输出
  "final_intent": "AFTERSALE.REFUND",  // 采纳的意图码（SEND_TO_LLM/澄清/UNKNOWN 时可空）
  "confidence": 0.89,                  // 决策概率——必须先经真实数据校准（遗留 4）才可用于阈值
  "need_llm": false,                   // == decision==SEND_TO_LLM（显式化便于路由代码直读）
  "need_clarification": false,         // == decision==ASK_CLARIFICATION
  "evidence": { /* 特征快照（同 meta_shadow.features）+ example_knn/margin */ },
  "reason_codes": ["low_margin", "knn_agrees_top1", "differs_from_active_task"],
  "model": "lgbm_v2",
  "contract_version": 1
}
```

契约纪律：

1. **不进学习层的决策**（保持结构性，模型输出不含也不影响）：
   RAG/FAQ 路由（确定性矩阵 R1-R5）、任务挂起/恢复执行（SWITCH 决策的
   挂起动作由状态机结构完成，LIFO/TTL 恢复照旧）、确认门与 L3 安全约束
   （模型任何输出都不能绕过——结构保证非模型自觉）；
2. `reason_codes` 是证据的**纯派生**（`derive_reason_codes`，✅ 影子期已落库
   随 graph_trace_json.meta_shadow）——审核与分歧分析免翻 JSON，可 SQL 聚合；
3. 影子记录即契约的前身：features/actual/decision/agree/reason_codes 已按
   本形状积累，接管时只是把 decision 从「记录」升级为「生效」+补齐显式字段。

## 5. 验收标准

1. `uv run python scripts/train_meta_classifier.py` 在部署域跑通四模型对比，
   输出业务指标表 + 产物（模型/feature_spec.json/metrics.json/树规则）；
2. 泄漏黑名单断言生效（黑名单列进特征即报错）；
3. 浅决策树首批分裂特征落在业务直觉集（slot_match/margin/state/score 类）；
4. tests/stage27：特征契约（黑名单不进白名单）、业务指标计算、
   数据契约（部署域 6 类、组安全 split）回归锁定；
5. 文档三件套：本文档 + 操作文档 + docs/intent/README 索引更新。

## 6. 遗留（明确记录）

1. ~~真实化导出~~ ✅ 已实现（2026-07-30）：影子模式把**特征向量+实际决策
   （弱标签）**每轮落 `decision_log.graph_trace_json.meta_shadow`
   （不依赖模型产物——采集与预测独立降级，上线第一天起即积累训练数据）；
   `scripts/export_meta_training_set.py` 导出训练契约 CSV（session md5
   分桶组安全、影子分歧行加权 1.5 优先人工审核、导出列==训练白名单
   契约防漂移测试）。**2026-08-05 标签/评估纪律加固**：三列标签
   （policy_decision 链路事实不可改 / reviewed_decision 人工审核专用列，
   维持原判也显式填写 / target_decision 训练标签，训练侧 reviewed 优先
   ×2 加权 + `--reviewed-only` 硬执行）；hindsight_tier 证据分级
   （task_deny=强、低分/差评=中、仅转人工=弱——后见信号只排审核优先级
   永不直接当真值）；`--split-by time` 整会话时间切分（旧训新测，
   接管门禁 2 的评估口径）；
2. has_business_object 运行时特征实现（当前为近似启发式）；
3. 分歧率看板（指标 meta_shadow_total 已有，Grafana 面板待加）；
4. 概率校准（合成数据上无意义，真实数据后 isotonic/Platt）；
5. 类别新增（意图码扩表）时的特征契约版本管理；
6. 人工审核标注工具/流程（当前为 CSV 改标）；
7. **模型结构备选**（真实混淆矩阵出来后再决策，不提前复杂化）：若六分类
   在 SWITCH/CONTINUE 上精度不足，考虑两阶段拆分——模型 A 判「当前语义
   结果可否直接信任」（ACCEPT/UNCERTAIN），模型 B 判不确定时的处置
   （CONTINUE/SWITCH/LLM/CLARIFY/UNKNOWN）；类别不平衡下两阶段通常更容易
   拿到高精度的切换判断。v1 维持六分类；
8. split 维度扩展：时间切分已实现（`--split-by time`）；按用户/客户维度
   分组（同一客户跨 session 的重复话术泄漏）需 join chat_session.user_id，
   真实流量后按需加。
