# PRODUCT_DISCOVERY Skill

## 目标

把用户的模糊购买需求转成结构化约束，并生成可供推荐系统使用的需求画像。

## 进入条件

- `PRODUCT.SELECTION_HELP`；
- 用户说“不知道选哪款”；
- 用户主动接受选品帮助；
- NBA 判断购买意图明确但关键约束缺失，并得到用户同意。

## 必填槽位

按品类动态声明，通用字段：

```text
category
budget_range
usage_scenario
```

可选字段：

```text
quantity
brand_preference
must_have_features
nice_to_have_features
excluded_features
size_or_capacity
delivery_deadline
region
```

## 步骤

```text
IDENTIFY_CATEGORY
→ COLLECT_HARD_CONSTRAINTS
→ COLLECT_PREFERENCES
→ CONFIRM_REQUIREMENTS
→ REQUEST_RECOMMENDATION
→ PRESENT_OPTIONS
→ REFINE_OR_COMPLETE
```

## 原则

1. 优先询问对候选集合影响最大的槽位。
2. 一轮最多询问两个高度相关问题。
3. 已从对话、画像或商品上下文获得的值不重复询问。
4. 用户不愿继续回答时，用已有约束给出“有限可信度”的候选。
5. 推荐结果必须暴露 trade-offs，不只说优点。

## 与任务栈关系

选品期间出现售后高优先级意图：

```text
挂起 PRODUCT_DISCOVERY
→ 处理新任务
→ 完成后恢复原 Playbook
```

恢复时总结已知需求，不从头询问。

## 产物

```json
{
  "category": "air_conditioner",
  "hard_constraints": {
    "budget_max": 3000,
    "room_area_m2": 18
  },
  "preferences": {
    "low_noise": true
  },
  "confidence": 0.86,
  "missing_optional_slots": []
}
```
