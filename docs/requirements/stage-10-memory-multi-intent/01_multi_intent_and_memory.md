# Stage 10 需求：多意图识别、多任务时效治理与记忆系统

> 前置阅读：`docs/chat/intent_taxonomy.md`、`stage-05`（任务栈）、`stage-04`（LLM 工厂）。
> **状态：✅ 已实现（2026-07-03，实现记录见文末附录）。**
> 背景：核心链路闭环后的三块能力补齐——①一句话多意图；②多任务流转的时效与长度治理；
> ③短期/长期记忆（自研 + mem0 双实现，Provider 协议可扩展）。

---

## 1. 多意图识别（一句话多个诉求）

**现状缺口**：分类器只输出单意图，「我要退款，顺便查下这个订单的物流」会丢掉第二个诉求。

**设计（复用任务栈，不新造机制）**：

```text
整句分类（控制层意图命中则整句归它，不做拆分）
  ↓ 文本含并列标记（另外/顺便/还有/再帮我/然后/以及/同时/对了 或多子句标点）
分段：按并列标记与句读切分（≤3 段）
  ↓ 每段独立分类 + 每段独立抽槽（防串槽：「退款订单A1，查B2物流」A1 归退款、B2 归物流）
≥2 个不同业务意图 → 主意图 = 第一段（尊重用户表达顺序）
次要意图（最多 2 个）→ 构造 pending 任务连同该段槽位压入任务栈
  ↓
主意图正常走状态机；主任务结束时任务栈自动恢复次要任务（Stage 05 既有机制），
回复末尾附「另外，关于您提到的『X』：…」续办提示
```

规则：CHITCHAT/UNKNOWN 段在存在业务意图时忽略；两段同意图合并槽位不拆任务；
分段后段内分类置信度沿用现有阈值体系；决策日志记录 multi_intent 拆分结果。

## 2. 多任务流转治理（时效 + 长度）

已实现（Stage 05）：任务栈挂起/恢复、上下文槽位继承、「只发单号」续接**最新**任务。
本阶段补两道治理：

1. **任务时效（TTL）**：任务携带 `updated_ts`；load_session_state 加载时丢弃超过
   `TASK_TTL_MINUTES`（默认 30）未推进的 active_task 与栈内任务（对应 chat_task 行标
   ABORTED）。解决「用户很久之后回来，旧任务不该复活，新单号应服务新问题」。
2. **追问上限**：同一任务 NEEDS_SLOT 追问计数 `ask_count`，超过
   `TASK_MAX_ASKS`（默认 3）→ 放弃该任务并附转人工建议话术。解决「流转链不能太长」。

## 3. 记忆系统（短期 + 长期，双实现可扩展）

**协议**（`app/chat/memory/base.py`）：

```python
class MemoryProvider(Protocol):
    async def get_context(tenant_id, user_id, session_id, query) -> MemoryContext
        # MemoryContext = {session_summary, recent_turns[(role,text)], long_term_facts[str]}
    async def remember(tenant_id, user_id, session_id, user_text, reply, intent) -> None
        # 每轮结束后异步调用（best-effort，失败不影响主链路）
```

配置 `MEMORY_PROVIDER=local | mem0 | off`（默认 local）。

### 3.1 自研实现（LocalMemoryProvider）

- **短期**：本会话最近 `MEMORY_SHORT_TERM_TURNS`（默认 8）条消息（chat_message 现取）；
- **会话摘要**（防上下文膨胀）：会话消息数超过 `MEMORY_SUMMARY_THRESHOLD`（默认 20）后，
  用 LLM 把更早的对话压缩成摘要存 `chat_session.metadata_json.memory_summary`，
  之后注入摘要+近期窗口而非全量历史——**总结记忆，token 上界可控**；
- **长期**：新表 `user_memory`（tenant/user/kind/content/来源会话），每轮结束 LLM 抽取
  0-2 条**跨会话有价值**的持久事实（称呼、偏好、进行中纠纷等），完全重复不入库，
  取用时返回最近 `MEMORY_LONG_TERM_MAX`（默认 5）条；
- **无 LLM Key 降级**：摘要与长期抽取自动停用，短期窗口照常（离线可用）。

### 3.2 mem0 接入（Mem0MemoryProvider）

- 依赖 `mem0ai`（内部自带 LLM 抽取 + 向量检索 + 冲突合并）；
- `remember` → `Memory.add(messages, user_id=f"{tenant}:{user}")`；
  `get_context` 长期部分 → `Memory.search(query, user_id=...)`（短期窗口仍走本地消息表）；
- mem0 需要 LLM/Embedding 配置（走 OPENAI_* settings，向量库配 Milvus）；
- 同步 SDK 用线程池包装；导入失败/未配 Key → 启动告警并**自动降级 local**。

### 3.3 注入点

- LLM 回复润色（llm_responder）：摘要 + 近期轮次 + 长期事实进 prompt（此前 history 为空）；
- RAG 生成（answerer）：注入长期事实（如用户已说明的商品型号偏好）；
- 记忆写入：ChatService 在成功轮次登记提交后任务，只有聊天主事务 `commit` 成功后才
  `asyncio.create_task` 异步执行；事务 `rollback` 时丢弃，避免生成未落库轮次的幽灵记忆，
  同时保证摘要查询能读到本轮消息。记忆失败仍只记日志，绝不阻塞响应。

## 4. 不做什么

- 记忆管理界面/删除 API（按合规需求后补，表结构已支持按 user 清除）；
- mem0 托管云服务（只接 OSS 本地模式）；跨租户记忆共享（严格 tenant+user 隔离）。

## 5. 验证方式

1. 「我要退款，顺便查下这个订单的物流到哪了」→ 先进退款补槽/确认，回复附物流续办提示，
   两个诉求都被处理；「退款订单A1，再看下B2的物流」→ 槽位各归其主。
2. 订单缺号 → 转问物流也缺号 → 用户发单号 → 归**物流**（最新任务）；
   把任务 updated_ts 改老 → 新消息不再续接旧任务（TTL 生效）。
3. 同一任务连续 3 次追问不给 → 第 4 轮放弃并建议转人工。
4. MEMORY_PROVIDER=local + 配 LLM：多轮后 chat_session 出现 memory_summary；
   user_memory 出现持久事实；无 Key 时短期窗口照常、不报错。
5. MEMORY_PROVIDER=mem0：remember/search 走 mem0（单测 mock；真实联调需 LLM Key）；
   未装包/未配 Key 自动降级 local 并告警。
6. 全量回归（66+ 测试、既有 e2e 场景）不回退。

---

## 附录：实现记录（2026-07-03）

### 已实现清单

1. **多意图**（`app/chat/intent/multi_intent.py` + intent_classify 节点）：并列标记触发分段、
   每段独立分类与抽槽（`slot_text` 限定主意图段抽槽范围，防串槽）、次要意图 pending 入任务栈
   （状态机 resolve 新增 pending_intents 参数，倒序压栈保持用户表达顺序）；
   决策日志 intent_result 带 multi_intent 标记。
   **恢复即完成的读任务**（槽位已齐）特殊处理：保持等待态并提示「回复『继续』即可查询」，
   下一轮任意消息续接触发 tool_invoke/product 执行（避免静默完成却没人执行查询）。
2. **多任务治理**：任务携带 `updated_ts`（每次评估刷新），load_session_state 丢弃超过
   `TASK_TTL_MINUTES` 的 active/栈内任务（chat_task 行标 ABORTED、残留 COLLECTING/CONFIRMING
   状态复位 IDLE）；`ask_count` 追问计数超 `TASK_MAX_ASKS` → 放弃 + 转人工建议话术（gave_up）。
3. **记忆系统**：`MemoryProvider` 协议 + 双实现：
   - LocalMemoryProvider：短期窗口（chat_message 近 N 条）+ 会话摘要
     （超阈值 LLM 增量压缩存 chat_session.metadata_json，记录已覆盖消息数）+
     长期事实（LLM 抽取入 user_memory 表，内容级去重）；无 Key 时摘要/抽取停用、窗口照常；
   - Mem0MemoryProvider：长期记忆托管 mem0（add/search，Milvus 独立 collection、
     OPENAI_* 配置），短期复用本地；未装包/无 Key 自动降级 local 并告警；
   - 注入点：LLM 回复润色（摘要+近期轮次+长期事实）、RAG 生成（长期事实，
     标注「仅供表达贴合」防把记忆当新事实）；写入由 ChatService 登记为 after-commit
     异步 best-effort，主事务回滚不写记忆。
4. **新表** user_memory（migration `d7395298fab5`）。

### 过程中发现并修复的真实 bug

1. **chat_message.created_at 用 now()（事务时间戳）**：同一轮的用户消息与 AI 回复
   同事务落库时间戳完全相同，历史排序先后不稳定（回复可能排在提问前）——
   改 `clock_timestamp()`（语句级，migration `df385246e05d`）。
2. 商品名引导词模式允许 1 字符：「就是那个啥」把「啥」抽成商品名——最少 2 字符。

### 验证记录（全部通过）

- 单测 15 个（累计 81）：多意图拆分/串槽防护/控制意图不拆/闲聊段忽略、
  pending 入栈与恢复顺序、追问上限放弃、updated_ts、记忆短期窗口/无 Key 降级/
  事实抽取去重/摘要写入/mem0 无 Key 降级 local、MemoryContext 序列化。
- e2e：「退款订单A1…，再看下B2…的物流」→ 退款确认+工单号+物流续办提示 →
  「继续」查 **B2** 物流（槽位各归其主）；追问 2 次不给单号 → 第 3 轮放弃+转人工建议，
  放弃后单号不复活任务；任务时间戳改老 1 小时 → 新单号不续接旧任务；
  无 LLM Key 时记忆零影响、全链路回归正常。

### 遗留

```text
1. 记忆的 LLM 路径（摘要/事实抽取/mem0）与真实端点联调待配 Key 后验证（单测已 fake 覆盖）。
2. 多意图 >3 段截断、pending >2 丢弃——极端长句的取舍已在代码注释与本文档声明。
3. 记忆管理面（查看/删除用户记忆）按合规需求后补（表结构已支持按 user 清除）。
```

## 附录：记忆写入事务一致性修订（2026-07-15）

此前 ChatService 在图执行完成、但请求级数据库事务尚未提交时立即创建记忆后台任务，存在两类
竞态：摘要查询可能看不到本轮消息；主事务随后提交失败时，长期事实却可能已由独立事务落库。

本次修订：

1. 新增 `app/chat/memory/scheduler.py`，在请求 `AsyncSession` 上登记待写入的记忆数据；
2. 监听 SQLAlchemy `after_commit`，提交成功后才创建后台任务；监听 `after_rollback` 清空待执行项；
3. 后台任务继续持有强引用，应用 shutdown 时限时等待，异常只记录日志；
4. 增加提交前不执行、提交后执行、回滚不执行的回归测试。
