# 意图体系规范（Intent Taxonomy v2）

> 本文档是全项目意图体系的**单一事实来源（Single Source of Truth）**。
> 代码（`app/chat/intent/types.py`）、Skill 设计文档（`docs/chat/skills_design/`）、
> 意图分类器（规则 / LLM）、评估集，全部以本文档注册的意图码为准。
> 新增 / 废弃意图必须先改本文档，再改代码与 Skill 文件。

---

## 1. 背景：为什么需要这份规范

v1 阶段意图定义分散在三处且互相不一致：

| 位置 | 数量 | 问题示例 |
|---|---|---|
| `app/chat/intent/types.py` | 16 个 | 用 `META.HANDOFF_REQUEST`，缺 `ORDER.CANCEL` 等 13 个业务意图 |
|  `skills/`（原 docs/chat/skills_design/skills/）| 29 个文件 34+ 标签 | 用 `META.TRANSFER_HUMAN`，schema 的 domain 枚举漏了 LOGISTICS |
| `00_skill_schema.md` 文件组织节 | 13 个 | 又是第三套命名（`META.IDENTITY`、`ORDER.QUERY_LOGISTICS`） |

由此产生的实际 bug：用户说「我要取消订单」被规则分类器命中 `META.ABORT`（放弃当前操作），
而不是业务意图 `ORDER.CANCEL`；写操作进入确认门后用户回复「确认」无意图可接，确认门死循环。

---

## 2. 命名与结构约定

- 意图码格式：`DOMAIN.ACTION`，全大写，点号分隔，全局唯一。
- **域（Domain）枚举（9 个）**：`PRODUCT / ORDER / LOGISTICS / AFTERSALE / PAYMENT / PROMOTION / FAQ / CHITCHAT / META`。
  （v1 schema 漏了 `LOGISTICS` 和 `FAQ`，本版补齐。）
- 一个意图码只能属于一个域；跨域复用通过 Skill 的 `triggers.intents` 声明，不新造意图码。
- 废弃意图码保留为**别名（alias）**，分类器仍可识别但必须归一化为规范码后再进入下游。

### 2.1 已废弃别名对照表

| 废弃码 | 规范码 | 说明 |
|---|---|---|
| `META.HANDOFF_REQUEST` | `META.TRANSFER_HUMAN` | v1 代码用的名字，统一到 Skill 文档命名 |
| `ORDER.QUERY_LOGISTICS` | `LOGISTICS.TRACK` | 物流查询统一归 LOGISTICS 域 |
| `META.IDENTITY` | `META.BOT_IDENTITY` | schema 文件组织节的旧名 |
| `AFTERSALE.COMPLAINT` | `AFTERSALE.COMPLAIN` | 拼写统一 |
| `PRODUCT.ASK_SPEC` | `PRODUCT.ASK_INFO` | 规格并入信息咨询 |
| `PAYMENT.DISCOUNT` | `PROMOTION.COUPON` | 优惠归 PROMOTION 域 |
| `PROMOTION.NEGOTIATE` | `PRODUCT.ASK_PRICE`(BARGAIN) | 从未有过 Skill 文件，删除引用 |
| `META.CLARIFY_REPLY` | （删除） | 澄清应答由对话状态机 + SLOT_ONLY/CONFIRM/DENY 处理，不是独立意图 |

---

## 3. 意图注册表（Canonical Registry，33 个）

字段说明：
- **优先级（priority）**：数字越小越先判定（机读字段，分类器与状态机共用），见第 4 节。
- **风险等级（risk_level）**：见第 5 节，决定是否需要确认门、身份核验与决策留痕。
- **上下文约束**：该意图仅在特定对话状态下有效，否则分类器不得输出。

### PRODUCT 域（5）

| 意图码 | 描述 | 必填槽位 | 可选槽位 | 风险 | 优先级 |
|---|---|---|---|---|---|
| PRODUCT.ASK_INFO | 商品介绍/参数/属性（含别名 ASK_ATTR） | — | product_id, attr_type | L0 | 70 |
| PRODUCT.ASK_PRICE | 询价/议价（含别名 BARGAIN） | — | product_id | L0 | 70 |
| PRODUCT.ASK_STOCK | 库存/现货查询 | product_id | sku_attr | L0 | 70 |
| PRODUCT.COMPARE | 多商品对比 | compare_items(两款名称) | — | L0 | 70 |
| PRODUCT.RECOMMEND | 推荐选购 | category, budget | use_scene, preference | L0 | 70 |

> 2026-08-05（Stage 32 实做）：COMPARE 必填由 product_ids 改为
> compare_items（「A和B」整段捕获后节点内切分——用户不知道商品 ID）；
> RECOMMEND 补 category/budget 必填（对候选集影响最大的两个硬约束，
> 一条 collect 模板一次问齐）。use_scene/preference 收集但 v1 不参与排序。

### ORDER 域（4）

| 意图码 | 描述 | 必填槽位 | 可选槽位 | 风险 | 优先级 |
|---|---|---|---|---|---|
| ORDER.QUERY_STATUS | 订单状态查询（是否付款/发货） | customer_phone_or_order_id | — | L1 | 70 |
| ORDER.CREATE | 下单意向引导 | product_id | sku_attr, quantity | L2 | 60 |
| ORDER.CANCEL | 取消未发货订单 | customer_phone_or_order_id | — | **L3** | 60 |
| ORDER.CHANGE_ADDRESS | 改收货地址/收件人（含别名 CHANGE_INFO） | customer_phone_or_order_id, new_address | new_receiver_name, new_receiver_phone | **L3** | 60 |

### LOGISTICS 域（3）

| 意图码 | 描述 | 必填槽位 | 可选槽位 | 风险 | 优先级 |
|---|---|---|---|---|---|
| LOGISTICS.TRACK | 包裹到哪了/物流轨迹 | customer_phone_or_order_id | — | L1 | 70 |
| LOGISTICS.DELIVERY_TIME | 发货/送达时间 | customer_phone_or_order_id | — | L1 | 70 |
| LOGISTICS.SHIPPING_FEE | 运费/包邮政策 | — | destination_region, order_amount | L0 | 70 |

### AFTERSALE 域（5）

| 意图码 | 描述 | 必填槽位 | 可选槽位 | 风险 | 优先级 |
|---|---|---|---|---|---|
| AFTERSALE.REFUND | 退款申请 | customer_phone_or_order_id, refund_reason | — | **L3** | 60 |
| AFTERSALE.RETURN | 退货（寄回）申请 | customer_phone_or_order_id, return_reason | has_original_packaging | **L3** | 60 |
| AFTERSALE.EXCHANGE | 换货申请 | customer_phone_or_order_id, exchange_reason, target_sku | — | **L3** | 60 |
| AFTERSALE.REPAIR | 维修/保修 | customer_phone_or_order_id, issue_description | — | L2 | 60 |
| AFTERSALE.COMPLAIN | 投诉/情绪升级 | complaint_content | customer_phone_or_order_id | L2 | 60 |

### PAYMENT 域（3）

| 意图码 | 描述 | 必填槽位 | 可选槽位 | 风险 | 优先级 |
|---|---|---|---|---|---|
| PAYMENT.METHOD | 支付方式/分期咨询 | — | — | L0 | 70 |
| PAYMENT.ISSUE | 支付失败/扣款异常（全转人工） | customer_phone_or_order_id | issue_type | L1 | 60 |
| PAYMENT.INVOICE | 发票申请 | customer_phone_or_order_id, invoice_type, invoice_title | tax_id(专票必填) | **L3** | 60 |

### PROMOTION 域（2）

| 意图码 | 描述 | 必填槽位 | 可选槽位 | 风险 | 优先级 |
|---|---|---|---|---|---|
| PROMOTION.COUPON | 优惠券查询/使用问题 | user_id（系统注入） | coupon_id | L1 | 70 |
| PROMOTION.ACTIVITY | 活动规则查询 | — | — | L0 | 70 |

### FAQ 域（1，Stage 06 落地）★ 新增

| 意图码 | 描述 | 必填槽位 | 可选槽位 | 风险 | 优先级 |
|---|---|---|---|---|---|
| FAQ.GENERAL | 平台政策/规则/通用知识问答（RAG 主入口：退换货政策总述、保修政策、会员规则、门店信息等，不针对具体订单和具体商品） | — | topic | L0 | 80 |

### MEMBER 域（1）★ Stage 33 新增（2026-08-05）

| 意图码 | 描述 | 必填槽位 | 可选槽位 | 风险 | 优先级 |
|---|---|---|---|---|---|
| MEMBER.REGISTER | 注册/开通本平台会员（新手引导） | phone | — | L2 | 60 |

> 触发：规则层确定性关键词（「注册会员/开通会员/我要注册」，否定语境与
> 第三方平台语境不触发）+ LLM 二判目录兜底；语义层暂无训练样本（回流后补，
> README 第 5 节流程）。写操作过确认门（ActionExecutor `register_member`）。
> 主动建议入口归 NBA 轴（START_ONBOARDING，stage-33 需求 1.3 节）。

### CHITCHAT 域（2）

| 意图码 | 描述 | 风险 | 优先级 |
|---|---|---|---|
| CHITCHAT.GENERAL | 闲聊/打招呼（含别名 GREETING） | L0 | 90 |
| CHITCHAT.THANKS | 感谢 | L0 | 90 |

### META 域（8）

| 意图码 | 描述 | 上下文约束 | 风险 | 优先级 |
|---|---|---|---|---|
| META.ABORT | 放弃/撤回当前操作（终态） | — | L0 | **10** |
| META.CORRECTION | 纠正系统理解（换商品/改说法），继承有效槽位 | 有 active_task | L0 | **20** |
| META.TRANSFER_HUMAN | 要求转人工 | — | L0 | **30** |
| META.CONFIRM ★新增 | 确认执行（"确认/是的/对/就这样"） | **仅 CONFIRMING 状态有效** | 随任务 | **40** |
| META.DENY ★新增 | 否认/拒绝执行（"不对/不要/先不"） | **仅 CONFIRMING 状态有效** | L0 | **40** |
| META.SLOT_ONLY | 裸槽位输入（只发订单号/手机号） | 有 active_task 才续接 | L0 | 50 |
| META.BOT_IDENTITY | 询问是否真人/机器人 | — | L0 | 90 |
| META.UNKNOWN | 兜底：无法识别/超出业务范围 | — | L0 | **100（最低）** |

> **META.CONFIRM / META.DENY 设计说明**：这是 v2 修复确认门死循环的关键。
> 二者是**上下文敏感意图**：分类器必须接收 `current_state` 与 `active_task` 作为输入，
> 只有当对话处于 `CONFIRMING` 状态时才允许输出，其余场景下「好的/是的」应落入
> CHITCHAT 或其它意图。它们不对应独立 Skill 文件，由确认门解析逻辑
> （Stage 03 规则版 → Stage 05 ConfirmationResponseParser）处理。

---

## 4. 判定优先级（机读规则）

分类器（规则版与 LLM 版一致）按 priority 升序判定，先命中先返回：

```text
10  META.ABORT            —— 用户随时可以放弃，压倒一切
20  META.CORRECTION       —— 纠正比继续执行重要（需 active_task）
30  META.TRANSFER_HUMAN   —— 转人工请求不得被业务流程吞掉
40  META.CONFIRM / DENY   —— 仅 CONFIRMING 状态判定
50  META.SLOT_ONLY        —— 仅 active_task 等槽位时续接
60  写操作业务意图         —— 高风险优先于读操作（退款优先于查订单）
70  读操作业务意图
80  FAQ.GENERAL           —— 业务意图都不命中时才考虑知识问答
90  CHITCHAT / BOT_IDENTITY
100 META.UNKNOWN          —— 兜底，永远最后
```

**关键裁决规则（解决 v1 的误判）**：

1. `META.ABORT` 的触发词必须**不带业务宾语**：「算了」「不用了」「没事了」→ ABORT；
   「取消订单」「我要取消这单」「帮我取消一下订单」→ `ORDER.CANCEL`（宾语是订单）。
   规则实现：先匹配 `取消.{0,4}订单|订单.{0,4}取消` → ORDER.CANCEL，再匹配裸「取消/算了」→ ABORT。
2. `META.CONFIRM/DENY` 只在 `current_state == CONFIRMING` 时判定；
   在 CONFIRMING 状态下命中其它**业务意图**（如「先帮我查下物流」）→ 挂起当前任务或按新任务处理（见状态机规范）。
3. `META.SLOT_ONLY` 在无 active_task 时降级为 `META.UNKNOWN` 处理（现行为保持）。

---

## 5. 风险等级（risk_level，机读字段）

| 等级 | 含义 | 强制要求 |
|---|---|---|
| **L0** | 无风险：公开信息问答、闲聊 | 无 |
| **L1** | 读敏感数据：订单/物流/优惠券等个人数据 | 必须持有 customer_phone_or_order_id 等身份线索才可查询；日志脱敏 |
| **L2** | 写操作-工单类：投诉单、报修单、下单引导 | 落决策日志；确认门可简化（单次确认或告知式） |
| **L3** | 写操作-资金/履约类：退款、退货、换货、取消订单、改地址、开发票 | **强确认门**（明确复述要执行的内容+用户明确确认）；LLM 无权直接执行；决策日志全量留痕；执行结果可审计 |

对应 Skill schema 新增字段（见 `00_skill_schema.md` v2）：

```yaml
risk_level: L3        # L0 / L1 / L2 / L3
priority: 60          # 判定优先级，见 taxonomy 第 4 节
```

---

## 6. 意图边界裁决表（易混淆三组）

### 6.1 退款 / 退货 / 取消订单

| 用户表达特征 | 意图 | 依据 |
|---|---|---|
| 「取消订单」「不想要了，还没发货」 | ORDER.CANCEL | 履约未开始，诉求是终止订单 |
| 「退货」「东西收到了要退回去」 | AFTERSALE.RETURN | 已收货，需寄回商品 |
| 「退款」「把钱退我」（未明确是否寄回） | AFTERSALE.REFUND | 诉求是钱；是否需退货由 Skill 内工具查询后分流 |

分类阶段**只按字面诉求归类**，不猜订单状态；订单实际状态（已发货的取消 → 引导走退货）由 Skill 运行时用工具结果分流。这条边界规则必须写进 LLM 分类器的 few-shot。

### 6.2 订单状态 / 物流轨迹 / 送达时间

| 用户表达特征 | 意图 |
|---|---|
| 「我的订单什么情况/付款了吗/发货了吗」 | ORDER.QUERY_STATUS |
| 「快递到哪了/物流信息」 | LOGISTICS.TRACK |
| 「什么时候发货/什么时候能到」 | LOGISTICS.DELIVERY_TIME |

三者共享槽位 `customer_phone_or_order_id`，续接补槽时任务不互换。

### 6.3 商品信息 / 知识库问答

| 用户表达特征 | 意图 |
|---|---|
| 针对**具体商品**的参数/介绍/价格/库存 | PRODUCT.ASK_* （工具优先，RAG 增强） |
| **平台级政策/规则**：退换货政策总述、运费政策、保修条款、会员积分规则 | FAQ.GENERAL（纯 RAG） |

判据：问题是否绑定具体商品/订单。绑定 → PRODUCT/ORDER 域；不绑定 → FAQ。

---

## 7. 统一槽位字典（Slot Dictionary）

所有 Skill 文件与代码的槽位命名以本表为准：

| 槽位名 | 类型 | 说明 | 来源 |
|---|---|---|---|
| customer_phone_or_order_id | string | 订单号或下单手机号（用户提供的订单定位线索） | 用户输入 |
| product_id | string | 商品 ID（用户点选/上下文继承） | 用户输入/上下文 |
| product_ids | list | 商品 ID 列表（对比场景，≥2） | 用户输入 |
| product_name | string | 商品名称（口语提及，需解析为 product_id） | 用户输入 |
| sku_attr | string | 规格属性（颜色/尺寸/版本） | 用户输入 |
| quantity | int | 数量 | 用户输入 |
| color | string | 颜色（sku_attr 的细分，Stage 03 遗留，逐步并入 sku_attr） | 用户输入 |
| phone | string | 手机号（单独出现时，可填充 customer_phone_or_order_id） | 用户输入 |
| new_address / new_receiver_name / new_receiver_phone | string | 改地址场景新收货信息 | 用户输入 |
| refund_reason / return_reason / exchange_reason | enum | 售后原因枚举 | 用户输入 |
| invoice_type / invoice_title / tax_id | string/enum | 发票信息 | 用户输入 |
| complaint_content | string | 投诉内容 | 用户输入 |
| issue_description | string | 故障描述 | 用户输入 |
| budget / use_scene / preference | string | 推荐场景偏好 | 用户输入 |
| destination_region / order_amount | string/number | 运费计算参数 | 用户输入 |
| user_id | string | 用户标识 | **系统注入（请求上下文），不向用户询问** |

**占位符来源约定**（修复 v1「confirmation_prompt 引用未定义字段」问题）：
Skill 的 `confirmation_prompt` / 回复模板中的占位符只能来自三种源，且必须在 Skill 中声明：

1. `slots` 中定义的用户槽位；
2. `tool_returns`：工具返回字段（v2 schema 新增段，如 `query_order` 返回 `order_id / product_name / amount / tracking_number`）；
3. 系统上下文（`user_id`、`tenant_id` 等，schema 白名单）。

---

## 8. 分类器演进路线

| 阶段 | 分类器 | 说明 |
|---|---|---|
| Stage 03（已实现） | RuleIntentClassifier | 关键词+正则；按第 4 节优先级排序；含 CONFIRM/DENY 上下文判定与 ORDER.CANCEL |
| Stage 04-02（SetFit v1） | HybridIntentClassifier | 三层：规则控制层（META/确认门/取消订单，确定性短路）→ **SetFit 语义层**（29 类，本地模型，置信度阈值兜底 UNKNOWN）→ 模型不可用降级规则全表。训练数据见 `docs/intent/README.md`，需求见 `stage-04-llm-integration/02_setfit_intent_classifier.md` |
| Stage 04 后续 | + LLM 难例二判 | SetFit 低置信样本交 LLM few-shot（含第 6 节裁决表）二次判定，成本只花在难例上 |
| Stage 06+ | + 向量召回辅助 | FAQ/长尾表达用 embedding 相似度召回候选意图，供排序 |

**上下文敏感意图（META.CONFIRM/DENY/SLOT_ONLY/CORRECTION）永远由规则+状态机判定，不进语义模型**——
它们的语义由对话状态决定，模型只看单句必然误判。

分类结果结构（保持 v1 兼容）：`{pred_label, confidence, decision_source, top_k}`；
`decision_source` 枚举：`RULE_KEYWORD / RULE_SLOT_ONLY / RULE_CONFIRM_GATE / RULE_FALLBACK /
SETFIT / SETFIT_LOW_CONF / SETFIT_FALLBACK_RULE / LLM / LLM_FALLBACK / VECTOR_ASSIST`。

---

## 9. 评估要求（Stage 04 起强制）

- 每个意图至少 10 条标注样例（正例 + 易混淆负例），存放于 `docs/testing/intent_eval_set.md`（Stage 04 创建）。
- 第 6 节三组易混淆边界必须有专门的对抗样例（如「取消订单」vs「算了」）。
- 每次修改分类器（规则词表 / prompt / 模型）必须跑评估集，域级准确率不得回退。

---

## 10. 已知缺口备忘（新增意图/数据前先查此处）

| # | 缺口 | 现状与处置方向 |
|---|---|---|
| 1 | **缺「修改订单商品/数量」意图**（「把数量改成两件」） | 训练数据 v41 的 ORDER.CHANGE_INFO 里混有此类样本，v42 暂并入 ORDER.CHANGE_ADDRESS。新增 `ORDER.CHANGE_ITEM` 时按第 9 节流程：注册→补数据（≥300 条）→重训→补对抗样例 |
| 2 | **「政策疑问 vs 售后动作」边界 bad case**：「七天无理由退货是什么意思」→ 模型判 RETURN(0.62) 而非 FAQ.GENERAL | 走安全兜底（确认门/可退出），无风险执行。回流方向：补「X是什么意思/什么规定」句式的 FAQ 对抗样本后重训（数据集规范第 5 节） |
| 3 | **口语化物流长尾低置信**（「东西还没到我等急了」→ UNKNOWN） | 已由 LLM 难例二判部分缓解（需配 Key）；从 decision_log 的 SETFIT_LOW_CONF 记录持续回流 |
| 4 | 会员/积分账户操作、催发货工单、预售定金、门店自提等意图未注册 | 按业务需要时走第 9 节新增流程 |

---

## 变更记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-07-02 | v2 | 首版规范：统一三套意图命名；新增 FAQ.GENERAL、META.CONFIRM/DENY、META.UNKNOWN 注册；补 LOGISTICS/FAQ 域；新增 priority / risk_level 机读字段与边界裁决表 |
| 2026-07-02 | v2.1 | 分类器路线更新（SetFit 语义层 + LLM 难例二判已实现）；新增第 10 节已知缺口备忘 |
