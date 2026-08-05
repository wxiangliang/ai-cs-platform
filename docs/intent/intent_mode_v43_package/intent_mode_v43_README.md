# Intent / Conversation Mode 数据集 v43

生成日期：2026-08-05  
随机种子：`20260805`

## 为什么要重新生成

引入 `Conversation Mode Gate` 后，闲聊不再只是业务 Intent 分类器中的普通类别：

```text
规则/护栏 → Mode Gate(SOCIAL_ONLY/TASK_ONLY/MIXED/OOS)
          → 只有 TASK_ONLY 和 MIXED 的业务段进入 Intent + KNN + Meta + LLM 二判
```

因此原来的两份数据需要做兼容升级，但不能直接覆盖原文件：

1. 完整契约文件保留全部历史行并新增 Mode 标注，便于回放和审计；
2. 项目投喂文件移除闲聊、身份询问和异质 `META.UNKNOWN`；
3. 新增独立四分类 Mode Gate 数据集。

## 文件说明

### `intent_train_v43_clean_nodup_mode_annotated.csv`

- 行数：19641（与 v4.1 完全一致，不删除历史样本）
- 新增字段：
  - `conversation_mode_label`
  - `conversation_mode_trainable`
  - `conversation_mode_label_source`
  - `conversation_mode_reason`
  - `business_intent_classifier_use`
  - `business_intent_exclusion_reason`
  - `dataset_version`
- 用途：单一事实来源、审计、后续人工修订。

### `intent_train_v43_project_business.csv`

- 行数：18384
- Intent 类别数：25
- 字段仍保持 `text,intent,split,source`，兼容现有项目导入。
- 移除：CHITCHAT.GENERAL, CHITCHAT.THANKS, META.BOT_IDENTITY, META.UNKNOWN
- `META.UNKNOWN` 不再作为一个混合文本类别训练；未知由阈值/margin/KNN/LLM 失败后产生。

### `conversation_mode_train_v1.csv`

- 总行数：14400
- 四类各 3600 行：`SOCIAL_ONLY` / `TASK_ONLY` / `MIXED` / `OOS`
- `UNCERTAIN` 是推理时拒识结果，不是训练标签。
- 所有文本做归一化去重；不存在同一句跨标签冲突。
- `MIXED` 与 `OOS` 主要为合成冷启动数据，字段 `review_status` 已明确标记，不能把离线高分视为上线依据。

## 推荐训练方式

- Mode Gate：四分类 LR 作为基线，LightGBM/SetFit Head 作对照；不要直接用 Accuracy 决策上线。
- 首要指标：
  - `SOCIAL_ONLY precision`（业务误吞为闲聊必须极低）
  - `MIXED recall`（不能漏掉业务子句）
  - `OOS precision/recall`
  - `intent_llm_skipped_by_mode`
- 初始上线只接管高置信 `SOCIAL_ONLY`；`MIXED/OOS` 先影子观察。
- 活动任务中的 `SOCIAL_ONLY` 应由策略层输出 `SOCIAL_HOLD`，保持任务并重新提示 pending slot；它不是 Mode 训练标签。

## 标签注意事项

- `META.UNKNOWN` 原样保留在完整契约文件中，但 `conversation_mode_trainable=False`。
- 安全辱骂样本由 guardrail 先处理，不进入 Mode Gate 训练。
- `META.SLOT_ONLY / META.CLARIFY_REPLY / META.CORRECTION` 属于上下文控制样本，也不进入 Mode Gate。
- 合成数据只用于固化管线与特征契约，需通过影子日志和人工审核逐步替换。

## 可复现

运行：

```bash
python generate_intent_mode_v43.py
```

详细计数和 SHA-256 见 `intent_mode_v43_manifest.json`，分布见 `conversation_mode_train_v1_audit.csv`。
