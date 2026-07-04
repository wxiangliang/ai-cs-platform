# Skills 设计参考文档

> 所有 Skill 文件均为 YAML front-matter + Markdown body 格式，定义见 `00_skill_schema.md`（v2）。
> 意图码、优先级、风险等级的单一事实来源是 `docs/chat/intent_taxonomy.md`。

---

## 完整 Skill 清单（31 个文件）

> 历史版本此处曾写「27 个」但实际文件数不符；v2 起清单与目录文件严格一一对应：
> 31 = PRODUCT 5 + ORDER 4 + LOGISTICS 3 + AFTERSALE 5 + PAYMENT 3 + PROMOTION 2 + FAQ 1 + META 6 + CHITCHAT 2。

### PRODUCT 域（5个）

| 文件 | skill_id | 说明 | 三层情况 |
|---|---|---|---|
| `PRODUCT.ASK_INFO.md` | PRODUCT.ASK_INFO | 商品介绍/规格/属性查询 | L1 + L2(可选) |
| `PRODUCT.ASK_STOCK.md` | PRODUCT.ASK_STOCK | 库存查询 | L1 + L2(必须) |
| `PRODUCT.ASK_PRICE.md` | PRODUCT.ASK_PRICE | 询价/议价 | L1 + L2(可选) |
| `PRODUCT.COMPARE.md` | PRODUCT.COMPARE | 商品对比 | L1 + L2(必须,batch) |
| `PRODUCT.RECOMMEND.md` | PRODUCT.RECOMMEND | 推荐选购 | L1 + L2(可选) |

### ORDER 域（4个）

| 文件 | skill_id | 说明 | 三层情况 |
|---|---|---|---|
| `ORDER.QUERY_STATUS.md` | ORDER.QUERY_STATUS | 订单状态/物流查询 | L1 + L2(必须) |
| `ORDER.CREATE.md` | ORDER.CREATE | 下单意向引导 | L1 + L2(可选) + L3(无确认) |
| `ORDER.CANCEL.md` | ORDER.CANCEL | 取消订单 | L1 + L2(必须) + L3(有确认) |
| `ORDER.CHANGE_ADDRESS.md` | ORDER.CHANGE_ADDRESS | 修改收货地址/信息 | L1 + L2(必须) + L3(有确认) |

### LOGISTICS 域（3个）

| 文件 | skill_id | 说明 | 三层情况 |
|---|---|---|---|
| `LOGISTICS.TRACK.md` | LOGISTICS.TRACK | 物流追踪（在哪了） | L1 + L2(必须,2次) |
| `LOGISTICS.DELIVERY_TIME.md` | LOGISTICS.DELIVERY_TIME | 发货/到达时间 | L1 + L2(必须) |
| `LOGISTICS.SHIPPING_FEE.md` | LOGISTICS.SHIPPING_FEE | 运费查询 | L1 + L2(必须) |

### AFTERSALE 域（5个）

| 文件 | skill_id | 说明 | 三层情况 |
|---|---|---|---|
| `AFTERSALE.REFUND.md` | AFTERSALE.REFUND | 退款申请 | L1 + L2(必须) + L3(有确认) |
| `AFTERSALE.RETURN.md` | AFTERSALE.RETURN | 退货申请 | L1 + L2(必须,3次) + L3(有确认) |
| `AFTERSALE.EXCHANGE.md` | AFTERSALE.EXCHANGE | 换货申请 | L1 + L2(必须,3次) + L3(有确认) |
| `AFTERSALE.REPAIR.md` | AFTERSALE.REPAIR | 维修/保修 | L1 + L2(必须) + L3(有确认) |
| `AFTERSALE.COMPLAIN.md` | AFTERSALE.COMPLAIN | 投诉处理 | L1 + L2(可选) + L3(无确认) |

### PAYMENT 域（3个）

| 文件 | skill_id | 说明 | 三层情况 |
|---|---|---|---|
| `PAYMENT.METHOD.md` | PAYMENT.METHOD | 支付方式查询 | L1 + L2(必须) |
| `PAYMENT.ISSUE.md` | PAYMENT.ISSUE | 支付失败/扣款异常 | L1 + L2(必须) → 全转人工 |
| `PAYMENT.INVOICE.md` | PAYMENT.INVOICE | 发票申请 | L1 + L2(必须) + L3(有确认) |

### PROMOTION 域（2个）

| 文件 | skill_id | 说明 | 三层情况 |
|---|---|---|---|
| `PROMOTION.COUPON.md` | PROMOTION.COUPON | 优惠券查询/使用问题 | L1 + L2(必须) |
| `PROMOTION.ACTIVITY.md` | PROMOTION.ACTIVITY | 活动规则查询 | L1 + L2(必须) |

### FAQ 域（1个）★ v2 新增

| 文件 | skill_id | 说明 | 三层情况 |
|---|---|---|---|
| `FAQ.GENERAL.md` | FAQ.GENERAL | 平台政策/规则/通用知识问答（RAG 主入口，Stage 06 生效） | L1 + L2(必须, kb_retrieve) |

### META 域（6个文件）

| 文件 | skill_id | 说明 | 优先级（taxonomy 第 4 节） |
|---|---|---|---|
| `META.ABORT.md` | META.ABORT | 用户撤回（终态） | **10 最高** |
| `META.CORRECTION.md` | META.CORRECTION | 用户纠正理解 | 20，继承有效槽位 |
| `META.TRANSFER_HUMAN.md` | META.TRANSFER_HUMAN | 转人工 | 30，无确认门 |
| `META.SLOT_ONLY.md` | META.SLOT_ONLY | 裸槽位（无动作意图） | 50，填槽逻辑非意图路由 |
| `META.BOT_IDENTITY.md` | META.BOT_IDENTITY | 询问是否真人/机器人 | 90 |
| `META.UNKNOWN.md` ★v2 新增 | META.UNKNOWN | 兜底与澄清（2 轮失败转人工） | 100 最低（兜底） |

**无独立文件的 META 意图**（在 taxonomy 注册、由专门逻辑处理）：

| 意图 | 处理者 |
|---|---|
| META.CONFIRM / META.DENY ★v2 新增 | 确认门逻辑（Stage 03 规则版 → Stage 05 ConfirmationResponseParser）；仅 CONFIRMING 状态有效 |
| *(guardrails.md)* | 全局护栏，每轮必加载，非意图 |
| ~~META.CLARIFY_REPLY~~ | 已废弃（taxonomy 2.1）：澄清应答由状态机 + SLOT_ONLY/CONFIRM/DENY 覆盖 |

### CHITCHAT 域（2个）

| 文件 | skill_id | 说明 | 三层情况 |
|---|---|---|---|
| `CHITCHAT.GENERAL.md` | CHITCHAT.GENERAL | 闲聊/打招呼 | 只有 L1 |
| `CHITCHAT.THANKS.md` | CHITCHAT.THANKS | 感谢 | 只有 L1 |

---

## 三层模型速查

```
Layer 1 (prompt_fragment) → 告诉 LLM 「怎么说」
Layer 2 (required_tools)  → 告诉系统 「查什么 / 需要什么槽位」
Layer 3 (actions)         → 告诉系统 「做什么 / 需不需要用户确认」
```

| 组合 | 典型场景 | 示例 |
|---|---|---|
| 只有 L1 | 纯对话，无数据依赖 | CHITCHAT, META.ABORT |
| L1 + L2(可选) | 有工具更好，没有也能回答 | PRODUCT.ASK_INFO |
| L1 + L2(必须) | 无工具数据不能回答 | ORDER.QUERY_STATUS |
| L1 + L2 + L3(无确认) | 写操作但不需确认 | META.TRANSFER_HUMAN |
| L1 + L2 + L3(有确认) | 写操作必须用户明确同意 | AFTERSALE.REFUND, ORDER.CANCEL |

---

## 关键设计原则（从这 27 个 Skill 归纳）

1. **工具 optional=false**：无工具结果时不能瞎回复，必须追问或转人工
2. **写操作必须有确认门**：除非是「转人工」这类用户已明确的场景；确认门应答（确认/否认/修改）由 META.CONFIRM / META.DENY + ConfirmationResponseParser 处理
3. **requires_human_if 是安全阀**：遇到超出自动处理能力的情况，不要硬撑，转人工
4. **槽位 inherit_from_context**：能从上下文继承的就不要再问，减少用户重复输入
5. **META 域优先级最高**：ABORT(10) > CORRECTION(20) > TRANSFER_HUMAN(30) > CONFIRM/DENY(40) > SLOT_ONLY(50) > 业务意图，机读 priority 字段见 taxonomy 第 4 节
6. **forbidden 是红线**：比 prompt 规则更强，系统级别的硬约束
7. **检索不到就拒答**（v2 新增）：FAQ/RAG 场景禁止用模型自身知识编造，无命中转人工
8. **占位符必须有来源**（v2 新增）：模板占位符只能来自 slots ∪ tool_returns ∪ 系统上下文白名单，Loader 启动校验
