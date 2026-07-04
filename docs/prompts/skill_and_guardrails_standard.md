# Skill 与 Guardrails 规范

本文件记录后续 Skill / Prompt / Guardrails 的设计原则。  
当前阶段只预留入口，不要求完整实现 Skill 文件加载。

---

## 1. Skill 是什么

Skill 是处理某一类用户意图所需的能力声明，不是业务代码。

一个 Skill 应包含：

```text
1. skill_id
2. name
3. domain
4. triggers
5. prompt_fragment
6. required_tools
7. slots
8. actions
9. constraints
10. response_format
```

---

## 2. 三层能力

```text
Layer 1：Prompt 片段
Layer 2：数据工具与槽位
Layer 3：写操作与确认门
```

---

## 3. 写操作规则

```text
1. 退款、退货、取消订单、改地址等写操作不能直接执行。
2. 必须进入确认门。
3. 用户明确确认后才允许进入执行节点。
4. LLM 无权绕过确认门。
5. 第一阶段只预留确认门，不做真实写操作。
```

---

## 4. 全局护栏

```text
1. 价格、折扣、优惠不得编造。
2. 退款、补发、赔偿不得编造。
3. 不知道时说需要核实。
4. 不假装真人。
5. 最多一次追问，不连续追问多个问题。
6. 用户要求转人工时进入 HANDOFF。
```

---

## 5. 当前阶段

当前阶段只需要：

```text
1. 预留 SkillResolver。
2. 预留 guardrail_check 节点。
3. 用模板回复代替复杂 Prompt。
4. 后续再加载真实 Skill md 文件。
```
