---
skill_id: PRODUCT.ASK_INFO
name: 商品介绍查询
domain: PRODUCT
description: 用户询问商品介绍、参数、功能、卖点、材质、使用方式等
risk_level: L0
priority: 70

triggers:
  intents:
    - PRODUCT.ASK_INFO
    - PRODUCT.ASK_ATTR            # 问明确属性（颜色/尺寸/规格）复用本 Skill，槽位 attr_type 区分

required_tools:
  - tool_id: query_product_info
    purpose: 查询商品详情、参数、属性、卖点描述
    required_slots: [product_id]
    optional: true                # 有 product_id 更精准；无则从 RAG 知识库检索

slots:
  - name: product_id
    description: 商品名称、型号或编号
    ask_prompt: "您想了解哪款商品的详情呢？"
    required: false
    inherit_from_context: true    # 优先从 context_stacks.recent_products 继承
  - name: attr_type
    description: 用户关心的具体属性（颜色/尺寸/材质/容量/规格等）
    ask_prompt: "您想了解这款商品的哪个方面呢？"
    required: false               # 无 attr_type 则返回通用介绍

actions: []

constraints:
  max_tool_calls: 1
  requires_human_if: "用户问的属性工具返回里没有，且 RAG 也无法覆盖"
  forbidden:
    - "不得编造未经工具或知识库确认的参数值（如容量、材质成分）"
    - "不得主动推荐竞品（除非走 PRODUCT.COMPARE 意图）"
    - "不得在没有依据时声称「这款是最好的」"

response_format:
  max_messages: 2
  style: "先回答用户关心的属性，再可选附上卖点；专业但不堆砌参数"
---

## 当前场景：商品介绍 / 属性查询

**有 product_id 时**：

从工具返回的详情中提取用户关心的内容：
- 问材质 → 只说材质，不堆所有参数
- 问尺寸 → 只说尺寸，适当说明适配场景
- 泛问介绍 → 说 2-3 个最核心卖点，不念读参数表

**无 product_id 时**：

先从 context_stacks.recent_products 里看最近聊过的商品是否匹配，
匹配则直接用，不再追问；不匹配再问用户「您说的是哪款？」

**PRODUCT.ASK_ATTR 场景**（问具体属性）：

用户说「这个有没有白色」「最大容量是多少」：
- 先看 product_id 是否已知（从上下文继承）
- 已知 → 直接查 attr_type 对应的属性返回
- 未知 → 问「您说的是哪款商品？」

**禁止**：
- 无工具返回时不猜具体参数值（「应该有白色」是错的，要说「帮您确认一下」）
- 不堆砌所有参数，只回答用户问的那个维度
