---
capability_id: product_recommendation
status: implemented
implemented_by:
  intents: [PRODUCT.RECOMMEND]
  modules: [app/product/provider.py]
---

# PRODUCT_RECOMMENDATION Skill

## 目标

基于结构化需求产生 2～4 个可解释、可核验的商品候选。

## 输入

- Product Discovery 产物；
- 商品结构化事实；
- 实时价格；
- 实时库存；
- 地区可售；
- 活动资格；
- 用户历史偏好（可选）。

## 流程

```text
VALIDATE_REQUEST
→ RECALL_CANDIDATES
→ HARD_FILTER
→ SCORE_AND_RANK
→ DIVERSIFY
→ VERIFY_FACTS
→ BUILD_EXPLANATIONS
→ PRESENT
```

## 硬约束

不满足任一项即过滤：

- 品类；
- 预算上限；
- 地区可售；
- 库存；
- 必要规格；
- 兼容性；
- 禁售限制；
- 用户明确排除条件。

## 初始规则排序

```text
需求匹配度       45%
预算适配度       15%
核心功能匹配     15%
配送与库存       10%
用户偏好         10%
商业权重          5%
```

商业权重不得覆盖硬约束，也不得让明显低匹配商品排在高匹配商品之前。

## 输出

```json
{
  "items": [
    {
      "product_id": "A3",
      "rank": 1,
      "score": 0.89,
      "reasons": [
        "适合 15-20 平方米",
        "价格在预算内",
        "支持低噪音模式"
      ],
      "tradeoffs": [
        "不支持新风"
      ],
      "fact_snapshot": {
        "price": 2899,
        "stock_status": "IN_STOCK"
      }
    }
  ]
}
```

## LLM 边界

LLM 只能把结构化理由表达成自然语言，不得：

- 增删商品参数；
- 生成价格和库存；
- 虚构促销；
- 重新排列未授权候选；
- 隐藏明确 trade-off。
