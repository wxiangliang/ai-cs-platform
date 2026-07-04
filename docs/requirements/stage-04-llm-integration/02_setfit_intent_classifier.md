# Stage 04-02 需求：SetFit 语义意图分类器（v1）

> 前置阅读：`docs/chat/intent_taxonomy.md`（尤其第 4 节判定优先级、第 8 节分类器演进）、
> `docs/intent/README.md`（训练数据集规范）。
> 定位：本文档是 Stage 04「混合意图分类」的第一步落地——**用本地 SetFit 模型替代原计划中
> "LLM few-shot 分类"作为语义层**；LLM 分类层降级为后续增强（见第 7 节设计决策）。

---

## 1. 目标

规则关键词分类器覆盖不了口语化/长尾表达（「东西还没到我等急了」「能便宜点不」），
引入基于 SetFit（sentence-transformer 微调 + 分类头）的本地语义分类器：

- 覆盖 29 个可语义判定的意图（taxonomy 注册表中除上下文敏感 META 意图外的全部）；
- 单条推理 CPU < 50ms、无外部 API 依赖、按置信度阈值兜底 UNKNOWN；
- 模型不可用时自动降级回规则分类器，**绝不打断主链路**。

## 2. 三层混合架构（HybridIntentClassifier）

判定顺序与 taxonomy 第 4 节优先级严格一致：

```text
第 1 层 规则控制层（高精度短路，不交给模型）：
   取消订单正则 → ORDER.CANCEL
   META 关键词  → TRANSFER_HUMAN / ABORT / BOT_IDENTITY
   确认门应答   → CONFIRM / DENY（仅 CONFIRMING 状态）
   纯槽位输入   → SLOT_ONLY（仅有 active_task 语义）
第 2 层 SetFit 语义层（29 类业务/闲聊/FAQ 意图）——风险分级双阈值：
   读操作意图：confidence ≥ INTENT_CONFIDENCE_THRESHOLD（默认 0.40，val 集标定）→ 采纳
   写操作意图（L2/L3：退款/退换/取消/改地址/开票等）：≥ INTENT_CONFIDENCE_THRESHOLD_WRITE（默认 0.60）
     —— 错判开启高风险流程的代价大于多问一句，宁可澄清；确认门仍是最后防线
   低于对应阈值 → META.UNKNOWN（走澄清/知识库兜底），top_k 落决策日志供回流
第 3 层 降级层：
   模型未加载/推理异常 → 整体退回 RuleIntentClassifier（关键词全表），
   decision_source 标记 SETFIT_FALLBACK_RULE，日志告警
```

**上下文敏感意图（CONFIRM/DENY/SLOT_ONLY/CORRECTION）永远不进模型**——
它们的语义由对话状态决定，模型只看单句必然误判（这是训练数据剔除微量类的原因）。

## 3. 训练要求

- 数据：`docs/intent/intent_train_v42_project.csv`（由 build 脚本生成，29 类 ~20k 条），
  用其 split 列，禁止自行重新划分（保证结果可比）。
- 基座模型：`BAAI/bge-small-zh-v1.5`（中文、24M 参数、CPU 友好）；可配置替换。
- CPU 训练策略（无 GPU 环境）：对比学习 body 微调用每类采样子集（默认 16 条/类），
  分类头（LogisticRegression）用全量 train 集 embedding 训练——SetFit 标准做法，
  兼顾效果与训练时长（目标 < 30 分钟 CPU）。
- 产物：`models/intent_setfit_v1/`（模型 + `metrics.json` + `labels.json`），**不入 git**（.gitignore）。
- 评估：test 集整体 accuracy / macro-F1 + 逐类 F1 + 易混淆三组混淆矩阵
  （REFUND/RETURN/CANCEL、TRACK/DELIVERY_TIME/QUERY_STATUS、ASK_INFO/FAQ.GENERAL），
  写入 metrics.json 并回填本文档附录。
- 验收线：test 整体 accuracy ≥ 0.90，且易混淆三组内无一类 F1 < 0.75（达不到需分析 bad case 调数据）。

## 4. 运行时集成

- `app/chat/intent/setfit_classifier.py`：模型加载（进程内单例、懒加载）+ 推理封装；
  推理是 CPU 密集同步调用，必须 `asyncio.to_thread` 包装，避免阻塞事件循环。
- `app/chat/intent/hybrid_classifier.py`：实现第 2 节三层架构，遵守 `IntentClassifier` 协议。
- 配置（settings）：

```text
INTENT_CLASSIFIER=rule|hybrid              # 默认 rule；hybrid = 规则控制层 + SetFit
SETFIT_MODEL_PATH=models/intent_setfit_v1
INTENT_CONFIDENCE_THRESHOLD=0.40           # 读操作意图阈值（val 集标定，见附录）
INTENT_CONFIDENCE_THRESHOLD_WRITE=0.60     # 写操作意图阈值（风险分级）
```

- `intent_classify` 节点改为从工厂取分类器实现，不 import 具体类。
- decision_source 扩展：`SETFIT` / `SETFIT_LOW_CONF`（低置信兜底 UNKNOWN）/ `SETFIT_FALLBACK_RULE`（模型不可用降级）。
- decision_log 的 `intent_result_json.top_k` 记录模型 top-3 标签与分数（排查与回流依据）。

## 5. 目录和文件

```text
scripts/build_intent_dataset.py      # v41 → v42 映射清洗 + FAQ.GENERAL 生成（可重复执行）
scripts/train_setfit_intent.py       # 训练 + 评估 + 保存产物
app/chat/intent/setfit_classifier.py
app/chat/intent/hybrid_classifier.py
app/chat/intent/factory.py           # 按 INTENT_CLASSIFIER 返回实现
models/                              # 产物目录（gitignore）
docs/intent/README.md                # 数据集规范
```

## 6. 验证方式

1. `uv run python scripts/build_intent_dataset.py` → 生成 v42，打印各类分布（29 类，无 taxonomy 外标签）。
2. `uv run python scripts/train_setfit_intent.py` → 训练完成，metrics.json 达到第 3 节验收线。
3. `INTENT_CLASSIFIER=hybrid` 启动服务：
   - 「东西还没到我等急了」→ LOGISTICS.TRACK（规则版会 UNKNOWN）；
   - 「能便宜点不」→ PRODUCT.ASK_PRICE；
   - 「我要取消订单」仍走规则层 → ORDER.CANCEL（不受模型影响）；
   - 确认门三轮回归不受影响（CONFIRM/DENY 仍规则判定）；
   - decision_log 可见 decision_source=SETFIT 与 top_k。
4. 把 SETFIT_MODEL_PATH 指向不存在路径启动 → 自动降级规则分类器，请求不失败，日志告警。
5. `INTENT_CLASSIFIER=rule` 行为与 Stage 03 完全一致（回归）。

## 7. 设计决策记录

1. **为什么 SetFit 而不是直接 LLM 分类**（对原 stage-04 方案的调整）：
   本地模型零 API 成本、延迟稳定（<50ms vs LLM 500ms+）、离线可用；
   数据量（每类 300-1500 条）远超 SetFit few-shot 需求，效果有保障。
   LLM 分类层保留为后续增强：处理 SetFit 低置信样本的二次判定（成本只花在难例上）。
2. **为什么规则控制层仍在模型之前**：META 控制意图（转人工/放弃）错判代价高且关键词精度足够；
   上下文敏感意图模型无法判定；「取消订单 vs 放弃会话」的裁决规则（taxonomy 4 节）必须确定性执行。
3. **为什么训练数据剔除 SAFETY.ABUSE**：辱骂检测是护栏（guardrail_check 节点）职责，
   不是意图路由问题，混入会污染意图空间；数据保留在 v41 供 Stage 05 护栏建设。

---

## 附录：训练结果（2026-07-02，模型 v1）

**训练配置**：基座 `BAAI/bge-small-zh-v1.5`；数据 `intent_train_v42_project.csv`
（train 17205 / test 1474，29 类）；body 微调每类 16 条采样、400 步；
分类头 LogisticRegression 用全量 train 集 embedding 拟合；**CPU 总耗时 9 分钟**。

**test 集结果（✅ 达到验收线 accuracy ≥ 0.90）**：

```text
accuracy = 0.9417    macro-F1 = 0.9233    单条推理 CPU 10~25ms
最弱类：META.TRANSFER_HUMAN 0.75（混合架构中该类由规则控制层先接住，模型层弱化可接受）、
        AFTERSALE.RETURN 0.78、PROMOTION.COUPON 0.85、CHITCHAT.THANKS 0.86、FAQ.GENERAL 0.87
完整逐类 F1 见 models/intent_setfit_v1/metrics.json
```

**易混淆组混淆矩阵（行=真实，列=预测）**：

```text
退款/退货/取消组：REFUND [40,3,0] / RETURN [3,23,0] / CANCEL [2,0,34] —— 组内 F1 全部 ≥ 0.75 ✅
物流组：TRACK [61,0,0] / DELIVERY_TIME [0,113,1] / QUERY_STATUS [0,0,65]（3 条漏到组外）✅
商品信息/FAQ 组：ASK_INFO [114,0] / FAQ.GENERAL [1,38]（各有 10~12 条漏到组外低置信）✅
```

**置信度阈值标定（val 集 1462 条，acc 0.9412）**：

| 阈值 | 覆盖率 | 覆盖内精度 | 被误拒的正确预测 |
|---|---|---|---|
| 0.40 ★读操作采用 | 97.7% | 95.4% | 0.9% |
| 0.50 | 93.1% | 96.8% | 4.0% |
| 0.60 ★写操作采用 | 87.7% | 97.9% | 8.3% |

结论：0.55 单一阈值会白白把 6% 正确预测打回 UNKNOWN；改为风险分级双阈值
（读 0.40 / 写 0.60）。**换 embedding 基座或数据集重训后必须重新标定本表。**

**已知短板与回流方向**（均走安全兜底路径，不产生错误执行）：

1. 口语化物流长尾：「东西还没到我等急了」→ 低置信 UNKNOWN（top1 误向 COMPLAIN/DELIVERY_TIME）
   → 走知识库/澄清兜底；
2. 「政策疑问 vs 售后动作」边界：「七天无理由退货是什么意思」→ AFTERSALE.RETURN(0.62)
   而非 FAQ.GENERAL(0.19)——含「退货」字样的政策问句易被 500 条 RETURN 动作样本带偏，
   进入退货补槽流程（用户可「算了」退出，且写操作有确认门，无风险执行）。
   回流方向：补充「X是什么意思/什么规定/什么条件」句式的 FAQ.GENERAL 对抗样本。

后续从 decision_log 的 SETFIT_LOW_CONF / 人工纠正记录回流补充训练数据（数据集规范第 5 节）。

**运行时配套**：SkillRegistry 已扩展到全部 29 类（新增 COMPARE/RECOMMEND/CREATE/
CHANGE_ADDRESS/REPAIR/DELIVERY_TIME/SHIPPING_FEE/PAYMENT×3/PROMOTION×2 的模板技能），
守护测试 `tests/intent/test_registry_coverage.py` 保证模型标签空间与技能注册表不脱节。
