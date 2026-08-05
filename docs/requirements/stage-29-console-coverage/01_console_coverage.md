# Stage 29：控制台全接口覆盖 + 观测查询 API

## 1. 阶段目标

Stage 28 落了前端地基后，目标升级为「**所有后端能力都能在页面上看/用**」。
2026-08-05 全量盘点结论：接口共 25 个（HTTP 23 + WS 2），其中**分析类查询
接口存在真实缺口**——聊天记录只有"知道 session_id+user_id 才能查"，
决策日志/工具调用审计只能 CLI（replay_trace.py）或 SQL 直查，页面化无从谈起。

### 接口覆盖矩阵（盘点结果）

| 域 | 接口 | 后端 | 控制台 | 归属批次 |
|---|---|---|---|---|
| chat | 建会话 / 发消息 / 历史分页 | ✅ | ✅ 对话控制台 | 已完成 |
| chat | SSE 流式 | ✅ | ❌ | 批 3 |
| chat | feedback / notify | ✅ | ❌（notify 已在文档给 curl） | 批 2/3 |
| ws | 用户端 / 坐席端 | ✅ | 用户端 ✅ / 坐席端 ❌ | 批 2 |
| handoff | 队列/详情/claim/reply/resolve | ✅ | ❌ 占位页 | 批 2 |
| kb | 写侧 11 个（upsert/upload/审核流/版本/回滚/FAQ） | ✅ | ❌ 占位页 | 批 3 |
| kb | **文档列表 / FAQ 列表查询** | ❌ **缺** | — | 批 3（补后端） |
| product | upsert | ✅（列表查询 ❌ **缺**） | ❌ 占位页 | 批 3（补后端） |
| **观测** | **会话列表查询** | ❌ **缺** | — | **批 1（本批）** |
| **观测** | **会话决策日志查询** | ❌ **缺**（仅 CLI） | — | **批 1** |
| **观测** | **会话工具调用审计查询** | ❌ **缺** | — | **批 1** |
| **观测** | **会话消息（admin 视角，免 user_id）** | ❌ **缺** | — | **批 1** |
| health/metrics | 健康/就绪/Prometheus | ✅ | 登录页用 health | 观测页挂链接（批 3） |

## 2. 分批计划

```text
批 1 ✅ 已实现（2026-08-05，e2e 冒烟通过：三轮对话→列表/决策/审计可查、
      RULE_PENDING_SLOT+pending_fill 证据页面可见、跨租户 404、无令牌 403）：
      观测查询 API + 会话记录页 —— 用户明确诉求：
   「聊天记录查询」「记录日志方便分析」
   后端：新增 observe 路由（admin scope，只读 4 个 GET）：
     GET /api/observe/sessions                     会话列表（user/status 过滤+分页）
     GET /api/observe/sessions/{id}/messages       消息流（admin 免 user_id）
     GET /api/observe/sessions/{id}/decisions      决策日志（intent/来源/margin/
                                                   graph_trace/retrieval/experiment 全量 JSON）
     GET /api/observe/sessions/{id}/tool-calls     工具调用审计
   前端：菜单新增「观测分析 → 会话记录」：会话表格 → 详情抽屉
     （消息流 / 决策日志逐轮展开 JSON / 工具调用三个 Tab）
批 2 ✅ 已实现（2026-08-05）：坐席工作台（队列/上下文包/认领/回复/归还 +
      坐席端 WS 新单通知与用户消息实时刷新）+ 对话控制台 👍👎 反馈；
      e2e：转人工→认领→回复→用户端 WS 实收 agent_reply→归还实收 session_resumed
批 3 ✅ 已实现（2026-08-05）：后端补 3 个 list 接口（kb documents/faqs、
      product items——全状态查询、更新时间倒序、关键词过滤）；
      前端知识库文档页（状态机操作：草稿/提审/通过/驳回/下线/版本回滚/
      上传/直接发布）+ FAQ 页 + 商品页 + 对话控制台 SSE 流式开关
      （delta 渐进渲染、done 落决策标签）+ 系统状态页（health/ready/metrics）；
      e2e：三列表接口 + SSE meta→delta→done 实测通过
```

**至此覆盖矩阵闭环**：25 个既有接口 + 7 个本 Stage 新增（observe 4 +
list 3）全部可在控制台页面使用；占位页清零。

## 3. 技术要点与红线

1. observe 路由**全部只读 + admin scope**（复用 require_admin：鉴权模式
   admin Key / 开发模式 X-KB-Admin-Token）——分析面不能挂在 chat scope 下
   （chat Key 只能看自己 user 的数据，观测面是跨用户的）；
2. 决策日志落库时已脱敏（Stage 13），observe 原样透出即可，**不新增脱敏
   逻辑也不得绕过**；工具调用 request_json 同理（落库前已 mask）；
3. 前端凭证扩展：登录页加可选「管理令牌」字段（对应 X-KB-Admin-Token），
   未填时观测/坐席/知识库等 admin 页给出明确提示而非报错堆栈；
4. 零 migration：全部查询现有表；分页 limit 上限 100 防大响应。

## 4. 验收（批 1）

1. 会话记录页：列表分页/按用户过滤 → 点开任一会话看到消息流与逐轮决策
   （intent、decision_source、confidence、margin、status、graph_trace 展开）；
2. 「我要退款→订单号→确认」一轮走完后，页面上能看到 RULE_PENDING_SLOT
   续接轮与确认门轮的完整决策证据（Stage 26/27 字段可见=分析可用）；
3. admin 令牌缺失/错误 → 页面提示配置方法，不崩；
4. 后端测试覆盖仓储查询与路由序列化；全量零回归。

## 5. 遗留

1. 批 2/批 3（见第 2 节）；
2. 决策日志按 trace_id 全局搜索、按意图/时间聚合视图（等真实流量需求）；
3. observe 分页为 limit/offset 简版，数据量大后换游标。
