---
skill_id: PRODUCT.RECOMMEND
name: 商品推荐
domain: PRODUCT
description: 用户请求推荐商品、不知道选哪款、让客服帮忙推荐
risk_level: L0
priority: 70

triggers:
  intents:
    - PRODUCT.RECOMMEND

required_tools:
  - tool_id: query_product_list
    purpose: 查询符合条件的商品列表
    required_slots: []            # 无需槽位也能调用（返回热销/推荐列表）
    optional: true
    filter_by_slots: [budget, use_scene, preference]  # 有槽位时过滤

slots:
  - name: budget
    description: 用户预算
    ask_prompt: "您大概的预算范围是多少？"
    required: false               # 渐进式收集，不强制一次问完
  - name: use_scene
    description: 使用场景（送礼/自用/办公/运动等）
    ask_prompt: "请问是自用还是送礼？有什么特别的场合或需求吗？"
    required: false
  - name: preference
    description: 用户偏好（风格/颜色/品牌等）
    ask_prompt: "您对风格或颜色有什么偏好吗？"
    required: false

actions: []

constraints:
  max_tool_calls: 1
  max_recommendations: 3         # 最多推荐3款，不做选择困难
  requires_human_if: "用户有非常具体的定制化需求或预算超出系统商品范围"
  forbidden:
    - "不得一次推荐超过3款（越多越让人纠结）"
    - "不得每款都说「这款非常好」（空洞赞美）"
    - "不得推荐工具里没有的商品"

response_format:
  max_messages: 2
  style: "推荐1-3款，每款一句核心卖点 + 适合谁；结尾加一个引导下一步的问题"
---

## 当前场景：商品推荐

**渐进式槽位收集**（不一次性追问所有槽位）：

第1轮：先问最关键的一个（通常是预算或使用场景）
第2轮：根据第1轮回答进一步询问或直接推荐
不要第一句就问「预算是多少？用途是什么？风格偏好？」（三问连发令人反感）

**推荐策略**：

- 有槽位信息 → 精准推荐 1-2 款
- 无槽位信息 → 推荐 2-3 款主力款，涵盖不同价位段
- 推荐时说「为什么适合你」而不是只说「这款很好」

**推荐示例**（好 vs 差）**：

差：「这三款都很好，您可以参考一下」

好：
「根据您送礼的需求，给您推荐两款：
A款：[核心卖点]，适合正式商务场合，价格 XXX
B款：[核心卖点]，外观更时尚，适合年轻人，价格 XXX
您更倾向哪个方向？」

**用户已有倾向时**：

不要覆盖用户的偏好，顺着推荐：
「您喜欢 [用户提到的风格]，那 [推荐款] 挺合适的，[核心理由]」
