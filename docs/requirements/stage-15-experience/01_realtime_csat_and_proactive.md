# Stage 15 需求：体验闭环——坐席实时通道、满意度、会话生命周期与主动消息

> 来源：AI Agent 客服系统功能差距分析（2026-07-03）。当前平台「用户问→bot 答→
> 转人工」的被动闭环已完整，缺的是**实时性**（坐席侧轮询、用户侧无推送）、
> **满意度量化**（只有消息级点赞点踩，无会话级 CSAT）与**会话生命周期管理**
> （会话永不关闭、无主动触达）。

---

## 1. 阶段目标

补齐三块用户可感知的体验能力，全部复用既有表与状态机，不推翻现有架构。

## 2. 本阶段要做什么

### 2.1 实时通道（WebSocket）

- `WS /api/chat/sessions/{session_id}/ws`（chat scope）：用户侧长连接——
  坐席回复（role=agent 消息）实时推送（替代当前「拉历史才看到」）；
  bot 回复仍走既有 HTTP/SSE，WS 只做服务端主动推送通道；
- `WS /api/handoff/ws`（admin scope）：坐席侧——新工单（PENDING 创建）、
  用户新消息（HANDOFF_SILENT 轮次）实时推给已认领坐席/队列页；
- 实现：Redis Pub/Sub 做进程间广播（多 worker 安全），连接管理器持弱引用，
  断线即弃（客户端重连+拉历史补齐，不做离线消息队列）；
- 排队反馈：用户转人工后，回复话术带当前 PENDING 队列位置（简单 count 查询）。

### 2.2 会话级满意度（CSAT）

- resolve 归还会话时（bot 恢复轮）与会话关闭时，向用户发一条满意度询问
  （模板消息，role=assistant，metadata 标记 csat_request）；
- 用户回复 1-5 分或「满意/不满意」→ 控制层规则识别（CONFIRMING 之外的
  csat_pending 状态标记，存 context_stacks_json）→ 落 `chat_csat` 表
  （session_id/score/comment/trigger=handoff_resolve|session_close）；
  非评分回复照常进主链路（不打断正常提问）；
- 看板：quality_daily 加 csat 均分列；quality_queries.md 补 CSAT 口径 SQL；
  低分（≤2）自动进 export_review_set 待审导出。

### 2.3 会话生命周期

- `SESSION_IDLE_CLOSE_HOURS`（默认 24）：超时无消息的 active 会话由定时 CLI
  （`scripts/close_idle_sessions.py`，幂等，进 cron）置 closed +
  状态机复位 + 触发 CSAT 询问（可关）；
- closed 会话来新消息：自动重开（status→active，state=IDLE，任务栈不复活——
  与 TASK_TTL 语义一致）；
- handoff 工单超时自动关单：ASSIGNED 超 `TICKET_STALE_HOURS` 无坐席动作 →
  status=CLOSED + 会话归还（补上 Stage 07 遗留的 CLOSED 路径）。

### 2.4 主动消息（最小闭环）

- `POST /api/chat/sessions/{session_id}/notify`（admin scope）：业务系统回调入口
  （物流异常/工单完成等），写 role=assistant 消息（metadata 标记 proactive）+
  WS 推送；本阶段只做**入口与推送**，不做事件源对接（属真实系统联调）。

## 3. 本阶段不做什么

- 多渠道接入（微信/企微/APP SDK——backlog，见 roadmap 差距清单）；
- 离线消息队列与已读回执；
- 坐席工作台前端（API 就绪即可，前端另行）；
- 营销类主动触达（合规面大，需单独评审）。

## 4. 目录和文件要求

```text
app/api/routes/ws.py              # 两个 WS 端点 + 连接管理器
app/services/notify_service.py    # Redis Pub/Sub 广播 + 主动消息
app/models/chat_csat.py + repository + migration
scripts/close_idle_sessions.py
tests/stage15/
```

## 5. 验证方式

1. 双端 WS：坐席 reply → 用户 WS 秒收；用户在 handoff 会话发消息 → 坐席 WS 秒收；
2. resolve 后用户收到 CSAT 询问，回「5」落表且不影响继续提问；回业务问题照常应答；
3. 造 25 小时前的 idle 会话 → CLI 关闭 + 重开语义正确；ASSIGNED 超时 → CLOSED + 会话归还；
4. notify 入口写入 + WS 推送 + 消息落库可追溯；
5. 多 worker（--workers 2）下 WS 跨进程广播正确（Pub/Sub 验证）。

---

## 附录：实现记录（2026-07-04）

### A. 已实现清单

| 需求项 | 实现位置 | 说明 |
|---|---|---|
| WS 双端通道 | `app/api/routes/ws.py`（用户 `WS /api/chat/sessions/{id}/ws` + 坐席 `WS /api/handoff/ws`）+ `app/services/notify_service.py`（WsHub） | Redis Pub/Sub 跨进程广播（psubscribe ws:*，每进程一个监听任务，断连指数退避重连）；Redis 故障回落本进程直投（单 worker 仍可用）；断线即弃无离线队列（重连拉历史补齐）；死连接投递失败自动清理 |
| WS 鉴权 | verify_bearer_token（从 get_auth_context 提取共用）：鉴权开启读 Authorization 头（用户端 chat scope+会话归属校验，越权 4404；坐席端 admin scope）；开发模式用户端 query tenant_id/user_id、坐席端 query admin_token（与管理面同 token 口径） | 校验失败 4403/4404 策略码关闭 |
| 推送事件 | 用户端：agent_reply（坐席回复）/ session_resumed（归还+CSAT 询问）/ proactive（主动消息）；坐席端：ticket_created（新工单）/ user_message（接管期间用户消息） | 发布点：handoff_service.reply/ensure_ticket/resolve、save_turn（HANDOFF_SILENT 轮）、notify 接口；publish 全部 fail-open |
| CSAT | `chat_csat` 表（migration `b546c33dea6f`）+ `app/chat/csat.py` 解析器（数字 1-5/「4分」/口语词完全命中才算，防误吞业务消息） | resolve/会话关闭发询问（assistant 消息 metadata.csat_request + context_stacks.csat_pending）；评分回复在 load_session_state 短路捕获（blocked 复用，guardrail 透传条件扩为通用 blocked）→ save_turn 落库+清标记+致谢（低分附致歉）；非评分回复照常进主链路且标记一次性清除 |
| CSAT 看板与回流 | quality_daily 重建加 csat_avg/csat_count 列（CTE+LEFT JOIN）；quality_queries.md 补口径；export_review_set 纳入低分（<=2）CSAT 会话全部轮次 | 实测 csat_avg=5.00 出数 |
| 会话生命周期 | `scripts/close_idle_sessions.py`（幂等，建议 cron 每小时）：空闲关闭（最后消息早于 SESSION_IDLE_CLOSE_HOURS→closed+状态机复位+可选 CSAT 询问）+ 超时工单（ASSIGNED 超 TICKET_STALE_HOURS→CLOSED+会话归还，补 Stage 07 遗留；PENDING 不自动关——排班问题不该丢单）；closed 会话来消息 load_session_state 自动重开（任务不复活） | — |
| 排队反馈 | queue_position（PENDING 队列位置）：USER_REQUEST 建单回复附「当前排队第 N 位」（第 1 位不显示） | — |
| 主动消息 | `POST /api/chat/sessions/{id}/notify`（admin scope）：写 assistant 消息（metadata.proactive/category）+ WS 推送；事件源对接留给真实系统联调 | — |

### B. 关键实现决策

1. **同步策略修复（顺带）**：claim/claim_for_execution 的 Core 条件 UPDATE 后显式 `session.expire` 身份映射实例——expire_on_commit=False 下 auto 同步策略行为不稳定（pytest 环境暴露 assignee 陈旧）。
2. **CSAT 短路复用 blocked 机制**：guardrail_check 透传条件从 handoff_silent 扩为「上游已置 blocked」，为后续短路类需求留了通用位。
3. **CSAT 一次性语义**：询问后仅下一轮生效——评分即捕获，非评分即清标记，不纠缠用户。

### C. 验证记录（第 5 节场景全过）

- 双端 WS e2e：转人工→坐席端 ticket_created 秒达；接管期用户消息→坐席端 user_message；坐席 reply→用户端 agent_reply；resolve→用户端 session_resumed（含 CSAT 询问）；notify→用户端 proactive ✅
- 排队位置：建单回复附「当前排队第 5 位」（库内既有 PENDING 单，位置正确）✅
- CSAT：回「5」→致谢话术 + chat_csat 落库 + 标记清除；quality_daily csat_avg 出数 ✅
- 生命周期：单测覆盖空闲关闭（+CSAT 询问）/超时工单 CLOSED+会话归还/closed 重开；CLI dry-run 幂等 ✅
- 全量 185 tests（新增 stage15 19 例）；ruff/mypy 干净

### D. 遗留

- 坐席工作台前端（API/WS 就绪）；离线消息与已读回执（明确不做，v2 再议）；
- WS 连接数上限/心跳保活（生产接入层——nginx/网关侧配置，代码无需改）。
