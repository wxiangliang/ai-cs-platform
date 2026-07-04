# Stage 07 需求：人工接管与工单

> 前置阅读：`docs/architecture/roadmap.md` 3.5、`docs/chat/skills_design/skills/META.TRANSFER_HUMAN.md`
> （主动/被动/不可用/带上下文四场景话术）、`docs/database/chat_tables.md`。
> 前置条件：Stage 05 已完成（工单是写操作，复用 chat_task/审计模式；requires_human_if 声明已在 Skill 中）。

---

## 1. 阶段目标

把"转人工"从**只改状态字段**升级为**完整闭环**：建工单（带上下文移交）→ bot 静默 →
坐席认领/回复 → 解决归还 → bot 恢复。

**必须修复的现状缺陷**：当前 META.TRANSFER_HUMAN 只把会话置为 handoff，
但用户后续消息仍会被 bot 正常处理（决策链照跑、照回复）——人工接管期间 bot 抢答
是体验事故，本阶段第一优先修复。

## 2. 本阶段要做什么

1. **chat_handoff_ticket 表**（migration，同步 chat_tables.md）

   | 字段 | 说明 |
   |---|---|
   | id / tenant_id / session_id / user_id | 标识（索引 tenant 打头） |
   | reason | 触发原因枚举：USER_REQUEST（用户要求）/ SKILL_RULE（requires_human_if）/ PAYMENT_ISSUE / REPEATED_UNKNOWN（连续 2 轮兜底）/ EXECUTION_FAILED（工具执行失败）/ MANUAL |
   | source_intent | 触发时的意图码 |
   | status | PENDING → ASSIGNED → RESOLVED / CLOSED（放弃） |
   | assignee | 坐席标识 |
   | context_json | **上下文移交包**：active_task + task_stack 快照、已收集槽位、最近 8 条消息摘要、最后一次检索/工具轨迹——坐席打开工单即懂上下文，不让用户复述 |
   | created_at / updated_at / resolved_at | 时间 |

2. **HandoffService（触发收口）**：所有转人工路径统一走 `create_ticket()`：
   - META.TRANSFER_HUMAN（现有）；
   - PAYMENT.ISSUE（skills_design 声明全转人工，现在只有话术没有单）；
   - 连续 2 轮 META.UNKNOWN（META.UNKNOWN.md 声明的安全阀，现在未实现计数）；
   - ActionExecutor 执行失败（现在只给安抚话术）；
   - Skill 的 requires_human_if 命中（Stage 05 已加载声明，本阶段接判定钩子——
     v1 先接"工具返回异常状态"这一机读条件，自然语言条件留给 LLM 判定后续增强）。
   - 同一会话存在未关闭工单时不重复建单（幂等）。

3. **bot 静默**：会话 status=handoff 期间，用户消息**不进决策链**——
   graph 前置短路（load_session_state 后判断）：消息照常落库（人工要看），
   回复固定话术「人工客服正在为您服务，请稍候」，decision_log 记 status=HANDOFF_SILENT。
   坐席 resolve 归还后 bot 恢复。

4. **坐席侧 API**（管理面，沿用 KB_ADMIN_TOKEN 临时保护，Stage 08 换正式鉴权）：

   ```text
   GET  /api/handoff/tickets?status=&limit=&before=   # 工单队列（PENDING 优先）
   GET  /api/handoff/tickets/{id}                     # 详情（含 context_json 与会话近况）
   POST /api/handoff/tickets/{id}/claim               # 认领（body: assignee）PENDING→ASSIGNED
   POST /api/handoff/tickets/{id}/reply               # 坐席回复：入 chat_message（role=agent）
   POST /api/handoff/tickets/{id}/resolve             # 解决归还：ticket→RESOLVED，
                                                      #   会话 status→active、dialog_state→IDLE（任务栈清空），bot 恢复
   ```

5. **chat_message.role 扩展**：新增 `agent`（人工客服消息），历史接口原样返回
   （前端按 role 区分展示）；枚举文档同步。

## 3. 本阶段不做什么

- 坐席工作台 UI、实时推送（WebSocket/SSE 坐席通知）——先靠轮询队列接口；
- 排班、技能组路由、自动分配（PENDING 队列人工认领）；
- 满意度评价（Stage 09 chat_feedback 承接）；
- requires_human_if 自然语言条件的 LLM 判定（仅接机读条件）。

## 4. 技术要求

- 工单创建与消息落库同事务；重复建单幂等（唯一部分索引：同会话未关闭工单唯一）。
- bot 静默判断必须在意图分类**之前**短路（省一次模型推理，也避免静默期决策日志噪音）。
- resolve 归还时 dialog_state 重置要走既有 upsert（乐观锁），并发坐席操作冲突返回 409。
- context_json 注意脱敏口径与 chat_tool_call 一致（手机号打码）。

## 5. 目录和文件要求

```text
app/models/chat_handoff_ticket.py
app/repositories/chat_handoff_ticket_repository.py
app/services/handoff_service.py          # 触发收口 + 坐席操作
app/api/routes/handoff.py
app/chat/graph/nodes/…                   # bot 静默短路（load_session_state 内或新节点）
alembic/versions/xxxx_add_handoff_ticket.py
tests/stage07/
```

## 6. 具体实现要求

- 连续 UNKNOWN 计数存 dialog_state.context_stacks_json（已有闲置字段），不加新列。
- 建单后的用户话术区分场景（META.TRANSFER_HUMAN.md 的四场景）：坐席在线提示等待、
  队列繁忙提示留言（v1 统一「已为您转接人工，请稍候」+ 工单号）。
- 决策日志：建单轮记录 reason 与 ticket_id（error_json 复用或 retrieval 扩展字段）。

## 7. 代码质量要求

- 单测：触发收口各 reason、幂等建单、静默短路、resolve 归还后 bot 恢复、并发 claim 冲突。
- ruff / mypy 通过；核心逻辑中文注释。

## 8. 验证方式

1. 「转人工」→ 回复含等待话术，ticket=PENDING（context_json 带任务快照）；再发消息 → bot 静默、消息落库。
2. 坐席 claim → reply（用户历史接口可见 role=agent 消息）→ resolve → 用户再发消息 bot 正常应答。
3. 「支付扣了两次款」→ 自动建单（reason=PAYMENT_ISSUE）。
4. 连续两轮无法识别 → 第二轮回复附转人工询问并建单（reason=REPEATED_UNKNOWN）。
5. 同会话重复「转人工」不重复建单。
6. mock 工具置为失败 → 执行失败自动建单（reason=EXECUTION_FAILED）。

## 9. 执行提示词

```text
请先阅读 AGENTS.md、docs/architecture/roadmap.md、本文档与 META.TRANSFER_HUMAN.md。
本次只实现 Stage 07，按第 2 节逐项实现，第 3 节不要做。
完成后说明新增/修改文件、迁移脚本、验证步骤。
```

---

## 附录：实现记录（2026-07-03）

### A. 已实现清单

| 需求项 | 实现位置 | 说明 |
|---|---|---|
| 工单表 | `app/models/chat_handoff_ticket.py`，migration `7cfc385fd19a` | 部分唯一索引 `uq_chat_handoff_open_session` 承载「同会话最多一张未关闭工单」 |
| Repository | `app/repositories/chat_handoff_ticket_repository.py` | claim 用条件 UPDATE（WHERE status='PENDING'）防并发抢单 |
| HandoffService | `app/services/handoff_service.py` | `ensure_ticket` 幂等建单 + `_build_context` 上下文移交包（任务栈/槽位/最近 8 条消息，脱敏）+ claim/reply/resolve |
| bot 静默 | `app/chat/graph/nodes/load_session_state.py` | `chat_session.status=handoff` → blocked+handoff_silent 短路（意图分类前，省一次模型推理）；`response_generate` 返回固定等待话术；用户消息照常落库（status=HANDOFF_SILENT） |
| 触发收口 | `save_turn`（USER_REQUEST / PAYMENT_ISSUE / REPEATED_UNKNOWN）、`action_execute`（EXECUTION_FAILED）、`tool_invoke`（SKILL_RULE，requires_human_if 声明 + 工具全败） | 建单轮 `decision_log.retrieval_json.handoff={reason,ticket_id,created}` |
| UNKNOWN 连击 | `save_turn` 计数存 `context_stacks_json.unknown_streak`，阈值 `HANDOFF_UNKNOWN_STREAK=2` | 达阈值建单但**不置会话 handoff**（用户仍可与 bot 交互，坐席主动介入） |
| 坐席 API | `app/api/routes/handoff.py`（require_admin） | GET /api/handoff/tickets（游标分页）、GET /{id}（含 context）、POST /{id}/claim（409 冲突）/reply（role=agent）/resolve（会话归还 active + 状态机复位 IDLE） |
| 测试 | `tests/stage07/test_handoff.py`（10 例） | 幂等建单/并发 claim/静默短路/resolve 归还/连击建单/五类 reason 触发 |

### B. 关键实现决策

1. **回复定稿顺序**：save_turn 调整为「存用户消息 → 状态机 sync + 转人工触发（可能追加工单号文案）→ 存 AI 消息 → 决策日志」，保证落库的 assistant 消息与 API 返回一致。
2. **静默短路防覆盖**：guardrail_check 通过时会写 `blocked=False`，会覆盖 load_session_state 的静默标记——已在 guardrail_check 顶部对 `handoff_silent` 轮次透传（e2e 发现并修复）。
3. **建单失败不打断主链路**：action_execute / tool_invoke 中 ensure_ticket 包 try/except，只告警。
4. **resolve 清任务栈**：人工已处理完，不自动续接旧任务，状态机复位 IDLE。

### C. 验证记录（第 8 节场景全过）

- 「转人工」→ HANDOFF + 工单号；后续消息 HANDOFF_SILENT 固定话术，消息落库 ✅
- 坐席 claim（二次 claim 409）→ reply（历史可见 role=agent）→ resolve → bot 恢复应答 ✅
- 支付异常 → PAYMENT_ISSUE 自动建单 + 会话静默 ✅
- 连续 2 轮 UNKNOWN → REPEATED_UNKNOWN 建单 + 回复附转人工询问 ✅
- 同会话重复触发不重复建单（unit + e2e）✅
- 工具失败 → SKILL_RULE / 执行失败 → EXECUTION_FAILED（unit，mock 无法自然触发失败）✅
- 全量：ruff / mypy 通过，pytest 101 passed

### D. 遗留

- 坐席在线状态与排队提示（v1 统一「已转接请稍候」+ 工单号）；坐席侧实时推送（当前轮询队列）。
- CLOSED 状态暂无 API 触达（预留给超时自动关单/用户撤回，Stage 09 一并考虑）。
