---
skill_id: FAQ.GENERAL
name: 平台政策与通用知识问答
domain: FAQ
description: 不绑定具体订单/商品的平台级问题：退换货政策总述、运费政策、保修条款、会员积分规则、门店信息等（RAG 主入口，Stage 06 生效）
risk_level: L0
priority: 80

triggers:
  intents:
    - FAQ.GENERAL

required_tools:
  - tool_id: kb_retrieve
    purpose: 知识库混合检索（FAQ 精确层 + 文档层，见 stage-06 需求）
    required_slots: []
    optional: false                  # 无检索结果时必须拒答或转人工，不得依赖模型自身知识回答

slots:
  - name: topic
    description: 问题主题（用于检索过滤，可选）
    ask_prompt: ""
    required: false
    type: string

actions: []

constraints:
  max_tool_calls: 1
  requires_human_if:
    - "检索无命中或相似度低于拒答阈值"
    - "用户对标准答案表示不认可并要求进一步解释"
  forbidden:
    - "检索无结果时不得用模型自身知识编造政策条款"
    - "不得给出与检索片段冲突的答案"
    - "涉及金额/时效的政策表述必须与知识库原文一致，不得改写数字"

response_format:
  max_messages: 1
  style: "直接给答案 + 附来源；答案来自 FAQ 精确命中时用标准答案原文轻润色"
---

## 当前场景：平台政策与通用知识问答

**FAQ 精确命中（相似度 ≥ 阈值）**：

直接使用运营维护的标准答案，可做口语化润色但不得改动事实与数字。

**文档检索命中**：

只依据检索片段回答，逐点对应，末尾附来源（文档标题）。
「根据我们的退换货政策：签收后 7 天内、商品完好可申请无理由退货，退货运费由…（来源：《退换货政策》）」

**检索无命中 / 低于拒答阈值**：

「这个问题我暂时没有准确的资料，为了不误导您，我帮您转人工确认，或者您可以换个说法再问我一次。」
→ status=FALLBACK，不编造。

**与 PRODUCT / ORDER 域的边界**（见 taxonomy 6.3）：

- 问题绑定具体商品 →「这款手机保修几年」优先 PRODUCT.ASK_INFO（工具+RAG 增强）
- 问题绑定具体订单 →「我这单能退吗」优先 AFTERSALE 域（要查订单状态）
- 平台级泛问 →「你们退货政策是什么」→ 本 Skill
