# 意图评估集与回归规范（Stage 04-01）

> 规则：**任何对分类器的改动（规则词表 / SetFit 重训 / LLM 二判 prompt / 阈值）
> 都必须跑本评估，域级准确率不得回退**（taxonomy 第 9 节要求的落地）。

## 1. 评估资产

| 资产 | 位置 | 说明 |
|---|---|---|
| 全量标注数据 | `docs/intent/intent_train_v42_project.csv`（test split，1474 条 29 类） | SetFit 语义层准确率评估基准 |
| 控制层对抗样例 | `tests/eval/test_intent_eval.py` 的 `CONTROL_CASES` | taxonomy 第 4/6 节裁决规则的固化（取消订单 vs 放弃、确认门上下文限定等） |
| 模型指标快照 | `models/intent_setfit_v1/metrics.json` | 每次训练的 accuracy/逐类 F1/混淆矩阵 |

## 2. 运行方式

```bash
uv run pytest tests/eval/ -v          # 控制层（离线必跑）+ SetFit test 集（模型存在时）
uv run python scripts/train_setfit_intent.py   # 重训（自动输出完整指标）
```

## 3. 验收线

- 控制层对抗样例：**100% 通过**（确定性规则，无灰度）；
- SetFit test 集整体 accuracy ≥ **0.90**；易混淆三组（退款/退货/取消、物流三类、
  商品信息/FAQ）组内单类 F1 ≥ 0.75（见 metrics.json confusion_groups）；
- LLM 二判：无独立自动评估（依赖真实 LLM），上线后从 decision_log 抽
  `decision_source=LLM` 的样本人工抽检，错判样本回流训练集（数据集规范第 5 节）。

## 4. 新增意图时

1. 先在 `docs/chat/intent_taxonomy.md` 注册 → 同步 `app/chat/intent/catalog.py`；
2. 补训练数据（每类 ≥300 条，含易混淆负例）→ 重建 v42 → 重训；
3. 在 CONTROL_CASES 补充该意图的边界对抗样例（若涉及裁决规则）；
4. 跑本评估全绿后才能合入。
