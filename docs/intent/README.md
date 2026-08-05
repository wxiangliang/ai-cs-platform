# 意图训练数据集说明

> 意图码的单一事实来源是 `docs/chat/intent_taxonomy.md`；本目录管理**训练数据与两份训练操作文档**。
> 训练 SetFit 看 `setfit_training.md`，训练 Meta-classifier 看 `meta_classifier_training.md`
> ——两个不同的模型（前者答「什么意图」，后者答「对当前任务做什么操作」）。

---

## 1. 文件清单

| 文件 | 说明 |
|---|---|
| `intent_train_v41_clean_nodup.csv` | 原始数据（19641 行，35 标签，自带 train/val/test 划分）。**只读，不再修改，训练不直接用它** |
| `intent_train_v42_project.csv` | **项目对齐版（SetFit 训练用这份）**：由 v41 经映射规则清洗 + 补齐 FAQ.GENERAL 生成，标签与 taxonomy v2 完全一致。由 `scripts/build_intent_dataset.py` 生成，可重复构建 |
| `setfit_training.md` | **SetFit 意图分类器训练操作文档**（Stage 04）：数据来源/训练命令与参数/acc≥0.90 验收门禁/回流合并/重训闭环清单（含 KNN 索引重建红线）；训练脚本 `scripts/train_setfit_intent.py` |
| `meta_classifier_training.md` | **Meta-classifier 训练操作文档**（Stage 27）：学的是「对当前任务做什么操作」不是意图；数据在 `data/电商客服_MetaClassifier_合成训练数据_v1.csv`（12501 行合成表格特征）+ 影子回流，训练脚本 `scripts/train_meta_classifier.py` |
| `mode_gate_training.md` | **Conversation Mode Gate 训练操作文档**（Stage 30）：判「闲聊/业务/混合/OOS」对话模式（第三条轴），共享 SetFit body；训练脚本 `scripts/train_mode_gate.py` |
| `intent_mode_v43_package/` | **v43 数据包**（2026-08-05）：Mode Gate 四分类数据 `conversation_mode_train_v1.csv`（14400 行）+ v41 全量 mode 标注审计版 + **阶段 2 用**的 25 类业务版 SetFit 数据（Mode Gate 稳定前不要用它重训，stage-30 需求第 7/10 节红线） |

## 2. v41 质检结论（2026-07-02）

可用性：**合格，无需重新生成**。
文本长度 p5/p50/p95 = 6/11/18 字，口语化、带真实客服噪声（"呢/哈/帮我看下"），
完全重复文本仅 2 条；业务类每类 270~1135 条。

发现的问题与处置：

| 问题 | 处置（build 脚本自动执行） |
|---|---|
| 标签用的是旧命名/别名（6 处） | 按 taxonomy 2.1 别名对照表映射，见第 3 节 |
| **缺 FAQ.GENERAL（0 条）**——RAG 主入口意图 | 程序化生成 ~600 条（政策/规则类问法模板 × 主题组合），标记 `source=generated_v42` |
| META.SLOT_ONLY(10)/CLARIFY_REPLY(4)/CORRECTION(2) 微量类 | **剔除**：上下文敏感意图由规则+状态机处理（taxonomy 第 3 节 META 表），不进语义分类器 |
| SAFETY.ABUSE(50) 不在意图体系内 | **剔除**（保留在 v41 中，Stage 05 护栏建设时另用） |
| `trainable_for_classifier=False`(48) | 剔除 |
| 2 条完全重复文本 | 去重保留一条 |

## 3. v41 → v42 标签映射规则

| v41 标签 | v42（taxonomy 规范码） | 说明 |
|---|---|---|
| ORDER.QUERY | ORDER.QUERY_STATUS | 命名对齐 |
| ORDER.CHANGE_INFO | ORDER.CHANGE_ADDRESS | 别名合并（改收件人信息）。注：其中少量"改数量"样本暂并入，taxonomy **第 10 节已知缺口备忘 #1** 已记录"修改订单商品/数量"是缺失意图，未来拆出 |
| PRODUCT.ASK_ATTR | PRODUCT.ASK_INFO | 别名合并 |
| PROMOTION.NEGOTIATE | PRODUCT.ASK_PRICE | 议价归询价（BARGAIN 别名） |
| META.HANDOFF_REQUEST | META.TRANSFER_HUMAN | 命名对齐 |
| CHITCHAT.GREETING | CHITCHAT.GENERAL | 别名合并 |
| 其余 23 个标签 | 原样保留 | 已与 taxonomy 一致 |

**v42 结果：29 个类，约 20100 条**（19541 条映射 + ~600 条 FAQ.GENERAL 生成），
沿用 v41 的 split 字段（生成样本按 85/7.5/7.5 分配 train/val/test）。

## 4. v42 字段（训练只用这 4 列）

```text
text   : 用户话术
intent : taxonomy 规范意图码（29 类）
split  : train / val / test
source : 数据来源（v41 原值 / generated_v42）
```

## 4.5 多意图与训练数据的关系（2026-07-03 结论）

**分类器训练数据无需为多意图改动**：运行时多意图 = 分段后每段做单意图分类
（`app/chat/intent/multi_intent.py`），分段产物正是现有单意图训练分布。
v41 的 13 条 `sample_type=multi_intent` 复合句样本（trainable=False，本就是为切分器准备的）
已做成**切分器评估集** `tests/eval/test_multi_intent_eval.py`（当前 12/13，门槛 ≥10；
剩余失败样本语义本身模糊）。切分器改动（标记词表/触发条件）必须跑该评估。

## 5. 数据集变更规范

1. 不直接手改 CSV——改 `scripts/build_intent_dataset.py` 的映射规则或生成模板后重新构建；
2. 新增意图时：先在 taxonomy 注册 → 补充训练数据（每类 ≥300 条，含易混淆负例）→ 重训；
3. 每次重训必须报告 test 集整体准确率与**逐类** F1，易混淆三组（taxonomy 第 6 节）单独看混淆矩阵；
4. 线上 bad case（decision_log 中低置信/人工纠正样本）定期回流到数据集（Stage 09 评估平台闭环）。

## 6. 示例向量交叉验证与 LTR 路线（功能已实现，默认关闭）

> **给未来的自己**：这个功能做好了但没开，别忘了按下面的时机启用。
> 实现：`app/chat/intent/example_knn.py`（接入混合分类器 margin 小分支）；
> 索引构建：`scripts/build_intent_example_index.py`。

**是什么**：SetFit top1/top2 分差小（margin < 0.10）的难例，现行做法是交
LLM 二判（有成本）。本功能先查「训练集示例近邻」——用 SetFit 自己的语义体
把难例和每类真实样本做余弦匹配：近邻**同意** top1 且平均相似度 ≥
`INTENT_EXAMPLE_KNN_MIN_SIM` → 免二判直接采纳（`SETFIT_KNN_CONFIRMED`）；
近邻**不同意** → 绝不改选，分歧证据落 `intent_result_json.example_knn`
随决策日志入库，仍走二判。收益 = 省二判的 LLM 成本与延迟，风险 = 无
（不同意时行为与现状完全一致）。

**启用步骤与时机**（按序执行，缺一不可）：

```text
① 建索引（SetFit 模型重训后必须重建——分类与近邻必须同源表示）：
   uv run python scripts/build_intent_example_index.py
② .env 打开：INTENT_EXAMPLE_KNN_ENABLED=true
③ 验收：uv run pytest tests/eval/ 意图评估门禁零回归
④ 观察（上线后 1-2 周）：
   - count_intent 指标里 SETFIT_KNN_CONFIRMED 占比 = 省掉的二判量；
   - decision_source=LLM 占比应下降，错向监控（quality_queries 第 9 组）不上升；
   - 相似度阈值 0.65 是待标定默认：从 decision_log 的 example_knn.similarity
     分布看确认样本的正确率再调
⑤ 建议启用时点：真实流量跑起来、LLM 二判成本可见之后（与 Meta 影子观察期同步）
```

**低置信闲聊救援**（2026-08-05 补充，随同一开关生效）：

客服场景家常闲聊高频（"今天真热"/"刚下班好累"），开放域表达 SetFit 常给
**低置信**（训练样本覆盖不了），此前每条都花一次 LLM 二判或落 UNKNOWN 尬澄清。
现在低置信分支先查近邻：**top1 就是 CHITCHAT.\* + 近邻同意 + 相似度 ≥
`INTENT_KNN_CHITCHAT_MIN_SIM`(0.70，高于 margin 确认线——低置信要更强证据)**
→ 免二判直接采纳（`SETFIT_KNN_CHITCHAT`）。三条红线：只确认 top1 绝不改选；
只救零副作用的闲聊类（不触碰任务、不触发工具/检索，判错用户重说即纠正）；
**业务意图低置信永不走此通道**。二判目录里 CHITCHAT.GENERAL 的描述同步
充实（家常/情绪/生活琐事），有 Key 时二判归类更准。
长期方案不变：回流真实家常样本 → 标注 → 扩充训练集重训（第 5 节流程）。

**LTR 重排的路线**（2026-07-30 评审结论：先不做，前置在攒）：

```text
现在                 数据成熟后（人工确认难例 ≥ 数千条）
KNN 交叉验证    →    LightGBM LambdaRank 重排 TopK（零新依赖，train 组已有）
（本节功能）          训练数据来源 = export_review_set.py 回流的二判改判样本
                     插入位置 = SetFit 与 LLM 二判之间做分流
                     验证 = A/B 框架（Stage 18）对比二判量下降 & 错向率不升
                     触发信号 = LLM 来源意图占比高 / 二判改判率差
```
