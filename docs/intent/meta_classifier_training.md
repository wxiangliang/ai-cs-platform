# Meta-classifier 训练操作文档

> 设计与选型依据见 `docs/requirements/stage-27-meta-classifier/01_meta_classifier.md`；
> 本文档回答三件事：**为什么训练、作用是什么、怎么训练**。
> 意图码单一事实来源仍是 `docs/chat/intent_taxonomy.md`。

---

## 1. 为什么训练、作用是什么

聊天链路里有两类完全不同的「分类」：

| | 意图分类器（SetFit） | **Meta-classifier（本文档）** |
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

# 人工审核改标 target_decision 后重训
uv run python scripts/train_meta_classifier.py --data data/export/meta_train_t1_<date>.csv
```

导出口径：split 按 session md5 分桶（80/10/10，同会话同桶=组安全）；
`target_decision` 默认是链路实际决策（弱标签），影子分歧行
（shadow_agree=False）自动加权 1.5 并应**优先人工审核**——模型与
Stage 26 阈值不一致的样本信息量最大；message 列已脱敏、仅供审核。
契约防漂移：tests/stage27 断言导出特征列 == 训练白名单。

维护约定：特征白名单/黑名单以 `scripts/train_meta_classifier.py` 顶部
常量为准（单一事实来源），改动需同步 tests/stage27 契约测试与本文档第 2 节。

## 6. 影子模式（已接入，只观察不决策）

训练产物在位时，线上每轮**语义层**决策（SETFIT/LLM 二判等来源；控制层
短路轮次不在部署域）会附带一次影子预测：

```text
配置：META_SHADOW_ENABLED=true（默认）/ META_SHADOW_MODEL=champion|lr|lgbm|xgb
     / META_SHADOW_DIR=models/meta_classifier_v1
落库：decision_log.graph_trace_json.meta_shadow
     = {decision: 影子预测, actual: 链路实际决策(近似口径), agree, model}
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
