---
skill_id: PRODUCT.COMPARE
name: 商品对比
domain: PRODUCT
description: 用户要求对比两个或多个商品、品牌、型号之间的差异
risk_level: L0
priority: 70

triggers:
  intents:
    - PRODUCT.COMPARE

required_tools:
  - tool_id: query_product_info
    purpose: 查询每款商品的详情用于对比
    required_slots: [compare_items]
    optional: false
    call_mode: batch              # 对每款各查一次商品库，结果合并

# Stage 32 实做：product_ids 改 compare_items（「A和B」整段捕获后节点内
# 切分——用户不知道商品 ID）；运行时槽位/模板以代码注册表（registry.py）为准
slots:
  - name: compare_items
    description: 要对比的两款商品名称（「A和B」「A vs B」，抽取规范为「A|B」）
    ask_prompt: "您想对比哪两款商品？例如「凉风X1 和 凉风X2」"
    required: true
    type: string

actions: []

constraints:
  max_tool_calls: 3              # 最多对比3款，防止 prompt 过长
  requires_human_if: "用户要求对比的商品超过3款或工具返回数据不足以支撑对比"
  forbidden:
    - "不得主观评价哪款更好（除非用户明确问「哪个好」，再结合用户需求给建议）"
    - "不得编造两款商品的差异参数"
    - "超过3款对比不要强行对比，建议用户缩小范围"

response_format:
  max_messages: 2
  style: "对比表格或分点说明；最后可加一句根据用户已知需求的建议（非强推）"
---

## 当前场景：商品对比

**标准流程**：

1. 确认要对比的商品（从上下文继承或追问）
2. 并行查询各商品详情
3. 找出用户关心的维度（价格/材质/功能/适用场景），聚焦对比
4. 不堆砌所有参数，只对比用户在意的维度

**对比维度选择**：

- 用户指定了维度（「哪个便宜」「哪个质量好」）→ 只对比这个维度
- 用户未指定 → 选 2-3 个最关键维度（价格、核心功能、适用人群）

**「哪个更好」**：

不直接评价，而是根据用户已知需求给建议：
- 「如果您更在意价格，A 款更合适；如果重视 [核心功能]，B 款更适合」
- 不说「A 款比 B 款好」这种绝对评价

**超出3款**：

「同时对比这么多款不太好区分，建议您先告诉我最在意的是哪方面，我帮您缩小选择范围」
