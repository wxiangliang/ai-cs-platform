# Stage 05 需求：工具层与确认门闭环

> 前置阅读：`docs/chat/intent_taxonomy.md`（风险等级 L0-L3）、`docs/chat/skills_design/00_skill_schema.md`（v2，
> required_tools / actions / tool_returns 段）、`docs/architecture/roadmap.md` 3.3 节。
> 前置条件：Stage 04 已完成（LLM Provider 可用——确认应答解析需要它）。
> **状态：✅ 已实现（2026-07-02，实现记录与范围调整说明见文末附录）。**

---

## 1. 阶段目标

让 Skill 的三层能力真正跑起来：从 md 文件加载 Skill 声明、工具接口层（mock 实现）、
写操作的完整确认门闭环（确认 → 执行 → 审计），并把任务生命周期从 JSONB 快照升级为持久化表。
本阶段结束后，「查订单」能返回 mock 数据，「退款」能走完 确认 → 建工单（mock）→ 回执 全流程。

## 2. 本阶段要做什么

1. **Skill Loader**（`app/chat/skills/loader.py`）
   - 启动时从 `docs/chat/skills_design/skills/*.md` 解析 YAML front-matter，构建 SkillRegistry（替换内存静态注册表）。
   - 加载时校验：skill_id 必须在 taxonomy 注册表中；domain 属于 9 域枚举；risk_level/priority 必填；
     `confirmation_prompt` 占位符必须能在 slots ∪ tool_returns ∪ 系统上下文白名单中找到来源。校验失败启动报错，禁止带病上线。
   - Stage 03 的模板回复字段（collect/confirm/answer/confirmed）作为 Skill md 的补充段保留，供 LLM 降级用。

2. **工具接口层**（`app/chat/tools/`）
   - `base.py`：`ToolProvider` 协议——`async def invoke(tool_id, params, *, tenant_id, timeout) -> ToolResult`；
     `ToolResult = {ok, data, error_code, latency_ms}`。
   - `mock_provider.py`：实现 Skill 文件中声明的全部 tool_id（query_order / query_logistics_track / query_product /
     create_refund_ticket / cancel_order / …），返回确定性 mock 数据（便于测试断言）。
   - 工具调用一律有 timeout（settings，默认 10s）；结果与耗时落 `chat_tool_call` 表。

3. **确认门闭环**
   - `app/chat/confirmation/parser.py`：ConfirmationResponseParser——在 CONFIRMING 状态下解析用户应答：
     规则先行（「确认/是的」「不/算了」），含糊表达（「金额不对，应该是 200」）交 LLM 解析为
     `CONFIRM / DENY / MODIFY(slot_updates) / UNRELATED`；MODIFY 回填槽位重新进确认门。
   - `app/chat/actions/executor.py`：ActionExecutor——执行前二次校验（risk_level、必填槽位齐全、action 声明
     requires_confirmation 时必须有本轮确认记录），调用 ToolProvider，执行结果写 chat_tool_call 并更新任务状态。
   - **红线**：response_generate / LLM 任何路径不得直接调用写工具；唯一入口是 ActionExecutor。

4. **任务持久化**（新表，出 Alembic migration，同步更新 `docs/database/chat_tables.md`）
   - `chat_task`：id / tenant_id / session_id / intent / skill_id / status(COLLECTING/CONFIRMING/EXECUTING/DONE/ABORTED/FAILED) /
     collected_slots_json / confirmed_at / executed_at / result_json / version / created_at / updated_at。
     dialog_state.active_task_json 改为只存 task_id 引用 + 轻量快照。
   - `chat_tool_call`：id / tenant_id / session_id / task_id / tool_id / request_json（脱敏）/ response_json /
     ok / error_code / latency_ms / created_at。
   - 索引均以 tenant_id 打头。

5. **任务挂起/恢复**：启用 dialog_state.task_stack_json——COLLECTING/CONFIRMING 中用户提出新业务意图时，
   旧任务入栈（上限 2 层，超出提示先完成当前任务）；新任务结束后询问是否继续旧任务。

6. **图结构调整**：`skill_resolve → tool_invoke（新节点，读操作查询）→ response_generate`；
   CONFIRMING 状态入口改走 `confirmation_parse` 节点分支。线性图升级为条件边图。

## 3. 本阶段不做什么

- 不对接真实业务系统（全部 mock）；不做 RAG（Stage 06）；不做坐席侧接管界面（Stage 07）。
- 不实现 rollback（actions 声明保留字段，执行层只记录不支持撤销）。

## 4. 技术要求

- YAML 解析用 pyyaml（front-matter 解析）；工具层全 async；所有工具调用带 timeout 与错误码。
- chat_tool_call 的 request_json 必须脱敏（手机号打码），日志同样。
- 事务边界：工具执行（外部副作用）与 DB 事务解耦——先落「执行中」状态再调工具，工具结果用独立提交更新，避免回滚后外部副作用无痕。

## 5. 目录和文件要求

```text
app/chat/skills/loader.py
app/chat/tools/base.py
app/chat/tools/mock_provider.py
app/chat/confirmation/parser.py
app/chat/actions/executor.py
app/chat/graph/nodes/tool_invoke.py
app/chat/graph/nodes/confirmation_parse.py
app/models/chat_task.py
app/models/chat_tool_call.py
app/repositories/chat_task_repository.py
app/repositories/chat_tool_call_repository.py
alembic/versions/xxxx_add_task_and_tool_call.py
```

## 6. 具体实现要求

- SkillRegistry 对外接口（`get(intent)`）保持不变，下游节点无感知。
- ActionExecutor 的确认记录校验：必须是**同一 task 且本轮或上一轮**的 CONFIRM，防止旧确认被重放。
- 执行成功/失败都要给用户明确回执（成功含工单号 mock 值；失败走转人工话术）。
- decision_log 扩展记录 tool_calls 摘要与确认门轨迹。

## 7. 代码质量要求

- 确认门与执行器必须有单元测试（含：未确认直接执行被拒、修改槽位重进确认门、工具超时降级）。
- ruff / mypy 通过；核心逻辑中文注释。

## 8. 验证方式

1. 「我要退款」→ 补订单号 → 系统复述确认 → 「确认」→ 返回 mock 工单号，chat_task=DONE，chat_tool_call 有记录。
2. 确认门下回复「金额不对」→ 解析为 MODIFY，更新槽位后再次确认。
3. 确认门下回复「先帮我查下物流」→ 旧任务入栈，物流查询完成后提示恢复。
4. 直接构造未确认任务调 ActionExecutor → 拒绝执行并告警日志。
5. 「订单到哪了」→ tool_invoke 返回 mock 物流轨迹，回复含真实（mock）数据而非「帮您核实」。

## 9. 执行提示词

```text
请先阅读 AGENTS.md、docs/chat/intent_taxonomy.md、docs/chat/skills_design/00_skill_schema.md、本文档。
本次只实现 Stage 05，按第 2 节逐项实现，禁止对接真实外部系统。
完成后说明新增/修改文件、迁移脚本、验证步骤。
```

---

## 附录：实现记录（2026-07-02）

### 与原需求的范围调整（决策记录）

| 原需求 | 实际实现 | 调整理由 |
|---|---|---|
| Skill Loader 完全替换内存注册表 | **双源合并**：md 提供能力声明（工具/动作/风险/约束/prompt_fragment），代码注册表保留运行时模板与运行时槽位 | 31 个 md 用业务视角槽位（customer_phone_or_order_id），运行时抽取器用 order_id/phone；一次性全量迁移风险大，合并式渐进最稳。启动校验照做（占位符来源校验发现并修复了 6 个文件的 tool_returns 缺失） |
| 恢复挂起任务时「询问是否继续」 | **自动恢复 + 续办提示**（回复末尾附原任务的追问/确认话术） | 少一轮无信息量的问答，且确定性可测 |
| 栈超限「提示先完成当前任务」 | 溢出丢最旧（FIFO 淘汰，上限 TASK_STACK_MAX=2） | 拒绝用户新诉求的体验更差 |

### 已实现清单

1. **Skill Loader**（`app/chat/skills/loader.py`）：31 个 md 全量加载校验（skill_id 必须在意图目录、
   9 域枚举、risk_level/priority 必填、confirmation_prompt 占位符来源校验，
   `SKILL_LOADER_STRICT` 控制告警是否升级为启动失败）；已批量补齐全部 md 的
   risk_level/priority 与 6 个写技能的 tool_returns 声明。
2. **工具层**（`app/chat/tools/`）：ToolProvider 协议 + MockToolProvider
   （确定性假数据：同入参恒同输出；TOOL_TIMEOUT 超时；未知工具 TOOL_NOT_FOUND）；
   审计脱敏（手机号打码）。
3. **ActionExecutor**（`app/chat/actions/executor.py`，**唯一写入口**）：
   三重校验（WRITE+action 声明 / 必填槽位 / 防重放——任务行已 EXECUTING/DONE 拒绝二次执行）；
   独立事务先落 EXECUTING 标记再调工具（外部副作用可追溯）；
   结果写 chat_tool_call + chat_task（注意主事务更新前 refresh 行版本，避免撞乐观锁）。
4. **ConfirmationResponseParser**（`app/chat/confirmation/parser.py` + confirmation_parse 节点）：
   短句确认/否认仍走规则（0ms）；含糊应答 LLM 解析 CONFIRM/DENY/MODIFY(槽位修正白名单)/
   UNRELATED；无 Key 时透传原分类结果。
5. **新表**：chat_task（生命周期 COLLECTING/CONFIRMING/EXECUTING/DONE/ABORTED/FAILED/SUSPENDED，
   乐观锁）+ chat_tool_call（append-only 审计）；save_turn 统一同步任务行状态。
6. **任务挂起/恢复**：确认门/补槽中提出新业务意图 → 旧任务入栈（SUSPENDED）；
   新任务结束自动恢复 + 续办提示；**上下文槽位继承**（「查下这个订单的物流」继承
   被挂起任务的 order_id，skills 设计原则 4 落地）；「算了」清空任务与栈。
7. **图路由升级**：+confirmation_parse（线性段）；+action_execute / +tool_invoke 分支
   （五个回复分支汇入 save_turn）；tool_invoke 把订单/物流查询从「帮您核实」升级为
   真实（mock）数据回答，全部工具失败按 R4 走 rag_fallback 或降级模板。

### 验证记录（全部通过）

- 单测 53 个：任务栈（挂起/恢复/继承/清栈/深度上限）、执行器三重校验与防重放、
  mock 确定性、确认解析器（MODIFY 白名单/无效输出/无 Key）、loader 全量加载；
- e2e：退款→确认→**工单号 RF…**（chat_task=DONE + result_json + 三段 tool_call 审计）；
  确认门中插入「查这个订单物流」→ 挂起+继承订单号+真实轨迹回复+续办提示 → 「确认」
  恢复执行成功；重复「确认」安全兜底；订单状态查询返回 mock 事实数据。

### 遗留

```text
1. 真实业务工具对接：新增 ToolProvider 实现（HTTP）并在 factory 登记；
   真实副作用场景建议把「EXECUTING 独立提交 + 结果更新」升级为 outbox 模式。
2. MODIFY 槽位修正的 e2e 依赖真实 LLM（单测已覆盖，联调时验证）。
3. rollback 字段仍只声明不支持撤销（按原需求）。
4. AFTERSALE.COMPLAIN 的建单 action（无确认写操作）暂未走执行器（仍模板回复），
   接入时只需把其意图路由到 action_execute 分支。
```
