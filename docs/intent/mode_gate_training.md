# Conversation Mode Gate 训练操作文档

> 设计与裁剪决策见 `docs/requirements/stage-30-conversation-mode-gate/01_mode_gate.md`；
> 本文档回答：**数据在哪、怎么训、指标怎么读、启用条件、重训时机**。
> 与本目录另两份训练文档的关系：SetFit（`setfit_training.md`）答「什么业务
> 意图」，Meta（`meta_classifier_training.md`）答「对当前任务做什么操作」，
> Mode Gate 答「这句话是闲聊/业务/混合/超范围（OOS）哪种模式」——三个模型三条轴。

---

## 1. 数据：v43 包

`docs/intent/intent_mode_v43_package/`（生成脚本与 SHA-256 清单随包，
可复现；详见包内 `intent_mode_v43_README.md`）：

| 文件 | 用途 |
|---|---|
| `conversation_mode_train_v1.csv` | **Mode Gate 训练用这份**：14400 行四类均衡（SOCIAL_ONLY/TASK_ONLY/MIXED/OOS 各 3600），split 自带 |
| `intent_train_v43_clean_nodup_mode_annotated.csv` | 单一事实来源/审计：v41 全量 19641 行 + mode 标注列，不直接训练 |
| `intent_train_v43_project_business.csv` | **阶段 2 才用**：25 类业务版 SetFit 重训数据（去闲聊类）。Mode Gate 稳定接管前**不要**用它重训 SetFit（需求文档第 7/10 节红线） |

数据纪律：

- `UNCERTAIN` 是推理时拒识状态，**不是训练标签**（四分类训练）；
- TASK_ONLY/SOCIAL_ONLY 主要来自 v42 真实语料；**MIXED/OOS 是合成冷启动**
  （`review_status` 列已标记）——离线高分不构成上线依据，接管范围
  v1 只有高置信 SOCIAL_ONLY；
- OOS ≠ 闲聊：「帮我写代码」是超范围任务不是社交，两类样本严格分开。

## 2. 怎么训练

```bash
# 前置：SetFit 意图模型产物必须在位（mode head 用它的 body 编码——
# 分类与模式判定必须同源表示，models/intent_setfit_v1/）
uv run python scripts/train_mode_gate.py

# 常用参数
uv run python scripts/train_mode_gate.py \
  --data docs/intent/intent_mode_v43_package/conversation_mode_train_v1.csv \
  --out models/mode_gate_v1
```

训练过程：SetFit body 编码全量文本（CPU 数分钟）→ LR 四分类
（class_weight=balanced）→ **Platt 概率校准**（val 集拟合
`CalibratedClassifierCV(sigmoid)`——运行时阈值 0.88 要有「≈88% 正确率」
的语义，未校准的原始分数做不到；校准失败自动降级未校准 LR 并在
spec 中标记）→ test 集评估。

产物（`models/mode_gate_v1/`，不进 git、不进镜像——缺失时运行时
fail-open 等同关闭）：

```text
mode_head.joblib   # 校准后的四分类头
mode_spec.json     # 标签表 / embedding 来源(setfit 路径) / 校准方式 / 数据指纹
metrics.json       # accuracy / macro_f1 / 逐类 P・R・F1 / 混淆矩阵 / SOCIAL precision
```

## 3. 指标怎么读（Precision 优先，不看总 Accuracy 决策）

| 指标 | 期望 | 为什么 |
|---|---|---|
| **SOCIAL_ONLY precision** | **首要指标，越高越好** | 业务/投诉被误吞成闲聊是最高代价错误（「你们退款慢死了哈哈」回成客套话） |
| OOS precision/recall | 观察 | OOS 回复子开关的开启依据（误判 FAQ 为 OOS 会误拒真实咨询） |
| MIXED recall | 观察 | 漏掉业务子句的风险面（v1 影子，不影响线上） |
| SOCIAL_ONLY recall | 允许先低 | 第一阶段纪律：**Precision 极高、Coverage 可以低**——漏放的闲聊继续走二判，只是没省到钱，无错误代价 |

## 4. 启用步骤（默认关，按序执行）

```text
① 训练出产物（上节）
② .env 打开：MODE_GATE_ENABLED=true
③ 验收：uv run pytest tests/stage30 tests/eval -rs 零回归
④ 观察（1-2 周）：
   - conversation_mode_total{mode="SOCIAL_ONLY",accepted="true"} = 直通量；
   - llm_calls_total{purpose="classify"} 应下降（省掉的二判）；
   - decision_log 抽查 intent_result_json.mode_gate 证据 +
     accepted 轮次的人工回看（有没有业务被误吞）；
   - 误吞发现 → 提高 MODE_GATE_SOCIAL_MIN_SCORE 或补业务反证词表
⑤ OOS 边界回复子开关（MODE_GATE_OOS_REPLY_ENABLED）：影子期确认
   OOS 误判分布干净后再开
```

## 5. 重训时机（红线）

1. **SetFit 意图模型重训后必须重训 mode head**（同 KNN 索引纪律：
   body 变了，头就失效——`setfit_training.md` 第 6 节闭环清单已含此步）；
2. MIXED/OOS 合成样本被真实回流替换后（影子日志 + 人工审核，
   流程同 Meta：decision_log 里 mode_gate 证据即回流原料）；
3. 阈值标定与重训解耦：只调 `.env` 阈值不需要重训。
