# PURCHASE_ASSIST Skill

## 目标

帮助已经接近购买的用户降低决策成本、解决最后疑虑并完成下一步，不采用压力销售。

## 进入条件

- 用户主动询问怎么买、如何下单；
- Journey 为 `CONSIDERING` 或 `READY_TO_BUY`；
- 已有明确候选商品；
- NBA 推荐后用户接受购买帮助。

## 支持动作

- 对比候选；
- 解释真实差异；
- 回答配送、安装、退换政策；
- 核验库存和到货时间；
- 核验可用活动；
- 提供购买入口；
- 收集必要信息；
- 生成下单 ActionRequest。

## 不允许

- 用户仍在投诉时促成交；
- 未确认商品时直接下单；
- 使用虚假紧迫感；
- 隐藏更适合但利润更低的商品；
- 用户说“不买了”后继续追问；
- LLM 直接执行订单创建。

## 步骤

```text
IDENTIFY_BLOCKER
→ RESOLVE_BLOCKER
→ CONFIRM_PRODUCT
→ VERIFY_PRICE_STOCK
→ OPTIONAL_PROMOTION_CHECK
→ OFFER_NEXT_STEP
→ CONFIRM_ORDER_ACTION
→ HANDOFF_TO_ACTION_EXECUTOR
```

## 典型 blocker

```text
PRICE
FEATURE_UNCERTAINTY
COMPATIBILITY
DELIVERY_TIME
RETURN_POLICY
TRUST
PAYMENT_METHOD
```

每次只解决真实 blocker，不固定套用促销话术。
