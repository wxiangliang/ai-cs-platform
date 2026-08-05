# SetFit 意图分类器训练操作文档

> 设计与选型依据见 `docs/requirements/stage-04-llm-integration/02_setfit_intent_classifier.md`；
> 本文档回答四件事：**用什么数据、怎么训练、怎么验收、重训后要做什么**。
> 只想快速上手：直接看第 6 节重训闭环清单。
> 意图码单一事实来源是 `docs/chat/intent_taxonomy.md`；数据集本身的
> 来龙去脉（v41 质检/映射规则/字段说明）见本目录 `README.md`。

---

## 1. 定位：它在链路里是哪一层

三层混合分类架构（`INTENT_CLASSIFIER=hybrid`，`app/chat/intent/hybrid_classifier.py`）：

```text
规则控制层（classify_control：确认门/转人工/取消/补槽守护——硬短路，模型碰不到）
    ↓ 放行
SetFit 语义层（本文档：29 类业务意图，CPU 可跑）
    ↓ 低置信 / margin 小（难例）
LLM 二判（无 Key 自动降级）；margin 难例可先走 KNN 示例交叉验证免二判（README 第 6 节）
```

与本目录另一份训练文档的关系（两个模型，别混淆）：

| | **SetFit（本文档）** | Meta-classifier（`meta_classifier_training.md`） |
|---|---|---|
| 回答的问题 | 这句话是什么业务意图？ | 对当前任务该做什么操作（续接/切换/送二判…）？ |
| 输入 | 消息文本 | 表格特征（含 SetFit 的输出 TopK/margin） |
| 数据 | `intent_train_v42_project.csv`（真实语料） | 合成表格特征 + 影子回流 |
| 状态 | **已上线**（Stage 04，hybrid 模式） | 影子模式只观察不决策（Stage 27） |

## 2. 数据：训练用 v42，不是 v41

**常见误解澄清：训练脚本读的是 `docs/intent/intent_train_v42_project.csv`，
不是 `intent_train_v41_clean_nodup.csv`。** v41 是只读的原始数据
（19641 行 35 标签，永不手改），v42 由构建脚本从 v41 生成：

```text
intent_train_v41_clean_nodup.csv（原始，只读）
    │  scripts/build_intent_dataset.py
    │  ├─ 标签映射到 taxonomy 规范码（6 处别名，规则见 README 第 3 节）
    │  ├─ 剔除微量类/SAFETY.ABUSE/trainable=False（去向见 README 第 2 节）
    │  ├─ 程序化生成 ~600 条 FAQ.GENERAL（v41 完全缺失该意图）
    │  └─ [--extra 回流标注文件.csv ...] 合并线上回流的增量标注
    ▼
intent_train_v42_project.csv（29 类约 20100 条，训练用这份；可重复构建）
```

字段 4 列：`text / intent / split(train|val|test) / source`。
**数据变更纪律（README 第 5 节）**：不手改 CSV——改 `build_intent_dataset.py`
的映射规则或生成模板后重建；新增意图先在 taxonomy 注册、每类 ≥300 条。

## 3. 怎么训练

```bash
# 依赖：setfit/datasets 随主依赖组安装（uv sync 即可，无需额外 group）
uv run python scripts/train_setfit_intent.py

# 常用参数（默认值即 v1 上线配置）
uv run python scripts/train_setfit_intent.py \
  --base-model BAAI/bge-small-zh-v1.5 \   # 中文小模型，CPU 可跑
  --samples-per-class 16 \                # body 对比学习每类采样数
  --max-steps 400 \                       # body 微调步数上限（控制 CPU 时长）
  --batch-size 32
```

训练策略两阶段（CPU 环境权衡，stage-04-02 需求第 3 节）：

1. **body 对比学习微调**：每类只采 16 条、限 400 步——SetFit 的少样本
   对比学习特性，CPU 上数十分钟内完成；
2. **分类头全量拟合**：LogisticRegression 头用**全量 train 集** embedding
   重新拟合（SetFit 标准做法，头部拟合很快，不浪费全量数据）。

产物（`models/intent_setfit_v1/`，不进 git、不进镜像——运行时缺产物
自动降级规则层）：

```text
model_head.pkl + sentence-transformers 模型文件   # SetFitModel.save_pretrained
metrics.json    # accuracy / macro_f1 / 逐类 F1 / 易混淆组混淆矩阵 / 训练参数
labels.json     # 29 类标签表（运行时校验用）
```

## 4. 指标怎么读与验收线

| 指标 | 验收线 | 说明 |
|---|---|---|
| test 集整体 accuracy | **≥ 0.90**（评估门禁硬线） | v1 实测 0.94；低于线 `tests/eval` 直接 fail |
| macro_f1 | 观察 | 防大类掩盖小类 |
| 逐类 F1（worst 5 打印） | 观察 | 单类明显塌陷→查该类数据量/易混淆对 |
| 易混淆组混淆矩阵 | 观察 | 三组：退款/退货/取消、物流轨迹/时效/订单状态、商品咨询/FAQ（taxonomy 第 6 节） |

评估门禁在 `tests/eval/test_intent_eval.py`，三件事：

1. **控制层对抗样例**（纯规则、离线可跑）：「算了还是帮我退款吧」不得被
   吞成放弃、「订单被取消了怎么回事」不得判成取消请求等——改分类器必过；
2. **控制层放行样例**：含控制关键词的业务咨询（「怎么取消自动续费」）
   必须放行到语义层；
3. **SetFit test 集 accuracy ≥ 0.90**：模型产物存在时跑，不存在时
   **显式 skip（`pytest -rs` 可见，不允许静默当作通过）**——CI 无模型
   runner 时此项跳过，本地重训后必须亲自跑。

## 5. 数据回流（线上 bad case → 训练集）

```bash
# ① 导出待审样本（低置信/LLM 二判/FALLBACK/差评轮次，已去重脱敏、排除已入训练集）
uv run python scripts/export_review_set.py --tenant t1

# ② 人工审核：给导出 CSV 里的样本标上正确意图码（taxonomy 规范码）

# ③ 合并重建 v42 + 重训（--extra 可多个文件，source 标记 extra:<文件名>）
uv run python scripts/build_intent_dataset.py --extra data/export/review_xxx_labeled.csv
uv run python scripts/train_setfit_intent.py
```

低置信家常闲聊是已知高频回流类型（开放域表达训练集覆盖不了）：短期由
KNN 闲聊救援兜住（README 第 6 节，`SETFIT_KNN_CHITCHAT`），长期方案就是
本节回流→扩充 CHITCHAT 样本→重训。

## 6. 重训闭环清单（每次重训照此执行，缺一不可）

```text
① （数据有变更时）重建数据集：
   uv run python scripts/build_intent_dataset.py [--extra ...]
② 训练：
   uv run python scripts/train_setfit_intent.py
③ 评估门禁（三个评估集全过才算数）：
   uv run pytest tests/eval/ -rs
   —— 意图门禁（acc≥0.90 + 控制层回归）/ 多意图切分器（≥10/13）/ RAG 门禁
④ 重建 KNN 示例向量索引（红线：分类与近邻必须同源语义体——
   SetFit 重训后旧索引即失效，KNN 开关开着时必须重建）：
   uv run python scripts/build_intent_example_index.py
④' 重训 Mode Gate（Stage 30，同一红线：mode head 消费 SetFit body 的
   embedding，body 变了头就失效；MODE_GATE_ENABLED=true 时必须执行）：
   uv run python scripts/train_mode_gate.py
⑤ 重启服务生效（模型进程启动时加载；INTENT_CLASSIFIER=hybrid）
⑥ 观察下游联动：
   - count_intent 指标各 decision_source 占比（SETFIT_LOW_CONF/LLM 应随
     模型变好而下降）；
   - Meta 影子分歧率（meta_shadow_total）——SetFit 分数分布变了，Meta 层
     特征分布随之漂移，分歧率突变属预期，观察稳定后再做 Meta 侧结论；
   - 错向监控 SQL（docs/ops/quality_queries.md 第 9 组）不上升。
```

## 7. 运行时集成速查

```text
配置：INTENT_CLASSIFIER=rule|hybrid（默认 rule=纯规则零依赖；hybrid 启用 SetFit）
     SETFIT_MODEL_PATH=models/intent_setfit_v1（相对路径锚定仓库根）
降级：产物缺失/加载失败 → 自动回落降级规则分类器（RULE_FALLBACK 来源），
     不阻塞启动——这也是 models/ 不进镜像的部署形态默认行为
下游消费 SetFit 输出的模块（重训影响面）：
     margin 路由与软确认（Stage 26 P2/P4，阈值基于分数分布，大改模型后需复核）
     KNN 示例交叉验证（README 第 6 节，重训必重建索引）
     Meta-classifier 特征（setfit_top1/2_label/score/margin，影子期自动适应）
     LLM 二判触发条件（低置信/低 margin 才调用）
```
