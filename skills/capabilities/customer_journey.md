---
capability_id: customer_journey
status: implemented
implemented_by:
  modules: [app/services/journey_service.py]
  metrics: [journey_transitions_total]
---

# CUSTOMER_JOURNEY Skill

## 目标

维护跨会话的客户阶段，为 NBA 提供长期上下文，但不直接驱动写操作。

## 阶段

```text
VISITOR
NEW_USER
REGISTERING
REGISTERED
DISCOVERING
CONSIDERING
READY_TO_BUY
PURCHASED
REPEAT_CUSTOMER
AFTER_SALES
AT_RISK
```

## 更新证据

强证据：

- 注册成功；
- 订单创建；
- 支付成功；
- 收货；
- 售后工单；
- 用户明确拒绝继续购买。

弱证据：

- 多次询价；
- 多次比较；
- 表达购买犹豫；
- 浏览或聊天兴趣。

弱证据不能单独推动高风险阶段跳转。

## 输出

```json
{
  "from_stage": "DISCOVERING",
  "to_stage": "CONSIDERING",
  "confidence": 0.84,
  "evidence_codes": [
    "compared_multiple_products",
    "budget_confirmed"
  ]
}
```

## 防抖

- 同一会话最多更新一次；
- 低置信更新只落候选，不正式写入；
- 回退阶段需要强证据；
- 购买阶段必须由订单事实确认。
