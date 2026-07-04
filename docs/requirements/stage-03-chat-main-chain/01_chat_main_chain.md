# Stage 03：聊天主链路

本阶段实现第一版 AI 客服聊天主链路。

---

## 1. 阶段目标

用户调用：

```http
POST /api/chat/sessions/{session_id}/messages
```

系统可以完成：

```text
1. 保存用户消息
2. 读取或初始化会话状态
3. 执行 LangGraph 基础节点
4. 识别第一版意图
5. 抽取基础槽位
6. 更新 DialogState
7. 生成一个基础 AI 回复
8. 保存 AI 回复
9. 保存 decision_log
10. 返回统一响应
```

---

## 2. 本阶段不做

```text
1. 不接 RAG。
2. 不接 FAQ。
3. 不接向量数据库。
4. 不接真实商品工具。
5. 不接真实订单工具。
6. 不接真实售后工具。
7. 不执行真实写操作。
8. 不训练模型。
9. 不实现复杂 LLM Judge。
```

---

## 3. 目录要求

新增或修改：

```text
app/api/routes/chat.py
app/schemas/chat.py
app/services/chat_service.py

app/chat/graph/
  builder.py
  state.py
  nodes/
    load_session_state.py
    preprocess_message.py
    guardrail_check.py
    intent_classify.py
    slot_extract.py
    dialog_state_resolve.py
    skill_resolve.py
    response_generate.py
    save_turn.py

app/chat/intent/
  rule_classifier.py
  types.py

app/chat/slots/
  extractor.py
  patterns.py

app/chat/state/
  manager.py
  types.py

app/chat/skills/
  registry.py
  types.py

app/chat/logging/
  decision_logger.py
```

---

## 4. LangGraph 节点

实现以下节点：

```text
load_session_state
preprocess_message
guardrail_check
intent_classify
slot_extract
dialog_state_resolve
skill_resolve
response_generate
save_turn
```

节点职责：

```text
load_session_state：读取或初始化会话状态
preprocess_message：清洗、归一化用户消息
guardrail_check：预留护栏检查
intent_classify：规则意图识别
slot_extract：基础槽位抽取
dialog_state_resolve：状态机流转
skill_resolve：根据 final_intent 选择 Skill
response_generate：生成模板回复
save_turn：保存消息和 decision_log
```

---

## 5. 第一版意图（v2 修订）

> 意图码以 `docs/chat/intent_taxonomy.md` 为单一事实来源。
> v2 变更：`META.HANDOFF_REQUEST` 更名为 `META.TRANSFER_HUMAN`（废弃别名见 taxonomy 2.1）；
> 新增 `ORDER.CANCEL`（修复「取消订单」被误判 META.ABORT）与
> `META.CONFIRM / META.DENY`（修复确认门死循环，仅 CONFIRMING 状态输出）。

```text
PRODUCT.ASK_PRICE
PRODUCT.ASK_INFO
PRODUCT.ASK_STOCK
ORDER.QUERY_STATUS
ORDER.CANCEL          ← v2 新增（写操作，确认门）
LOGISTICS.TRACK
AFTERSALE.REFUND
AFTERSALE.RETURN
AFTERSALE.EXCHANGE
AFTERSALE.COMPLAIN
META.TRANSFER_HUMAN   ← v2 更名（原 META.HANDOFF_REQUEST）
META.BOT_IDENTITY
META.ABORT
META.SLOT_ONLY
META.CONFIRM          ← v2 新增（仅 CONFIRMING 状态）
META.DENY             ← v2 新增（仅 CONFIRMING 状态）
CHITCHAT.GENERAL
CHITCHAT.THANKS
META.UNKNOWN
```

---

## 6. Rule Intent Classifier

实现：

```text
app/chat/intent/rule_classifier.py
```

要求返回：

```json
{
  "pred_label": "AFTERSALE.REFUND",
  "confidence": 0.9,
  "decision_source": "RULE_KEYWORD",
  "top_k": []
}
```

规则要求：

```text
1. 退款/退货/换货/投诉等高风险意图优先识别。
2. 转人工、取消、算了等 META 意图优先级高。
3. 低置信返回 META.UNKNOWN。
4. 中文注释说明每类规则。
```

v2 补充要求（与 taxonomy 第 4 节判定优先级一致）：

```text
5. 「取消订单/取消这单」等带订单宾语的表达必须先于裸「取消/算了」判定，归 ORDER.CANCEL。
6. classify 接口是上下文敏感的 async 协议（app/chat/intent/base.py）：
   输入含 current_state / has_active_task；
   仅当 current_state == CONFIRMING 时才允许输出 META.CONFIRM / META.DENY。
7. 纯槽位（SLOT_ONLY）判定必须至少含一位数字，防止 "thank you" 等纯字母短语误判。
```

---

## 7. SlotExtractor

实现：

```text
app/chat/slots/extractor.py
```

第一版支持：

```text
order_id
phone
product_name
color
quantity
```

要求：

```text
1. 使用规则和正则。
2. 不依赖向量数据库。
3. 不调用 LLM。
4. 中文注释说明。
```

---

## 8. DialogStateManager

实现：

```text
app/chat/state/manager.py
```

第一版状态：

```text
IDLE
COLLECTING
CONFIRMING
DONE
ABORTED
HANDOFF
FAILED
```

规则：

```text
1. 如果用户说退款但没有 order_id，进入 COLLECTING。
2. 如果 active_task 正在等待 order_id，用户下一句只发订单号，要续接原任务。
3. 如果用户说“算了/不用了/取消”，进入 ABORTED。
4. 如果用户说“转人工/找客服”，进入 HANDOFF。
5. 写操作暂时不执行，只进入 CONFIRMING 或 COLLECTING。
6. 读操作可以 DONE。
7. （v2）CONFIRMING 下用户确认（META.CONFIRM）→ 状态 DONE、本轮 status=CONFIRMED、
   任务清空，回复「受理回执」话术（不承诺已执行——真实执行在 Stage 05）；
   否认（META.DENY）→ ABORTED、任务清空。修复 v1 确认门死循环。
8. （v2）状态机返回的 active_task 必须是新构造的 dict，禁止原地修改传入任务
   （原地改会破坏 SQLAlchemy JSONB 变更检测，导致补槽结果不落库）。
```

---

## 9. 回复生成

第一版使用模板回复，不强依赖外部 LLM。

示例：

```text
PRODUCT.ASK_PRICE 缺 product_name：
  请问您想咨询哪款商品的价格？

AFTERSALE.REFUND 缺 order_id：
  请提供需要退款的订单号。

META.TRANSFER_HUMAN：
  好的，我会为您转接人工客服。

写操作确认门通过（status=CONFIRMED，v2 新增）：
  已受理您对订单「{order_id}」的退款申请，我们核实后会尽快为您处理并反馈结果。

META.DENY（确认门否认，v2 新增）：
  好的，本次操作不会提交。还有什么可以帮您？

CHITCHAT.GENERAL：
  您好，有什么可以帮您？

META.UNKNOWN：
  我需要再确认一下，您是想咨询商品、订单、物流还是售后问题？
```

---

## 10. API 要求

实现：

```http
POST /api/chat/sessions/{session_id}/messages
```

请求参考：

```text
docs/api/chat_api.md
```

Route 层只调用 `ChatService`，不写复杂业务。

---

## 11. Decision Log

必须保存：

```text
original_text
normalized_text
intent_result_json
slot_result_json
selected_skill
status
decision_source
graph_trace_json
latency_json
error_json
```

第一版可以简化，但字段入口必须保留。

---

## 12. 代码质量要求

```text
1. 所有关键逻辑必须有中文注释。
2. Route 层只做参数接收和调用 Service。
3. Service 层协调 Repository 和 Graph。
4. Graph 节点只处理单一职责。
5. 不要把所有逻辑写在一个文件。
6. 不要接 RAG、FAQ、向量数据库。
7. 不要接真实订单/商品/售后 API。
8. 所有异常要走统一异常处理。
9. 保存 decision_log，便于后续训练和排查。
```

---

## 13. 验证方式

启动：

```bash
uv run uvicorn app.main:app --reload
```

测试：

```bash
curl -X POST "http://localhost:8000/api/chat/sessions/s_001/messages" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": -99,
    "user_id": "u_001",
    "message": "我要退款",
    "channel": "web",
    "stream": false
  }'
```

预期：

```text
intent = AFTERSALE.REFUND
status = NEEDS_SLOT（本轮处理状态）
state  = COLLECTING（状态机状态）
reply = 请提供需要退款的订单号。
```

> 注意区分两个枚举（v2 澄清）：`status` 是单轮处理状态（NEEDS_SLOT / NEEDS_CONFIRM /
> CONFIRMED / DONE / HANDOFF / ABORTED / FALLBACK / FAILED），`state` 是会话级状态机状态
> （IDLE / COLLECTING / CONFIRMING / DONE / ABORTED / HANDOFF / FAILED），不要混用。

v2 补充验证（确认门闭环，全部必须通过）：

```text
1. 「我要退款」→「订单号 A12345678」→「确认」：
   三轮依次 NEEDS_SLOT/COLLECTING → NEEDS_CONFIRM/CONFIRMING → CONFIRMED/DONE，
   受理回执含订单号；第二轮结束后 chat_dialog_state.active_task_json 中必须已含 order_id。
2. 确认门下回复「先不了」→ META.DENY / ABORTED，任务清空。
3. 「我要取消订单」→ ORDER.CANCEL（不得判为 META.ABORT）。
4. "thank you" → CHITCHAT.THANKS（不得判为 META.SLOT_ONLY）。
5. 用其他 tenant_id/user_id 访问已有会话 → 404 SESSION_NOT_FOUND。
6. 空白消息 → 422 VALIDATION_ERROR（不进主链路）。
7. 「转人工」→ chat_session.status 同步变为 handoff。
```

---

## 14. Codex 执行提示词

```text
请先阅读根目录 AGENTS.md，
再阅读 docs/requirements/stage-03-chat-main-chain/01_chat_main_chain.md，
并参考 docs/api/chat_api.md、docs/database/chat_tables.md、docs/architecture/system_overview.md。

本次只实现 Stage 03 聊天主链路。
严格按文档实现，不要超范围实现。
完成后说明新增文件、修改文件、启动方式、curl 测试方式和验证结果。
```

---

## 附录：v2 修订记录（2026-07-02）

v1 实现评审发现并已修复的问题（代码与本文档已同步修改）：

| 级别 | 问题 | 修复 |
|---|---|---|
| P0 | 确认门死循环：CONFIRMING 下「确认」无意图可接，机器人永远重复确认话术 | 新增 META.CONFIRM/DENY 上下文意图 + 状态机规则 7；受理回执模板（templates.confirmed） |
| P0 | JSONB 原地变更：续接补槽时 active_task 更新不落库 | 状态机不可变更新（规则 8）+ 模型 JSONB 列启用 MutableDict/MutableList |
| P0 | 跨租户/跨用户会话劫持：load_session_state 不校验归属 | 会话归属校验，不匹配返回 404 SESSION_NOT_FOUND |
| P1 | 并发冲突裸 500（乐观锁 StaleDataError / 首轮 IntegrityError） | ChatService 捕获后转 409 CONCURRENT_UPDATE |
| P1 | 节点异常时整轮无痕（消息与决策日志全回滚） | 失败轮次用独立事务写带 error_json 的 decision_log |
| P1 | 「取消订单」误判 META.ABORT 并回复「已为您取消」 | 新增 ORDER.CANCEL 意图（写操作确认门），取消订单正则先于裸「取消」 |
| P1 | 空消息把状态机打成 FAILED | 护栏拦截不改状态机；Schema message min_length/strip 校验，422 拦截 |
| P1 | "thank you" 误判 SLOT_ONLY；手机号被抽成订单号 | slot-only 必须含数字；订单号裸匹配前剔除手机号 |
| P1 | 意图码与 Skill 文档不一致 | META.HANDOFF_REQUEST → META.TRANSFER_HUMAN（对齐 taxonomy） |
| P1 | DB 无语句级超时；分类器无抽象接口 | engine connect_args 加 command_timeout；IntentClassifier async 协议（app/chat/intent/base.py） |
| P1 | 转人工后 chat_session.status 仍为 active | save_turn 联动更新为 handoff |
| P2 | 422 响应回显用户原始输入（敏感）；alembic URL 含 % 会被 configparser 破坏；根目录残留脚手架 main.py | 校验错误脱敏；URL 转义 %%；删除残留文件 |

已知遗留（记录在案，按阶段处理）：

```text
1. db_session 仍放在 GraphState 中传递——Stage 04 引入 checkpointer 前必须改为
   LangGraph config 注入（见 stage-04 文档前置条件）。
2. tenant_id/user_id 仍从请求体传入（开发模式）——Stage 08 鉴权后从凭证解析。
3. task_stack_json / context_stacks_json 建表未启用——Stage 05 任务挂起/恢复时启用。
4. latency 只记录 total_ms——Stage 04 起按节点细分。
```
