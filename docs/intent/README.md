# 意图训练数据集说明

> 意图码的单一事实来源是 `docs/chat/intent_taxonomy.md`；本目录管理**训练数据**。
> 当前用途：SetFit 语义意图分类器（见 `docs/requirements/stage-04-llm-integration/02_setfit_intent_classifier.md`）。

---

## 1. 文件清单

| 文件 | 说明 |
|---|---|
| `intent_train_v41_clean_nodup.csv` | 原始数据（19641 行，35 标签，自带 train/val/test 划分）。**只读，不再修改** |
| `intent_train_v42_project.csv` | **项目对齐版（训练用这份）**：由 v41 经映射规则清洗 + 补齐 FAQ.GENERAL 生成，标签与 taxonomy v2 完全一致。由 `scripts/build_intent_dataset.py` 生成，可重复构建 |

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
