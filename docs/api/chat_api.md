# Chat API 设计（v2）

本文件定义聊天接口设计。  
第一阶段先实现基础接口，后续再扩展流式、历史消息、人工接管等能力。

> 【鉴权，Stage 08 已实现】生产环境必须 `AUTH_ENABLED=true`：
> 所有接口带 `Authorization: Bearer ak_xxx.sk_yyy`（密钥用 `scripts/manage_api_keys.py` 创建），
> tenant_id 一律从凭证解析（请求体中的忽略）；聊天面需 `chat` scope，
> kb/product 管理面需 `admin` scope；会话必须先 `POST /sessions` 服务端发号。
> `AUTH_ENABLED=false`（默认）为开发模式：行为与旧版一致（请求体传 tenant_id），仅限联调。
> 发消息接口支持 `Idempotency-Key` 请求头幂等；所有响应带 `X-Trace-Id` 头。

---

## 1. 健康检查

```http
GET /api/health
```

返回：

```json
{
  "success": true,
  "code": "OK",
  "message": "success",
  "data": {
    "status": "ok"
  },
  "trace_id": null
}
```

---

## 2. 就绪检查

```http
GET /api/health/ready
```

检查：

```text
PostgreSQL
Redis
```

返回：

```json
{
  "success": true,
  "code": "OK",
  "message": "success",
  "data": {
    "postgres": "ok",
    "redis": "ok"
  },
  "trace_id": null
}
```

---

## 3. 发送聊天消息

```http
POST /api/chat/sessions/{session_id}/messages
```

请求：

```json
{
  "tenant_id": -99,
  "user_id": "u_001",
  "message": "我要退款",
  "channel": "web",
  "stream": false
}
```

返回：

```json
{
  "success": true,
  "code": "OK",
  "message": "success",
  "data": {
    "message_id": "m_xxx",
    "session_id": "s_001",
    "reply": "请提供需要退款的订单号。",
    "intent": "AFTERSALE.REFUND",
    "status": "NEEDS_SLOT",
    "state": "COLLECTING",
    "slots": {},
    "trace_id": "trace_xxx"
  },
  "trace_id": "trace_xxx"
}
```

---

字段说明：

```text
message : 必填，1~4000 字符，纯空白拒绝（422）
status  : 单轮处理状态（TurnStatus），取值见 docs/database/chat_tables.md 第 2 节
state   : 会话状态机状态（DialogStateValue），与 status 是两个枚举，勿混用
stream  : 预留字段，Stage 04 前忽略
```

---

## 4. 错误响应契约（v2 新增）

所有错误走统一结构（success=false），HTTP 状态码与业务码对照：

| HTTP | code | 场景 |
|---|---|---|
| 400 | MISSING_TENANT_ID | 开发模式下未提供 tenant_id |
| 401 | UNAUTHORIZED | 鉴权失败（统一话术，不区分 key 不存在/密钥错误，防探测） |
| 403 | FORBIDDEN | scope 不足（如 chat key 调管理面） |
| 404 | SESSION_NOT_FOUND | 会话不存在 / 归属不匹配 / 鉴权模式下未经服务端发号（不区分，防探测） |
| 409 | CONCURRENT_UPDATE | 同一会话并发消息导致状态冲突，调用方应稍后重试 |
| 413 | PAYLOAD_TOO_LARGE | 请求体超过 MAX_REQUEST_BODY_BYTES |
| 422 | VALIDATION_ERROR | 参数校验失败（data 为脱敏后的错误明细，不回显用户原始输入） |
| 429 | RATE_LIMITED | 触发租户级/会话级限流，响应带 Retry-After 头 |
| 403 | ADMIN_TOKEN_REQUIRED | 开发模式管理面未配置 KB_ADMIN_TOKEN（Stage 13：空 token 不再放行） |
| 409 | REQUEST_IN_FLIGHT | 相同 Idempotency-Key 的请求正在处理中（在途占位，Stage 13） |
| 422 | IDEMPOTENCY_KEY_REUSED | 同一 Idempotency-Key 被不同请求体复用（客户端 bug，Stage 13） |
| 500 | INTERNAL_ERROR | 未预期异常（详情不外露，靠 X-Trace-Id 排查） |

示例：

```json
{
  "success": false,
  "code": "CONCURRENT_UPDATE",
  "message": "该会话正在处理另一条消息，请稍后重试",
  "data": null,
  "trace_id": "trace_xxx"
}
```

---

## 5. Route 层要求

```text
1. 只做参数校验。
2. 只调用 ChatService。
3. 不写复杂业务判断。
4. 不直接操作数据库。
5. 返回统一响应结构。
```

---

## 6. 后续 API 规划（按阶段）

| 接口 | 阶段 | 说明 |
|---|---|---|
| `POST /api/chat/sessions` | Stage 04 ✅ 已实现 | 创建会话，服务端发号返回 session_id（body: tenant_id/user_id/channel；发消息接口仍兼容调用方自带 id 的开发模式） |
| `GET /api/chat/sessions/{session_id}/messages?tenant_id=&user_id=&limit=&before=` | Stage 04 ✅ 已实现 | 历史消息分页（created_at 倒序，`before` 为消息 ID 游标），校验会话归属，越权统一 404 |
| `POST /api/chat/sessions/{session_id}/messages/stream` | ✅ 已实现 | SSE 流式回复，事件协议见第 7 节（meta/delta/done/error） |
| `POST /api/kb/documents`、`POST /api/kb/documents/upload`（multipart 文件）、`POST /api/kb/faqs`、`DELETE /api/kb/documents/{id}` | Stage 06 ✅ 已实现 | 知识库管理面接口（配置 KB_ADMIN_TOKEN 后需带 X-KB-Admin-Token 头；请求体见 `app/api/routes/kb.py` 的 Pydantic 模型） |
| `POST /api/product/items` | Stage 06-03 ✅ 已实现 | 商品管理面接口（本地商品库维护，同 KB_ADMIN_TOKEN 保护；见 `app/api/routes/product.py`） |
| `GET /api/handoff/tickets?status=&limit=&before=`、`GET /api/handoff/tickets/{id}`（含上下文移交包）、`POST /api/handoff/tickets/{id}/claim`（并发抢单 409）、`.../reply`（写 role=agent 消息）、`.../resolve`（归还会话，bot 恢复应答） | Stage 07 ✅ 已实现 | 坐席工单 API（admin scope / KB_ADMIN_TOKEN 过渡，见 `app/api/routes/handoff.py`）；用户侧无需新接口——「转人工」走消息主链路自动建单，接管期间 bot 静默（status=HANDOFF_SILENT） |
| `POST /api/chat/sessions/{session_id}/feedback` | Stage 09 ✅ 已实现 | 用户反馈（body: user_id/message_id/rating=up\|down/comment；校验会话归属且 message 属该会话的 AI/坐席回复，重复评价同消息幂等更新） |
| `GET /metrics` | Stage 09 ✅ 已实现 | Prometheus 指标导出（豁免业务鉴权，生产以内网/防火墙限制访问） |
| `WS /api/chat/sessions/{session_id}/ws`、`WS /api/handoff/ws` | Stage 15 ✅ 已实现 | 双端实时通道（事件协议见第 8 节；鉴权开启读 Authorization 头，开发模式 query 参数） |
| `POST /api/chat/sessions/{session_id}/notify` | Stage 15 ✅ 已实现 | 主动消息（admin scope，业务系统回调入口：body content/category；写 assistant 消息 + WS 推送） |

发送消息接口的服务端幂等已实现（Stage 08）：带 `Idempotency-Key` 请求头时重复请求返回缓存响应。

---

## 7. SSE 流式协议（✅ 已实现）

`POST /api/chat/sessions/{session_id}/messages/stream`，请求体与发消息接口一致
（`stream` 字段忽略，走此端点即流式）；响应 `text/event-stream`。

事件序列（成功）：

```text
event: meta
data: {"session_id": "...", "trace_id": "trace_..."}

event: delta            # 0..N 个，每片 ≤24 字符
data: {"text": "回复分片"}

event: done             # 完整结果，字段同发消息接口的 data
data: {"message_id": "...", "session_id": "...", "reply": "...", "intent": "...",
       "status": "...", "state": "...", "slots": {}, "trace_id": "..."}
```

失败（限流除外——限流在建立流之前返回 429 JSON）：

```text
event: error
data: {"code": "SESSION_NOT_FOUND", "message": "会话不存在"}
```

约定：
- 客户端以 `done` 事件为准落最终消息；`delta` 仅用于渐进渲染；
- 当前版本主链路完整执行后分片下发（决策链路不可流式：意图/确认门/工具必须先完成）；
  生成端接入 LLM 流式后仅替换 `delta` 的产生方式，事件协议不变；
- 流式端点不支持 `Idempotency-Key`（幂等语义与流式冲突，客户端自行防重）。


---

## 8. WebSocket 事件协议（Stage 15，✅ 已实现）

服务端只推不收（客户端发的消息一律忽略）；断线即弃无离线队列，重连后拉历史补齐。
校验失败以策略码关闭：4403（无权）/ 4404（会话不存在或越权，不区分防探测）。

用户端 `WS /api/chat/sessions/{session_id}/ws`（开发模式 query：tenant_id/user_id）：

```text
{"type": "agent_reply",     "message_id": "...", "content": "...", "created_at": "..."}
{"type": "session_resumed", "ticket_id": "...", "csat_message_id": "...", "content": "..."}
{"type": "proactive",       "message_id": "...", "category": "logistics_alert", "content": "..."}
```

坐席端 `WS /api/handoff/ws`（开发模式 query：tenant_id/admin_token）：

```text
{"type": "ticket_created", "ticket_id": "...", "session_id": "...", "reason": "...", "source_intent": "..."}
{"type": "user_message",   "session_id": "...", "message_id": "...", "content": "..."}
```

---

## 9. 观测查询 API（Stage 29 批 1，✅ 已实现）

会话记录与决策日志的页面化查询（Web 控制台「观测分析→会话记录」消费）。
**全部只读 + admin scope**（鉴权开启用 admin Key；开发模式 `X-KB-Admin-Token`）；
决策日志/工具审计落库前已脱敏（Stage 13），本组接口原样透出。

| 接口 | 说明 |
|---|---|
| `GET /api/observe/sessions` | 会话列表：`tenant_id`/`user_id`/`status` 过滤，`limit`(≤100)/`offset` 分页，更新时间倒序，返回 `{sessions, has_more}` |
| `GET /api/observe/sessions/{id}/messages` | 消息流（admin 视角免 user_id），时间正序 |
| `GET /api/observe/sessions/{id}/decisions` | 逐轮决策日志：intent_result（含 margin/pending_fill/example_knn）、graph_trace（含 guardrail/meta_shadow）、retrieval/experiment/latency/error 全量 JSON——`replay_trace.py` 的页面化替代 |
| `GET /api/observe/sessions/{id}/tool-calls` | 工具调用审计（mock/MCP/诊断 agent 同表同口径） |

会话不存在/跨租户统一 404（不暴露存在性）。
