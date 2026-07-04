# Stage 04 需求：LLM 接入（混合意图分类 + 回复生成）

> 前置阅读：`docs/chat/intent_taxonomy.md`（意图体系规范，本阶段实现其第 8 节分类器演进的 Stage 04 形态）、
> `docs/architecture/roadmap.md` 3.2 节、`docs/chat/skills_design/guardrails.md`。
> 前置条件：Stage 03 v2 修订已完成（IntentClassifier 已协议化为 async、确认门最小闭环可用）。
> **状态：✅ 已实现（2026-07-02，实现记录与范围调整说明见文末附录）。**

---

## 1. 阶段目标

把 LLM 引入聊天主链路的三个环节——意图分类、槽位抽取兜底、回复生成——同时保证：
规则可短路、LLM 可降级、所有调用有 timeout、决策全部留痕。补齐前端联调必需的会话与历史 API。

## 2. 本阶段要做什么

1. **LLM Provider 工厂**（`app/chat/llm/factory.py`）
   - 基于 langchain-openai 的 ChatModel 封装，兼容 OpenAI 协议的任意后端（`OPENAI_BASE_URL` 可指向本地推理服务）。
   - 强制参数：`request_timeout`（settings，默认 30s）、`max_retries`（默认 1）、温度按用途区分（分类 0、生成 0.7）。
   - 所有 LLM 配置走 `app/core/config.py`，禁止写死；日志禁止输出 API Key 与完整 prompt 中的用户敏感信息。

2. **混合意图分类器**（`app/chat/intent/hybrid_classifier.py`，实现 Stage 03 已定义的 `IntentClassifier` 协议）
   - 第一层：规则分类器（现有 RuleIntentClassifier）——META 控制类与高置信关键词命中直接短路返回。
   - 第二层：LLM few-shot 分类——prompt 必须包含 taxonomy 第 3 节意图清单（含描述）与第 6 节边界裁决表的对抗样例；输出 JSON：`{pred_label, confidence, top_k: [{label, score}]}`，用结构化输出（JSON mode / tool call）保证可解析。
   - 融合策略：LLM confidence < `INTENT_CONFIDENCE_THRESHOLD`（settings，默认 0.6）→ `META.UNKNOWN` 走澄清；LLM 调用失败/超时 → 降级为规则结果（decision_source=LLM_FALLBACK）。
   - 上下文输入：current_state、active_task 摘要、最近 N 轮消息（N=4，settings 可配）。

3. **LLM 槽位抽取兜底**（`app/chat/slots/llm_extractor.py`）
   - 规则抽取先行；当意图的必填槽位仍缺失且文本非裸槽位时，调 LLM 按 Skill 的 slots 声明抽取。
   - 合并规则：规则结果优先（正则命中即信任），LLM 只补空缺，不覆盖。

4. **LLM 回复生成**（`app/chat/skills/llm_responder.py`）
   - 输入：guardrails 全局护栏 + 命中 Skill 的 prompt_fragment（Markdown body）+ 本轮状态（补槽/确认/完成）+ 已收集槽位 + 最近对话。
   - 输出约束：确认门话术必须使用 Skill 声明的 `confirmation_prompt` 语义（可润色不可改事实）；L1-L3 意图禁止编造工具未返回的数据（本阶段无真实工具，回复口径保持「帮您核实后回复」类话术）。
   - 失败降级：LLM 超时/异常 → 回落到 Stage 03 模板回复（responder.py），status 不变。

5. **会话与历史 API**（`app/api/routes/chat.py` 扩展）
   - `POST /api/chat/sessions`：服务端生成 session_id 并返回（body: tenant_id/user_id/channel）。
   - `GET /api/chat/sessions/{session_id}/messages?limit=&before=`：按 created_at 倒序分页拉历史，必须校验租户与用户归属。
   - 现有发消息接口保持兼容；`stream=true` 本阶段仍忽略，SSE 留到后续（在 chat_api.md 标注）。

6. **意图评估集与回归脚本**
   - `docs/testing/intent_eval_set.md`：每个已实现意图 ≥10 条样例（含边界对抗样例）。
   - `tests/eval/test_intent_eval.py`：跑评估集断言域级准确率下限；纯规则部分不依赖外部 LLM 即可运行，LLM 部分用环境变量开关。

## 3. 本阶段不做什么

- 不接真实业务工具（订单/物流查询仍是「帮您核实」话术）——Stage 05。
- 不做 RAG / 向量库 / FAQ 检索——Stage 06。
- 不做流式 SSE 输出（接口字段预留）。
- 不改鉴权模型（tenant_id 仍从请求体读，开发模式）。

## 4. 技术要求

- langchain-openai（此时才正式启用 pyproject 中已声明的依赖）；Pydantic v2 结构化输出。
- 所有 LLM 调用必须有 timeout 与降级路径；降级必须落 decision_log（decision_source 区分）。
- decision_log 新增记录：LLM 原始 top_k、prompt token 用量（latency_json 内加 `llm_ms`、`tokens`）。

## 5. 目录和文件要求

```text
app/chat/llm/factory.py          # Provider 工厂
app/chat/llm/prompts.py          # 分类/抽取/生成 prompt 模板（从 taxonomy 与 Skill 文件组装）
app/chat/intent/base.py          # IntentClassifier 协议（Stage 03 v2 已建，本阶段沿用）
app/chat/intent/hybrid_classifier.py
app/chat/slots/llm_extractor.py
app/chat/skills/llm_responder.py
app/api/routes/chat.py           # 新增会话创建与历史接口
docs/testing/intent_eval_set.md
tests/eval/test_intent_eval.py
```

## 6. 具体实现要求

- 分类器选择通过 settings 开关（`INTENT_CLASSIFIER=rule|hybrid`），图节点通过工厂获取实现，不 import 具体类。
- LLM 分类 prompt 中的意图清单必须程序化地从注册表生成（单一事实来源），禁止手抄一份进 prompt 文件。
- 会话历史注入 LLM 时须做长度截断（最近 N 轮 + 字符上限），避免 token 失控。
- 新 API 沿用统一响应结构与 AppException 错误码（404 SESSION_NOT_FOUND / 403 FORBIDDEN）。

## 7. 代码质量要求

- 核心类与复杂逻辑中文注释；ruff / mypy 通过。
- LLM 调用层单元测试用 fake client，不依赖真实网络。

## 8. 验证方式

1. `INTENT_CLASSIFIER=rule` 时行为与 Stage 03 一致（回归）。
2. `INTENT_CLASSIFIER=hybrid` 下发送规则词表覆盖不到的表达（如「东西还没到我等急了」→ LOGISTICS.TRACK），意图正确且 decision_log 记录 LLM top_k。
3. 断网/错误 API Key 场景：请求不 500，降级为规则分类 + 模板回复，decision_source=LLM_FALLBACK。
4. 创建会话 → 发多轮消息 → 拉历史分页，租户/用户不匹配返回 403/404。
5. 评估集回归：`uv run pytest tests/eval` 通过。

## 9. 执行提示词

```text
请先阅读 AGENTS.md、docs/chat/intent_taxonomy.md、本文档。
本次只实现 Stage 04，按第 2 节逐项实现，第 3 节列出的内容不要做。
完成后说明新增/修改文件、配置项、验证步骤。
```

---

## 附录：实现记录（2026-07-02）

### 与原需求的范围调整（决策记录）

| 原需求 | 实际实现 | 调整理由 |
|---|---|---|
| LLM few-shot 作为语义分类主力 | **SetFit 本地模型为语义主力**（04-02），LLM 降为**低置信难例二判** | 本地模型零 API 成本、延迟 <50ms；LLM 成本只花在难例上 |
| LLM 全量回复生成 | **模板底稿 + LLM 润色**（仅 DONE/FALLBACK 轮次；补槽/确认门保持确定性模板） | 话术即协议：任务流轮次的改写有语义偏移风险；RAG 场景的 LLM 生成已在 answerer 中实现 |
| 流式 SSE | 未实现（原需求即标注本阶段不做，接口字段保留） | — |

### 已实现清单

1. **LLM Provider 工厂**（`app/chat/llm/factory.py`）：ChatOpenAI 封装、按用途温度
   （classify 0 / generate 0.7）、强制 timeout + max_retries=1、`chat_completion()` 统一
   收口异常；`llm_available()` 未配置 Key 即 False，**所有 LLM 路径自动降级，主链路零依赖**。
2. **LLM 难例二判**（hybrid_classifier 第 3 层）：SetFit 低置信 → LLM 从程序化生成的
   意图目录（`app/chat/intent/catalog.py`，taxonomy 的代码投影）+ 边界裁决规则中选择；
   输出严格校验（必须是目录内意图码），失败回落 UNKNOWN；decision_source=LLM。
3. **LLM 槽位抽取兜底**（`app/chat/slots/llm_extractor.py` + slot_extract 节点）：
   必填槽位规则抽不全时补抽；**规则结果优先，LLM 只补空缺**；白名单槽位名过滤。
4. **LLM 回复润色**（`app/chat/skills/llm_responder.py` + response_generate 节点）：
   事实保护双防线——prompt 约束 + 输出校验（底稿中全部数字事实必须原样保留、长度上限），
   违规回退底稿。
5. **会话与历史 API**：`POST /api/chat/sessions`（服务端发号）、
   `GET /api/chat/sessions/{id}/messages?limit=&before=`（created_at 倒序 + 消息 ID 游标，
   会话归属校验，越权统一 404）。
6. **意图评估集**（`docs/testing/intent_eval_set.md` + `tests/eval/test_intent_eval.py`）：
   控制层对抗样例 100% 门禁 + SetFit test 集 accuracy≥0.90 门禁（模型缺失时显式 skip）。
7. **技术债清偿**：db_session 迁出 GraphState，改 LangGraph config 注入
   （`get_db_session_from_config`），state 全部可序列化，为 checkpointer 铺路。

### 配置（.env.example 已同步）

```text
LLM_REPLY_ENABLED=true / LLM_INTENT_SECOND_OPINION=true / LLM_SLOT_FALLBACK=true
LLM_HISTORY_MAX_TURNS=4
（以上均在 OPENAI_API_KEY 为空时自动失效，行为与 Stage 03 模板模式一致）
```

### 验证记录

- 单测 35 个全过（fake LLM：二判采纳/无效输出拒绝/无 Key 降级、槽位白名单、
  润色事实保护/确认门跳过）；ruff/mypy 通过；
- e2e：创建会话 → 三轮对话（商品价格/退款确认门）→ 历史倒序分页 + 游标翻页 →
  越权 404 → 无 Key 模板回归，全部通过。

### 遗留

```text
1. 真实 LLM 端点联调（配 OPENAI_API_KEY 后二判/槽位/润色即生效，建议先在 dev 验证
   润色话术质量与二判准确率，从 decision_log 抽检 decision_source=LLM 样本）。
2. 流式 SSE（chat_api.md 第 6 节规划保留）。
3. 回复润色的对话历史注入（prompt 已支持，等 checkpointer/记忆机制落地后接入）。
```
