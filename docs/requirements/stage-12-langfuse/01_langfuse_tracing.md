# Stage 12 需求：Langfuse 链路追踪

> 前置阅读：`docs/architecture/system_overview.md` 第 4 节（图结构）、
> `docs/requirements/stage-09-observability/`（Prometheus 指标——两者互补：
> Prometheus 管聚合指标与告警，Langfuse 管**单次调用级**的链路明细与 LLM 观测）。

---

## 1. 阶段目标

给聊天主链路加上 Langfuse 追踪：每轮对话一条 trace，图节点为 span，
LLM 调用（分类二判/槽位兜底/润色/RAG 生成/确认门解析/记忆摘要）自动记录
prompt/completion/token 用量，与业务 trace_id、session、user、tenant 关联——
排查"这轮为什么这么答"时可视化下钻，评估 LLM 成本与延迟分布。

## 2. 本阶段要做什么

1. **接入方式**：LangChain `CallbackHandler`（langfuse v4，OTel 基座）挂到
   LangGraph `ainvoke` 的 run config——节点 span 与嵌套 LLM 调用自动捕获，
   **不改任何节点代码**；`chat_completion`（LLM 统一收口）同样挂 handler，
   覆盖图外调用（记忆摘要等异步路径）。
2. **关联字段**：trace 带 `langfuse_session_id`（会话）、`langfuse_user_id`、
   tags（tenant/channel）与业务 `trace_id`（metadata），可从 X-Trace-Id 反查。
3. **配置与降级（红线）**：`LANGFUSE_ENABLED` + `LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST`
   走 settings；**未配置 Key / SDK 导入失败 / 上报异常一律静默降级**，
   绝不影响主链路（与 LLM/Milvus/Redis 同一韧性原则）。上报是后台批量异步的，
   不增加请求延迟；应用关停时 flush 缓冲。
4. **脱敏口径**：Langfuse 收到的是 prompt/回复原文（产品定位如此，自托管部署时数据不出内网）；
   tags/metadata 不放手机号等槽位值。生产用云版需评估合规，建议自托管。

## 3. 本阶段不做什么

- 不做 Langfuse 的 prompt 管理 / 数据集评估功能（后续按需）；
- 不替代 decision_log（决策日志是业务事实来源，Langfuse 是观测工具，可关）；
- 不做采样率控制（量大再加 `LANGFUSE_SAMPLE_RATE`）。

## 4. 目录和文件要求

```text
app/core/tracing.py          # Langfuse client/handler 工厂 + 降级 + flush
app/services/chat_service.py # run_config 挂 callbacks + 关联 metadata
app/chat/llm/factory.py      # chat_completion 挂 handler
app/main.py                  # lifespan 关停 flush
tests/stage12/               # 工厂降级、config 组装
```

## 5. 验证方式

1. 无 Key（默认）：全量测试通过，行为零变化；
2. 配置 Key + 本地/云 Langfuse：跑一轮混合对话，UI 可见 trace（节点 span 树 + session/user/tags）；
3. Key 配错/服务不可达：请求正常返回，仅日志告警。

---

## 附录：实现记录（2026-07-03）

| 项 | 位置 | 说明 |
|---|---|---|
| 工厂与降级 | `app/core/tracing.py` | `langfuse_enabled()`（开关+双 Key）；`_init_client()` lru_cache 一次初始化、失败缓存 None 不反复重试；`get_langfuse_handler()` 每请求轻量创建；`shutdown_langfuse()` flush |
| 主链路接线 | `app/services/chat_service.py` | `graph.ainvoke` 的 run_config 挂 callbacks + `run_name=chat_turn` + metadata（langfuse_session_id/user_id/tags=tenant,channel + 业务 trace_id）——节点 span 与嵌套 LLM 调用自动捕获，节点代码零改动 |
| LLM 收口接线 | `app/chat/llm/factory.py` | `chat_completion` 挂 handler（`run_name=llm_{purpose}`）；图内经 OTel 上下文归入本轮 trace，图外（记忆摘要异步路径）自成 trace |
| 关停 flush | `app/main.py` lifespan | shutdown 阶段调用（未启用为空操作） |
| 配置 | `LANGFUSE_ENABLED/PUBLIC_KEY/SECRET_KEY/HOST`（config.py + .env.example） | 默认 Key 为空即禁用，零开销 |
| 依赖 | langfuse 4.13（OTel 基座） | mypy/ruff 通过 |
| 测试 | `tests/stage12/test_langfuse_tracing.py`（4 例） | 无 Key 禁用/开关禁用/有 Key 出 handler/metadata 字段 |

验证：全量 116 tests 通过（默认无 Key 零回归）；假 Key + 不可达 host e2e——聊天请求正常
返回（NEEDS_CONFIRM 流程完整），仅 OTel exporter 后台告警与关停时 flush 重试，主链路零影响。
遗留：采样率（量大再加）；云版合规评估（生产建议自托管 Langfuse）。
