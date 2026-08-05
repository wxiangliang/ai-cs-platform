# Skill 定义规范（Schema v2）

> 这份文档定义一个 Skill 的完整结构，作为后续所有 Skill 文件的参考标准。
> **意图码、域枚举、优先级与风险等级以 `docs/chat/intent_taxonomy.md` 为单一事实来源**，
> 本文档只定义 Skill 文件的承载格式。
>
> v2 变更：域枚举补 LOGISTICS / FAQ；新增机读字段 `risk_level`、`priority`；
> 新增 `tool_returns` 段（声明确认话术占位符的来源）；`requires_human_if` 统一为列表。

---

## Skill 是什么

**一个 Skill = 处理某一类用户意图所需的全部能力描述。**

它不是代码，是给系统的**配置声明**，告诉系统：
- 哪些意图触发这个 Skill（`triggers`）
- LLM 在这个场景下应该怎么说话（`prompt_fragment`）
- 需要哪些数据工具（`required_tools`）
- 是否有写操作及其确认要求（`actions`）
- 槽位定义（`slots`，需要从用户话语里收集什么信息）

---

## Skill 文件结构

每个 Skill 是一个 YAML front-matter + Markdown body 的 `.md` 文件：

```markdown
---
# ============ 元数据 ============
skill_id: DOMAIN.ACTION              # 对应 domain.action 标签，唯一标识，必须已在 intent_taxonomy.md 注册
name: "人类可读名称"
domain: DOMAIN                       # PRODUCT / ORDER / LOGISTICS / AFTERSALE / PAYMENT / PROMOTION / FAQ / META / CHITCHAT
description: "一句话说明这个 Skill 处理什么"
risk_level: L3                       # L0 无风险 / L1 读敏感 / L2 写-工单 / L3 写-资金履约（见 taxonomy 第 5 节）
priority: 60                         # 意图判定优先级，数字越小越先判定（见 taxonomy 第 4 节）

# ============ 触发条件 ============
triggers:
  intents:                           # 哪些 domain.action 激活本 Skill
    - DOMAIN.ACTION_A
    - DOMAIN.ACTION_B                # 多个意图可共享一个 Skill
  slot_required: false               # 触发前是否必须先收集某个槽位

# ============ 所需工具（Layer 2）============
required_tools:
  - tool_id: query_order             # MCP Tool 的 ID
    purpose: "查询订单状态和物流"
    required_slots: [order_id]       # 调用此工具前必须有的槽位值
    optional: false                  # false = 没有这个工具结果就不能回复

# ============ 槽位定义（Layer 2 附属）============
slots:
  - name: order_id
    description: "订单号或下单手机号"
    ask_prompt: "请告诉我您的订单号或下单时使用的手机号"
    required: true                   # 工具调用前必须收集
    type: string
    validation: "11位手机号或字母数字混合订单号"

# ============ 工具返回字段（v2 新增）============
# 声明工具会返回、可在 confirmation_prompt / 回复模板中引用的字段。
# 模板占位符只能来自：slots ∪ tool_returns ∪ 系统上下文白名单（user_id/tenant_id），
# Skill Loader（Stage 05）启动时校验，来源不明的占位符直接报错。
tool_returns:
  - name: order_id                   # query_order 解析 customer_phone_or_order_id 后返回
    from_tool: query_order
  - name: product_name
    from_tool: query_order
  - name: amount
    from_tool: query_order

# ============ 写操作（Layer 3）============
actions:
  - action_id: create_aftersale_ticket
    description: "创建售后工单"
    requires_confirmation: true      # true = 执行前必须用户明确确认
    confirmation_prompt: "我将为您创建退款申请，订单号 {order_id}，退款金额 {amount}，确认吗？"
    rollback: false                  # 是否可撤销

# ============ 执行约束 ============
constraints:
  max_tool_calls: 2                  # 本 Skill 最多调用几次工具
  requires_human_if:                 # 什么情况下必须转人工（v2 统一为列表）
    - "工具返回异常状态"
  forbidden:                         # 绝对不能做的事（在 prompt 里强调）
    - "不得承诺具体赔偿金额"
    - "不得在工具未返回前确认退款"

# ============ RAG 兜底（v2 新增，可选）============
rag_fallback: false                  # true = 工具无结果时转知识库检索（Stage 06 生效）

# ============ 响应格式 ============
response_format:
  max_messages: 2                    # 最多分几条回复
  style: "安抚+核实"                 # 给 LLM 的格式提示
---

# Prompt 片段（Layer 1）
<!-- 这里是注入 system prompt 的文本，LLM 直接读取 -->

## 当前场景：{skill_name}

{prompt_content}
```

---

## Skill 三层能力说明

```
Layer 1：Prompt 片段
  作用：告诉 LLM "在这个意图下怎么说话"
  内容：语气要求、禁忌规则、响应风格
  加载时机：每轮命中该 Skill 时注入 system prompt
  大小目标：10~30 行，不超过 50 行

Layer 2：数据能力
  作用：声明"需要哪些工具和槽位"
  内容：required_tools + slots 定义
  使用方：SlotFiller（收集槽位）+ ActionExecutor（调用工具）
  LLM 不直接决定调哪个工具，由系统按声明自动路由

Layer 3：写操作
  作用：声明"能做什么操作，以及确认要求"
  内容：actions 列表，每个 action 有 requires_confirmation 标志
  使用方：ActionExecutor，写操作前强制走 ConfirmationResponseParser
  LLM 无权绕过确认门直接执行写操作
```

---

## Skill 激活流程

```
用户输入
  ↓
意图分类器 → domain.action（如 AFTERSALE.REFUND）
  ↓
Resolver 查 Skill 注册表 → 找到 aftersale_refund_skill
  ↓
Layer 1：skill.prompt_fragment 注入 system_prompt
Layer 2：检查 required_tools → 看 slots 是否满足
  ├── 槽位缺失 → SlotFiller 收集（不调 LLM 生成回复）
  └── 槽位齐全 → 调用 required_tools
Layer 3：有写操作 → ConfirmationResponseParser
  ├── 用户确认 → ActionExecutor 执行
  └── 用户拒绝/修改 → 回到槽位填充或澄清
  ↓
LLM 生成最终回复（此时已有工具结果作为上下文）
```

---

## 文件组织

Skill 文件统一存放于**仓库根 `skills/`**（2026-08-05 起：运行时加载物不放 docs；
schema 规范与 guardrails 规则库仍在本目录），文件名 = `skill_id`.md，
完整清单见同目录 `README.md`（31 个），意图码以 `docs/chat/intent_taxonomy.md` 注册表为准。
Stage 05 起由 `app/chat/skills/loader.py` 在启动时加载并校验。

> 注意：旧版本此处列过 `PRODUCT.ASK_SPEC / ORDER.QUERY_LOGISTICS / META.IDENTITY / PAYMENT.DISCOUNT`
> 等文件名，属已废弃别名（对照表见 taxonomy 2.1 节），不要再使用。
