---
capability_id: promotion_guide
status: implemented
implemented_by:
  modules: [app/chat/proactive/campaigns.py]
  configs: [configs/campaigns.example.json]
  metrics: [proactive_actions_total]
---

# PROMOTION_GUIDE Skill

## 目标

回答用户主动询问的活动问题，或在严格资格、相关性、频控和抑制条件下提示一次相关活动。

## 两种入口

### 用户主动

意图：

- `PROMOTION.QUERY`
- `PROMOTION.ELIGIBILITY`
- `PROMOTION.USAGE_HELP`

用户主动入口优先回答，不受“主动营销展示次数”限制，但仍需事实校验。

### 系统主动

只能由 NBA 输出 `MENTION_CAMPAIGN`，且通过全部抑制规则。

## 活动事实

必须来自 Campaign Registry / Tool：

```text
campaign_id
title
valid_from
valid_to
eligible_customer_segments
eligible_products
benefit
usage_conditions
stacking_rules
region_scope
inventory_or_quota
```

## 抑制

- 投诉、退款、取消、人工接管；
- 强负面情绪；
- 用户已拒绝；
- 冷却未结束；
- 活动与当前商品/需求无关；
- 活动已过期；
- 资格不可验证；
- 当前回复已经复杂；
- SOCIAL_ONLY 且无商品上下文。

## 禁止话术

- 虚假“马上没货”；
- 虚假倒计时；
- 夸大优惠；
- 隐藏使用门槛；
- 将活动说成用户一定符合。

## 输出

```json
{
  "campaign_id": "summer_2026",
  "eligible": true,
  "facts": {
    "benefit": "满3000减200",
    "valid_to": "2026-08-31T23:59:59+08:00"
  },
  "required_disclosures": [
    "仅限指定型号",
    "不可与员工折扣叠加"
  ]
}
```
