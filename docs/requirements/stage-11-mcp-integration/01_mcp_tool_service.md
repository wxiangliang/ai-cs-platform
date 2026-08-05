# Stage 11 需求：MCP 业务工具服务与集成

> 前置阅读：`stage-05`（ToolProvider 抽象与工具审计）。
> **状态：✅ 已实现（2026-07-03，实现与验证记录见文末）。**
> 目标：把订单查询/物流查询以 **MCP（Model Context Protocol）标准工具服务**对外提供，
> 客服平台作为 MCP 客户端调用——模拟并打通真实业务系统的标准接入形态。

---

## 1. 架构

```text
客服平台（本项目）                     MCP 业务工具服务（scripts/run_mcp_server.py）
tool_invoke / ActionExecutor            FastMCP，streamable-http（默认 :8400/mcp）
  → ToolProvider 抽象                     @tool query_order            ← 对接真实业务系统时
    TOOL_PROVIDER=mcp                     @tool query_logistics_track     替换函数内部为真实
    McpToolProvider ── MCP 协议 ──────→   （当前数据源=共享确定性 mock）   DB/RPC，平台侧零改动
       │
       └─ 回落 mock：①服务未声明的工具（写操作等）②MCP 调用失败/超时（degraded 标记）
```

设计要点：

1. **落在既有 ToolProvider 抽象上**：聊天链路（tool_invoke / ActionExecutor / 审计表）
   零改动，只是 factory 多一个 `mcp` 实现——这正是 Stage 05 抽象层的验证。
2. **组合式回落**：MCP 服务通过 `list_tools` 动态发现工具清单（缓存）；
   未覆盖的工具（写操作类）自动走进程内 mock；**MCP 故障回落 mock 并打
   `degraded` 标记**（韧性原则：外部依赖不打断主链路），发现失败缓存带
   60 秒 TTL——服务恢复后自动重新接管。
3. **数据对拍**：MCP 服务当前数据源与进程内 mock 共享同一确定性生成器
   （`app/chat/tools/mock_data.py`），同入参两条路径产出完全一致，切换 provider
   不影响测试断言；对接真实业务系统时只改 MCP 服务端工具函数内部。
4. 每次调用独立建 streamable-http 会话（无状态、进程重启零影响），
   本地实测单次调用约 40-50ms；高并发场景的连接复用列为遗留优化。

## 2. 配置与运行

```bash
# 启动 MCP 业务工具服务
uv run python scripts/run_mcp_server.py --port 8400
# 客服平台切换到 MCP 工具
TOOL_PROVIDER=mcp uv run uvicorn app.main:app
```

```text
TOOL_PROVIDER=mock|mcp     # 默认 mock
MCP_SERVER_URL=http://localhost:8400/mcp
MCP_TIMEOUT=10
```

## 3. 验证记录（2026-07-03，全部通过）

- McpToolProvider 直连：query_order/query_logistics_track 走 MCP 协议且**数据与
  mock 逐字段对拍一致**；写工具回落 mock（无降级标记）；
- 故障场景：服务不可达 → 全量回落 mock + `degraded=mcp_unreachable_fallback_mock`；
  调用失败 → 回落 + `degraded=mcp_fallback_mock`；发现失败 TTL 重试后自动接管恢复；
- 聊天全链路（TOOL_PROVIDER=mcp）：「查订单状态」「物流到哪了」经 MCP 返回真实数据
  （审计表 latency 37-46ms，MCP 服务端日志 26 次协议请求）；退款执行写工具回落
  进程内（0.07ms），确认门闭环不受影响；
- 单测 5 个（累计 92）：协议路由、未覆盖回落、调用失败降级标记、
  发现失败全量降级、TTL 重发现恢复。

## 4. 遗留

```text
1. 真实业务系统对接：替换 MCP 服务端工具函数内部实现（平台侧零改动）；
   届时按需扩充 MCP 工具清单（写操作是否经 MCP 暴露由业务安全策略决定，
   平台侧 ActionExecutor 唯一写入口红线不变）。
2. MCP 连接复用/会话池（当前每调用建会话，本地 ~40ms 可接受）。
3. MCP 服务自身的鉴权（当前内网裸跑；生产参照 MCP 规范的 auth 或网络隔离）。
```

## 5. 对接约定：大工具结果红线（Stage 20 增补）

真实业务系统的工具若返回大结构（订单列表、物流全程明细、批量查询结果等），
**MCP 服务端必须返回面向回复的结论字段**（如 `summary` + 回复所需的关键字段），
原始大 JSON **不得**原样进入平台侧 `tool_facts` / 回复润色 prompt——
LLM 上下文里只放结论，不放原文（对应业界 L1「大结果外置」模式）：

- 需要完整数据时由服务端落库存引用（refId 等），按需另查，不经 LLM 搬运；
- 平台侧注入点有字符上界，超长原文进 prompt 既稀释注意力也可能被截断丢关键字段；
- 本约定为**对接红线**，v1 不做平台侧强制校验（服务端自律 + 联调评审把关）。

---

## 6. 真实系统对接的端到端幂等契约（2026-08-05 生产就绪审计补充）

**问题**：我方侧已是「至多一次」（ActionExecutor 原子拿执行权 + 失败不自动
重试转人工），但「我方调用超时、外部系统实际已提交」的场景，此前只能靠
人工核实。端到端幂等需要外部系统配合去重。

**契约（对接真实业务系统时必须实现）**：

```text
1. 客户端义务（✅ 已实现）：所有写操作 params 携带 idempotency_key = task_id
   （chat_task 主键，一次确认唯一对应一个 key；重试/人工补发同 key）；
2. 服务端义务（对接方实现）：按 (tenant_id, idempotency_key) 去重——
   同 key 重复请求返回首次执行结果（含原工单号），不产生重复业务动作；
   去重记录保留期 ≥ 7 天（覆盖人工核实周期）；
3. 对账通道：chat_tool_call 审计表有每次调用的 task_id/请求/响应/结果，
   与外部系统按 idempotency_key 对账即可定位「执行了但没回执」的单。
```

mock 端 `ticket_data` 按 (tenant, tool, order_id) 哈希生成工单号，天然满足
同输入同结果，联调期即可验证客户端键传递（tests/stage29 锁定）。
