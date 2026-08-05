# Stage 30 需求：Conversation Mode Gate（对话模式门）

> 数据包：`docs/intent/intent_mode_v43_package/`（v43，2026-08-05）。
> 训练操作文档：`docs/intent/mode_gate_training.md`。
> 本文档记录设计决策：采纳了评审方案的什么、裁剪了什么、为什么。

---

## 1. 背景：要解决的唯一核心问题

「低置信闲聊 KNN 救援」（Stage 26 增量）已经把高频家常表达
（你好/谢谢/哈哈/今天真热）挡在 LLM 二判之外，但仍有一类浪费：

```text
旧路径（问题）：
  训练集未覆盖的闲聊新表达 / 表面带业务词的吐槽 / OOS 请求
  → SetFit 低置信 → LLM 意图二判（第 1 次调用）
  → 判出闲聊 → 闲聊回复可能再润色（第 2 次调用）
  = 一句闲聊花两次 LLM

新路径（本 Stage）：
  Mode Gate 高置信闲聊 → 直接闲聊模板回复
  = 零次 LLM（润色开启时至多一次）
```

Mode Gate 判断的不是 29 类业务意图，而是**对话模式**（另一条轴）：

| 模式 | 含义 | 例子 |
|---|---|---|
| SOCIAL_ONLY | 纯闲聊/社交 | 「你回复得还挺快哈哈」 |
| TASK_ONLY | 纯业务 | 「帮我查订单」 |
| MIXED | 闲聊+业务混合 | 「今天真热，顺便查下物流」 |
| OOS | 有明确任务但超出客服范围 | 「帮我写一段 Python」 |
| UNCERTAIN | 推理时拒识（**不是训练标签**） | 分数/margin 不达标 |

关键区分：**OOS ≠ 闲聊**。「帮我写代码」是用户有明确任务但不在业务范围，
训成 CHITCHAT 会让闲聊门吞掉它并给出不合时宜的客套回复。

## 2. 双轴决策系统（评审方案核心，采纳）

```text
轴一：对话模式        SOCIAL / TASK / MIXED / OOS   ← Mode Gate（本 Stage）
轴二：业务任务操作    CONTINUE / SWITCH / ACCEPT / SEND_LLM / CLARIFY / UNKNOWN
                                                    ← Meta-classifier（Stage 27）
```

**Meta-classifier 不加 CHITCHAT 类（明确决策）**：Meta 的职责是「业务意图
与当前任务的关系决策」，闲聊在另一条轴上。落实方式：

- `MODE_SOCIAL` 决策来源**不进** `meta_shadow._SHADOW_SOURCES`（部署域不变，
  测试锁定）——模式门放行的社交轮不是业务操作决策，不产生 Meta 训练样本；
- SOCIAL_HOLD 是**确定性策略**不是学习标签（见第 5 节）；
- Mode 证据未来可作为 Meta 的输入特征（`conversation_mode` 列），那是
  特征扩展不是类别扩展（遗留 5）。

## 3. 架构与链路位置

```text
用户消息
  ↓
规则控制层（不动：确认/否定/放弃/纯槽位/补槽守护/显式转人工——Mode Gate 永远碰不到）
  ↓ 未命中
共享 SetFit embedding（**一轮只编码一次**，body 即 bge-small-zh + 对比学习微调）
  ├─ mode_head（本 Stage：LR 四分类 + 概率校准，≈一次矩阵乘的开销）
  └─ intent_head（现有 29 类，改为消费同一 embedding）
  ↓
Mode Gate 决策（v1 只接管一条分支，其余全部影子）：
  ├─ SOCIAL_ONLY 高置信 + 无业务反证 → 直接 CHITCHAT.GENERAL（MODE_SOCIAL 来源）
  │     ├─ 无任务 → 闲聊模板回复（SOCIAL_RESPOND，现有 responder）
  │     └─ 有任务 → SOCIAL_HOLD：闲聊回复 + 保持任务 + 续办提示（第 5 节）
  ├─ TASK_ONLY / MIXED / OOS / UNCERTAIN
  │     → 现有流水线原样执行（SetFit 采纳阈值 → KNN → LLM 二判 → 澄清）
  │     → mode 证据随 intent_result 落决策日志（影子观察）
  └─ （可选子开关）OOS 高置信 + OOS 回复开启 → 能力边界话术，跳过澄清 LLM
```

**为什么 v1 只接管 SOCIAL_ONLY**（数据包 README 的建议，采纳）：
MIXED/OOS 训练数据主要是合成冷启动（`review_status` 已标记），离线高分
不构成上线依据；SOCIAL_ONLY 判错的代价最低（回了句客套话，用户重说即
纠正，任务不受影响——SOCIAL_HOLD 保证），且第一阶段目标是
**Precision 极高、Coverage 可以低**：宁可部分闲聊继续走二判，
也不能把退款/投诉/取消误吞成闲聊。

## 4. SOCIAL_ONLY 接受条件（组合判据，不只看概率）

```python
social_accepted = (
    mode_top1 == "SOCIAL_ONLY"
    and mode_score >= MODE_GATE_SOCIAL_MIN_SCORE        # 默认 0.88
    and mode_margin >= MODE_GATE_SOCIAL_MIN_MARGIN      # 默认 0.20
    and 无业务反证：
        - 不含强业务关键词（退款/取消/订单/投诉/发票/价格…）
        - 不含槽位值形态（订单号/手机号正则，复用 slots/patterns）
        - 不含显式切换信号（「另外/顺便」，复用 SWITCH_SIGNAL_RE）
)
if has_active_task:   # 任务进行中更严格
    social_accepted &= mode_score >= MODE_GATE_SOCIAL_MIN_SCORE_ACTIVE  # 默认 0.92
```

原则固化：**有业务副作用可能时，业务信号优先于闲聊信号**。
「你们退款真的慢死了哈哈」含「退款」→ 反证命中 → 不接受 → 走业务流水线
（大概率 AFTERSALE.COMPLAIN）。「你回复好快，订单号是12345678」在
COLLECTING 下会先被 Stage 26 补槽守护接走，根本到不了 Mode Gate——
控制层优先序保证了补槽不会被闲聊门吞。

所有阈值是**冷启动基线不是上线定值**，标定依据（真实流量后）：
业务误吞率（最重）、闲聊直通覆盖率、二判调用量降幅、任务恢复成功率。

**概率校准（采纳）**：SetFit/LR 概率不天然等于真实正确率，训练脚本内置
Platt scaling（`CalibratedClassifierCV(sigmoid)`，val 集拟合）——阈值
0.88 语义上接近「88% 正确率」才有意义。conformal prediction 记为遗留。

## 5. SOCIAL_HOLD：任务中的闲聊插话

**盘点结论：结构性部分已存在**。状态机（`state/manager.py`）对
CHITCHAT/META 类意图本就「DONE + 不动 active_task + 不动状态」——
闲聊插话不切任务、不清任务、不抽槽是 Stage 03 起的既有语义。

本 Stage 补的是**续办提示**：Mode Gate 接受的社交轮（MODE_SOCIAL 来源）
且有进行中任务在 COLLECTING 时，闲聊回复后追加一句
「刚才的『退款』还在办理中，继续提供所需信息即可」
（i18n key `mode.social_resume`）。确定性策略，两行判断：

```text
mode==SOCIAL_ONLY and active_task → SOCIAL_HOLD（闲聊回复+保任务+续办提示）
mode==SOCIAL_ONLY and 无任务      → SOCIAL_RESPOND（纯闲聊回复）
```

不进 Meta 训练标签、不进状态机新状态、不新增 GraphState 字段。

## 6. 对评审方案的裁剪（明确不做与理由）

| 方案项 | 决策 | 理由 |
|---|---|---|
| `app/chat/chitchat/` 新模块（L0/L1/L2 级联） | **不建** | L0 模板=现有 skill responder；L2=现有润色路径；跳过二判后「双调用」已消除。L1 本地小生成模型引新依赖，收益存疑，遗留 |
| MIXED 主动分段路由 | **v1 影子** | 现有 multi_intent 分段器已处理带并列标记的混合句（业务优先、次要入栈）；无标记 MIXED 的新分段器等真实样本standing后再做（遗留 2） |
| OOS 能力边界回复 | **实现但默认关** | `MODE_GATE_OOS_REPLY_ENABLED=false`；OOS 数据是合成的，先影子看误判分布再开。开启后收口在澄清生成入口（OOS 高置信直接边界话术，省掉澄清 LLM 调用） |
| 决策日志 10+ 新顶层字段 | **不加列** | mode 证据作为 `intent_result_json.mode_gate` 子对象落库（与 margin/pending_fill/example_knn 同模式），观测 SQL 可查，零 migration |
| 指标 9 个 | **收敛为 1 个** | `conversation_mode_total{mode,accepted}`——多租户不进 label 纪律；直通量=accepted social，二判降幅由既有 `llm_calls_total{purpose=classify}` 对比 |
| Meta-classifier 加 CHITCHAT 类 | **不加**（方案自己也反对） | 见第 2 节双轴 |
| 第二个编码模型 | **不加**（方案同） | mode_head 共享 SetFit body；顺带把 intent 推理改为同一 embedding 复用（一轮一次编码） |

## 7. 数据与训练（v43 包）

- `conversation_mode_train_v1.csv`：14400 行四类均衡（各 3600），
  split 自带（train 11647/val 1236/test 1517），归一化去重、零跨标签冲突；
  TASK_ONLY/SOCIAL_ONLY 大部分来自 v42 真实语料映射，**MIXED/OOS 为合成
  冷启动**（`review_status` 标记，离线高分不构成上线依据——与 Meta v1 同纪律）；
- 训练：`scripts/train_mode_gate.py`——SetFit body 编码（与线上同源表示，
  红线：**SetFit 重训后 mode head 必须重训**）→ LR 四分类 + Platt 校准 →
  产物 `models/mode_gate_v1/`（mode_head.joblib / mode_spec.json / metrics.json）；
- 首要指标：`SOCIAL_ONLY precision`（业务误吞必须极低）> OOS P/R >
  MIXED recall > accuracy；
- `intent_train_v43_project_business.csv`（25 类，去掉 CHITCHAT.*/
  BOT_IDENTITY/UNKNOWN）是**阶段 2 的 SetFit 重训数据，本 Stage 不用**：
  砍掉闲聊类别的 SetFit 必须在 Mode Gate 稳定接管后才能上——否则门一关
  （默认关）闲聊就没有任何分类器接得住（零回归红线）。阶段 2 清单见第 10 节。

## 8. 配置（全部默认关/保守，零回归）

```text
MODE_GATE_ENABLED=false                    # 总开关；产物缺失/加载失败/推理异常一律 fail-open 走现有流水线
MODE_GATE_DIR=models/mode_gate_v1          # 相对路径锚定仓库根
MODE_GATE_SOCIAL_MIN_SCORE=0.88            # SOCIAL 直通线（校准后概率）
MODE_GATE_SOCIAL_MIN_MARGIN=0.20           # top1-top2 分差线
MODE_GATE_SOCIAL_MIN_SCORE_ACTIVE=0.92     # 任务进行中更严
MODE_GATE_OOS_REPLY_ENABLED=false          # OOS 边界回复子开关（影子先行）
```

## 9. 验收标准

1. `MODE_GATE_ENABLED=false`（默认）：全量测试零回归，hybrid 路径字节级不变；
2. 开启且产物在位：高置信纯闲聊（无业务词）返回 `MODE_SOCIAL` 来源、
   不触发 LLM 二判；含业务词句子反证拦截走原流水线；
3. SOCIAL_HOLD：COLLECTING 中闲聊插话回复含续办提示、active_task/状态不变；
4. 产物缺失/损坏：静默降级，行为与关闭一致（fail-open）；
5. `MODE_SOCIAL` 不在 meta_shadow 部署域（测试锁定，双轴纪律）；
6. 训练脚本可复跑：四类 P/R/混淆矩阵 + SOCIAL precision 醒目输出 + 校准产物；
7. mode 证据落 `decision_log.intent_result_json.mode_gate`（观测可查）。

## 10. 阶段 2（SetFit v43 业务版重训，另行执行）

触发条件：Mode Gate 真实流量接管 SOCIAL_ONLY ≥ 2-4 周、业务误吞率达标。
届时执行：v43 business CSV（25 类）重训 SetFit → 重建 KNN 索引 →
意图评估门禁改用 v43 test 集 → KNN 闲聊救援退役（被 Mode Gate 替代）→
`_KNN_CHITCHAT_RESCUE_INTENTS` 分支下线。在此之前 v42 模型与低置信闲聊
救援保持现状——两套机制共存期以 Mode Gate 优先（它在语义层入口，先命中）。

## 11. 遗留（明确记录）

1. 阈值标定：全部冷启动默认，真实流量后按第 4 节四指标标定；
2. MIXED 主动分段：无并列标记的混合句分段器（真实样本攒够后）；
3. OOS 回复开启评估：影子期看 OOS 误判分布（尤其 FAQ/商品咨询被误判 OOS）；
4. MIXED/OOS 合成数据真实化：影子日志回流人工审核替换（与 Meta 同流程）;
5. conversation_mode 作为 Meta-classifier 输入特征（特征扩展，非类别扩展）；
6. conformal prediction / CICC 式候选集澄清（数据量够后）；
7. UNCERTAIN 状态感知澄清话术（「随便聊聊还是继续退款？」——需真实分布）；
8. example_knn 复用同轮 embedding（当前 KNN 内部重复编码一次，微优化）。
