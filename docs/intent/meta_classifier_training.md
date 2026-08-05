# Meta-classifier 训练操作文档

> 设计与选型依据见 `docs/requirements/stage-27-meta-classifier/01_meta_classifier.md`；
> 本文档回答四件事：**为什么训练、作用是什么、怎么训练、怎么审核改标**。
> 只想快速上手：直接看第 7 节操作手册。
> 意图码单一事实来源仍是 `docs/chat/intent_taxonomy.md`。

---

## 1. 为什么训练、作用是什么

聊天链路里有两类完全不同的「分类」：

| | 意图分类器（SetFit，训练见 `setfit_training.md`） | **Meta-classifier（本文档）** |
|---|---|---|
| 回答的问题 | 这句话是什么业务意图？ | **对当前任务该做什么操作？** |
| 输入 | 消息文本 | 表格特征：对话状态 + 任务上下文 + 槽位信号 + SetFit TopK/margin |
| 输出 | 29 个意图码 | 6 个决策：续接 / 切换 / 接受新意图 / 送 LLM 二判 / 澄清 / 未知 |
| 现状 | 已上线（Stage 04） | 该决策目前由 **Stage 26 手调阈值**承担（切换守护/margin 路由） |

训练 Meta-classifier 的目的：把「补槽中来了个 0.72 置信的新意图该不该切」
这类**多信号组合决策**从手写 if/else + 拍脑袋阈值，升级为从数据学出来的
决策边界。规则系统不被替代：`classify_control`（确认门/转人工/取消/补槽
守护）仍在模型**之前**硬短路，状态机+确认门在模型**之后**兜底——L3 写
操作永远不因模型高置信而跳过确认。

**当前版本（v1）是离线管线**：数据是合成的（SetFit 分数模拟），离线冠军
不能上线；v1 的交付物是可复跑的训练/评估管线 + 特征契约。上线路径
（影子模式→阈值接管）见 stage-27 文档 4.5 节。

## 2. 数据文件

`data/电商客服_MetaClassifier_合成训练数据_v1.csv`（12501 行，UTF-8 BOM）。

| 字段组 | 字段 | 说明 |
|---|---|---|
| 元数据 | row_id / split / case_family_id / scenario_family / feature_source / notes | split 自带 train(9739)/validation(1419)/test(1343)，**case_family 零跨集**（组安全，已由测试锁定）；16 个场景族 |
| 消息 | message | 仅供人读。**禁止作特征**：去重后仅 1273 条、单条最高重复 200 次（同一话术扰动特征生成多行），任何文本派生特征都会背标签 |
| 对话上下文 | current_state / has_active_task / active_intent / active_domain / pending_slot / suspended_task_count | 对应运行时 dialog_state / active_task / task_stack |
| 控制层 | control_result | **域过滤字段不是特征**：非 NONE 的行（3499）运行时被规则短路，到不了 Meta 层 |
| 槽位信号 | slot_match / slot_match_type / slot_value_type | 对应 Stage 26 pending_fill 产物 |
| 切换信号 | explicit_switch_signal / has_business_object | 对应 SWITCH_SIGNAL_RE；has_business_object 运行时暂无实现（遗留） |
| SetFit | setfit_top1/2_label / top1/2_score / margin / low_conf / ambiguous | **模拟值**——这是 v1 不能上线的根本原因 |
| 标签 | target_decision（训练目标）/ target_intent / target_domain / target_risk_level / target_priority | 后四列 v1 不用 |
| 权重 | sample_weight | 难例 1.5，只作训练权重 |
| 泄漏 | llm_review_expected | **黑名单**：实测与 SEND_TO_LLM 标签 100% 同向，是标签改写不是特征 |

## 3. 怎么训练

```bash
# 一次性：安装训练依赖组（lightgbm/xgboost/pandas，生产镜像不装）
uv sync --group train

# 训练 + 评估（默认部署域 6 类、四模型对比，CPU 数分钟内）
uv run python scripts/train_meta_classifier.py

# 常用参数
uv run python scripts/train_meta_classifier.py \
  --data data/电商客服_MetaClassifier_合成训练数据_v1.csv \
  --out models/meta_classifier_v1 \
  --scope deploy \        # deploy=control_result==NONE 6类（默认）；full=11类诊断
  --models lr,tree,lgbm,xgb   # 未装 lightgbm/xgboost 时自动跳过并提示
```

产物（`models/meta_classifier_v1/`，不进 git）：

```text
model_lr.joblib / model_tree.joblib / model_lgbm.joblib / model_xgb.joblib
feature_spec.json   # 特征白名单+类型+类别取值表——运行时推理必须按它构造输入
metrics.json        # 四模型对比：accuracy/macro-F1/逐类F1/业务代价指标/冠军
tree_rules.txt      # 浅决策树文本规则（规则发现用，不上线）
```

## 4. 指标怎么读（不只看 Accuracy）

| 指标 | 含义 | 期望 |
|---|---|---|
| false_switch_rate | 该续接却切走（吞任务上下文，**代价最高 ×5**） | 越低越好 |
| false_continue_rate | 该切换却吞入当前任务（×3） | 越低越好 |
| llm_miss_rate | 难例没送二判（×2） | 越低越好 |
| unnecessary_llm_rate | 不必要的 LLM 调用（×1，成本项） | 权衡 |
| clarification_rate | 预测澄清占比 | 过高=体验差，观察项 |
| cost_weighted_error | 上述权重加权的总代价（**冠军依据**） | 越低越好 |
| macro_f1 | 防大类掩盖小类（UNKNOWN/澄清各仅 420 条） | 平手裁决 |

读法纪律：
1. **LightGBM/XGBoost 赢不了 Logistic Regression ⇒ 别急着换模型**，
   说明特征或数据量不够，先补特征；
2. 浅决策树的前几个分裂应落在 slot_match / margin / current_state /
   top1_score；若用了 suspended_task_count 之类的边缘字段打头，怀疑数据泄漏；
3. 合成数据上的任何名次**不构成上线依据**（SetFit 分数是模拟的），
   只验证管线正确性与特征方向。

## 5. 重训与真实化流程

```text
换数据/加场景族 → 覆盖 CSV（保持字段契约与组安全 split）
              → tests/stage27 数据契约测试先跑（6 类/零跨集断言）
              → 重跑训练脚本 → 对比 metrics.json
```

**真实数据回流（上线后训练数据的存与取）**：

线上每个语义层轮次，影子模式把**完整特征向量 + 链路实际决策（弱标签）**
落进 `decision_log.graph_trace_json.meta_shadow.features/actual`
——**不依赖模型产物是否在位**（特征采集与预测独立降级），所以从上线
第一天起训练数据就在积累。导出为训练契约 CSV：

```bash
# 导出近 30 天真实特征表（与合成集同契约，可直接喂训练脚本）
uv run python scripts/export_meta_training_set.py --tenant t1 --days 30

# 接管评估用时间切分（整会话粒度：旧 80% 会话训练、新 20% 验证/测试——
# 更接近真实上线效果，能暴露意图分布/表达漂移；接管门禁 2 的口径）
uv run python scripts/export_meta_training_set.py --tenant t1 --days 90 --split-by time

# 人工审核（填 reviewed_decision 列）后重训
uv run python scripts/train_meta_classifier.py --data data/export/meta_train_t1_<date>.csv
```

导出口径：split 默认按 session md5 分桶（80/10/10，同会话同桶=组安全），
`--split-by time` 为整会话时间切分；message 列已脱敏、仅供审核。
契约防漂移：tests/stage27 断言导出特征列 == 训练白名单。

**三列标签**（防「模型只学会复制现有规则」——把当时做了什么、应该做什么、
后来发生了什么分开存）：

| 列 | 含义 | 谁写 |
|---|---|---|
| `policy_decision` | 链路当时实际做了什么（行为日志，**不可改**——审核后仍保留，供「策略 vs 人工」对比与审核覆盖率统计） | 导出脚本 |
| `reviewed_decision` | 人工认为应该做什么（**审核只填这一列**；认为链路判对了也把原值填进来=显式确认，与未审核行区分开） | 人工审核 |
| `target_decision` | 训练标签列（与合成集契约兼容），导出时初始=policy；训练脚本自动取 reviewed > target，reviewed 行 ×2 加权 | 自动 |
| `hindsight_signal` / `hindsight_tier` | 后来发生了什么（outcome，只排审核优先级，**永不当真值**） | 导出脚本 |

**审核优先级**（按 `hindsight_tier` 分级——后见信号是「该轮决策事后被
证伪的概率」排序器，不是判决书：转人工可能是工具失败/回复质量差/用户
坚持转人工，**不等于该轮意图决策错了**）：

| 优先级 | 筛选条件 | 含义 |
|---|---|---|
| 1 | `hindsight_tier=strong` | task_deny：该轮之后同会话出现任务中途否定=用户明确纠错，之前开的任务大概率错 |
| 2 | `hindsight_tier=medium` | low_csat≤2 / feedback_down：会话结局差的间接证据 |
| 3 | `shadow_agree=False` | 影子模型与链路阈值分歧，信息量最大（自动加权 1.5） |
| 4 | `hindsight_tier=weak` | 仅 handoff（转人工）：归因高度不确定 |
| 5 | 其余 | 抽检 |

注意：特征快照（decision_log 记录）是不可改的审计事实——哪怕状态
本身是上游误判「错出来的」，它也是模型推理时会真实面对的输入分布；
**人工修正的对象只有 reviewed_decision 一列**（在导出 CSV 里填）。
未经审核的行不要直接进训练——数据量够后用
`train_meta_classifier.py --reviewed-only` 硬执行该纪律。

维护约定：特征白名单/黑名单以 `scripts/train_meta_classifier.py` 顶部
常量为准（单一事实来源），改动需同步 tests/stage27 契约测试与本文档第 2 节。

## 6. 影子模式（已接入，只观察不决策）

训练产物在位时，线上每轮**语义层**决策（SETFIT/LLM 二判等来源；控制层
短路轮次不在部署域）会附带一次影子预测：

```text
配置：META_SHADOW_ENABLED=true（默认）/ META_SHADOW_MODEL=champion|lr|lgbm|xgb
     / META_SHADOW_DIR=models/meta_classifier_v1
落库：decision_log.graph_trace_json.meta_shadow
     = {features: 特征向量, actual: 链路实际决策(近似口径),
        reason_codes: 证据派生原因码(low_margin/knn_agrees_top1/
        differs_from_active_task 等——审核免翻 JSON,可 SQL 聚合),
        decision/agree/model: 模型产物在位时附加}
指标：meta_shadow_total{decision, agree} —— 分歧率 = agree=false 占比
降级：产物缺失（models/ 不进镜像）/依赖缺失/异常 → 静默跳过，零行为影响
```

分歧率速查 SQL（decision_log）：

```sql
SELECT graph_trace_json->'meta_shadow'->>'decision' AS shadow,
       graph_trace_json->'meta_shadow'->>'actual'   AS actual,
       count(*) AS n
FROM chat_decision_log
WHERE graph_trace_json ? 'meta_shadow'
GROUP BY 1, 2 ORDER BY n DESC;
```

注意：当前挂载的是**合成数据模型**，分歧率数字只反映「合成决策边界 vs
Stage 26 手调阈值」的差异，用于验证管线与积累对照样本；模型接管决策
必须等真实特征表重训 + 分歧分析达标（stage-27 文档 4.5 阶段 B）。

## 7. 快速上手操作手册（上线后按周期执行）

完整闭环一图流：

```text
① 导出          ② 审核标注         ③ 重训            ④ 生效观察
export_meta_ →  填 CSV 的      →  train_meta_    →  重启服务（影子懒加载）
training_set    reviewed_decision  classifier         → 看分歧率指标/SQL
（建议 2-4 周一次；早期数据少可放宽到量够 2000+ 行再训）
```

### 7.1 第一步：导出

```bash
uv run python scripts/export_meta_training_set.py --tenant t1 --days 30
# 输出示例：导出 3210 行 → data/export/meta_train_t1_20261015.csv（split=session）
# 审核优先级：hindsight strong 23 行 > medium 64 行 > 影子分歧 214 行 > weak 41 行 > 其余抽检
```

### 7.2 第二步：审核标注（核心步骤，不可省）

用 Excel/WPS/Numbers 打开导出的 CSV（UTF-8 编码；WPS 若乱码选
「数据→导入文本→UTF-8」）。**只允许填 `reviewed_decision` 一列**，
其他列一律不动——`policy_decision`/`target_decision` 是链路行为日志，
保留原值才能事后统计「人工推翻了多少策略决策」；`sample_weight` 保持默认。

审核顺序：先筛 `hindsight_tier=strong` 的行，再 medium、再
`shadow_agree=False`，其余抽检（第 5 节优先级表）。每行审核只回答一个问题——

> 看 `message`（用户当时说了什么）+ `current_state`/`active_intent`
> （系统当时在办什么）：这一轮**正确的决策**应该是下面 6 个里的哪个？

| 决策码 | 什么意思 | 典型例子（假设正在办退款、等订单号） |
|---|---|---|
| CONTINUE_CURRENT | 用户在回应当前任务，别打断 | 「稍等我找一下单号」「刚才那个订单」 |
| SWITCH_NEW | 用户真的换了件事办，挂起当前切过去 | 「先别退了，帮我改下收货地址」 |
| ACCEPT_NEW_INTENT | 没有进行中任务（IDLE），接受新诉求开任务 | （空闲时）「我要退款」 |
| SEND_TO_LLM | 信号矛盾/太模糊，该送 LLM 二判 | 「那个东西怎么弄」（分不清指什么） |
| ASK_CLARIFICATION | 在业务范围内但指向不明，该反问 | 「退款和换货有什么区别来着」 |
| UNKNOWN | 压根不在业务范围（闲聊外/无关问题） | 「你们招不招人」 |

三个典型标注场景：

```text
例 1（hindsight_tier=strong，policy=SWITCH_NEW）：补槽中用户问「什么
  时候能到」，链路切去物流查询，两轮后用户说「不是要查物流」。
  → reviewed_decision 填 SEND_TO_LLM 或 ASK_CLARIFICATION
    （当时就该多问一句，而不是直接切）。

例 2（shadow_agree=False, policy=CONTINUE_CURRENT）：切换守护把
  「帮我开张发票」拦在了退款任务里追问订单号——message 明显是新诉求。
  → reviewed_decision 填 SWITCH_NEW（守护拦错了，正是要教给模型的样本）。

例 3：看完 message 和上下文，认为链路当时判得没错。
  → reviewed_decision 照抄 policy_decision 的值。**维持原判也要填**
    ——显式确认与未审核是两回事（确认行会 ×2 加权、计入审核覆盖率）。
```

改完**另存为 CSV（保持 UTF-8）**，注意别让 Excel 把布尔列的
True/False 改成 TRUE/FALSE 或 1/0（只填 reviewed_decision 一列就不会碰到）。

### 7.3 第三步：重训与验收

```bash
uv run python scripts/train_meta_classifier.py --data data/export/meta_train_t1_20261015.csv
# 审核量攒够后建议只用人工标签训练（硬执行「未审核不进训练」纪律）：
uv run python scripts/train_meta_classifier.py --data ... --reviewed-only
```

标签自动按优先级取：`reviewed_decision`（人工，×2 加权）>
`target_decision`（链路弱标签）。开头会打印两者行数——**reviewed 行占比
就是「模型能否超越现有阈值」的上限**：0 行审核直接训 = 把 Stage 26
手调规则克隆成 LightGBM，没有意义。

看输出对比表（指标含义见第 4 节），验收四条：
1. `cost_weighted_error` 和 `false_switch_rate` 是核心，别只看 accuracy；
2. LightGBM/XGBoost 应赢过 LR——赢不了说明数据还不够，继续攒；
3. 浅决策树前几个分裂是否符合业务直觉（`tree_rules.txt`）；
4. 接管评估前额外用 `--split-by time` 重导出跑一遍（旧训新测）——
   指标比 session 切分明显差说明存在分布漂移，session 切分的数字虚高。

### 7.4 第四步：生效与观察

产物写在 `models/meta_classifier_v1/`，影子模式**进程启动时懒加载**
——重启服务后新模型自动生效（仍只观察不决策）。观察一到两周：

```bash
# 分歧率走势：Prometheus 查 meta_shadow_total{agree="false"} 占比
# 明细：第 6 节的分歧率速查 SQL
```

分歧率持续走低且分歧样本人工复核多数「模型对、阈值错」时，
才进入接管评估——按 stage-27 文档 4.5 阶段 B 的**十条接管门禁**逐项过
（审核标签量/时间切分评估/SWITCH precision/代价对比/分层无退化/校准/
回滚演练等），全过后按**分级接管顺序**灰度：先 SEND_TO_LLM（错了只多花
一次 LLM）、再澄清/UNKNOWN、再 ACCEPT_NEW、最后才是直接改写任务上下文的
SWITCH/CONTINUE。接管需另行开发+开关控制，默认关。
