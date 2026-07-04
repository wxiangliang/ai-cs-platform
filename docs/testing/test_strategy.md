# 测试策略（v3，2026-07-04 更新）

> v1 全靠手工 curl 验证；v2 起以**自动化测试为主门禁**，手工 e2e 作为阶段验收补充。
> 任何代码合入前必须全绿：`uv run ruff check app tests` + `uv run mypy app` + `uv run pytest`。

---

## 1. 自动化测试资产（当前 200 个用例）

| 目录 | 覆盖内容 | 依赖 |
|---|---|---|
| `tests/kb/` | 分块器（标题路径/大表分片重复表头/图片合块/碎片合并）、hash embedding、RRF 融合、RAG 回答策略（FAQ 命中/拒答/摘录降级）、文档解析（md→Block、MinerU content_list 映射、xlsx 解析） | 无外部依赖（fake 向量与后端） |
| `tests/intent/` | 混合分类器（控制层短路/语义层/低置信兜底/模型不可用降级）、**注册表覆盖守护**（SetFit 29 类标签空间必须全部有 Skill） | 无（fake SetFit） |
| `tests/llm/` | LLM 二判（采纳/无效拒绝/无 Key 降级）、槽位兜底白名单、回复润色（事实保护/确认门跳过） | 无（fake LLM） |
| `tests/stage05/` | 任务栈（挂起/恢复/上下文槽位继承/清栈/深度上限）、ActionExecutor 三重校验与防重放、mock 工具确定性、确认应答解析（MODIFY 白名单） | 无 |
| `tests/eval/` | **评估门禁**：控制层对抗样例 100% + SetFit test 集 accuracy≥0.90（模型缺失显式 skip）+ 多意图切分 + RAG 检索 32 组（阈值双档） | SetFit 模型产物（可选）/ Milvus |
| `tests/stage07/` | 转人工闭环：幂等建单、并发 claim、bot 静默短路、resolve 归还、UNKNOWN 连击、五类 reason 触发 | 真实 PG |
| `tests/stage08/` | 鉴权（bcrypt/scope/缓存）、限流窗口、幂等（指纹 422/在途锁 409/超限不缓存） | 真实 PG+Redis |
| `tests/stage09/` | 指标 before/after、反馈归属校验与幂等、导出脱敏与训练集排除 | 真实 PG |
| `tests/stage10/` `tests/stage11/` | 多意图/任务治理/记忆；MCP provider（发现/降级策略/TTL 重发现） | 真实 PG / fake MCP |
| `tests/stage12/` | Langfuse 工厂降级与 trace metadata | 无 |
| `tests/stage13/` | 生产加固：配置门禁、防重放并发（恰好执行一次）、吊销版本、脱敏、L3 弱确认、建单 SAVEPOINT | 真实 PG+Redis |
| `tests/stage14/` | 护栏：规则加载、注入/违禁/情绪/灌注、输出护栏、**训练语料 500 条零误拦扫描**（规则改动回归防线） | 真实 PG+Redis |
| `tests/stage15/` | CSAT 解析与闭环、会话重开/空闲关闭/超时工单、排队位置、WsHub Pub/Sub 投递；**功能审查整改回归**（护栏误伤/短词灌注/csat 拦截轮清除/僵尸任务/feedback 并发/幂等锁 CAD/RAG 名次摘录/商品编码） | 真实 PG+Redis |

## 2. 评估门禁（改分类器/检索必跑）

- 意图：`docs/testing/intent_eval_set.md`——控制层对抗样例全绿 + test 集准确率不回退；
- 检索：`docs/testing/rag_eval_set.md`（✅ 已落地 32 组含拒答样例，`tests/eval/test_rag_eval.py`
  执行门禁，阈值双档：hash 低档 / 真实 embedding 高档）；
  当前阈值按 hash 开发向量/当前 SetFit 模型标定，**换真实模型必须重标**。

## 3. 手工 e2e 验收清单（各阶段文档附录有完整记录，此处为回归抓手）

启动：`INTENT_CLASSIFIER=hybrid uv run uvicorn app.main:app`（开发模式另配
`FAQ_HIT_THRESHOLD=0.6 RAG_MIN_SCORE=0.2`，见 ops runbook）。

```text
1. 确认门执行闭环：我要退款 订单号A1 → 确认 → 回执含工单号；
   chat_task=DONE、chat_tool_call 有 create_refund_ticket 记录。
2. 任务挂起/恢复：确认门中问「先帮我查下这个订单的物流」→ 挂起+继承订单号+
   真实轨迹回复+续办提示 → 「确认」恢复执行。
3. 检索路由：会员积分怎么用（FAQ 命中）/ 凉风空调X1多少钱（商品库价格）/
   今天天气如何（拒答）/ 补槽轮次 decision_log 无 retrieval_json。
4. 工具查询：帮我查下订单 C1 的状态 → mock 事实数据（非「帮您核实」）。
5. 安全回归：跨租户访问历史 404、空消息 422、「取消订单」≠「算了」、
   重复「确认」不重复执行（防重放）。
6. 降级回归：无 OPENAI_API_KEY 一切正常（模板模式）；停 Milvus 仅 RAG 降级、
   主链路不受影响（ready 探针 kb_milvus=down）。
```

## 4. 约定

```text
1. 新功能必须带单元测试；修 bug 先补能复现的测试再修。
2. LLM/外部系统一律用 fake/monkeypatch，测试不依赖网络；
   真实联调场景写进阶段文档附录的「遗留」清单。
3. 测试数据确定性优先（mock 工具同入参恒同输出、hash embedding 确定性向量）。
4. Stage 09 ✅ 已实现：评估门禁进 CI（`.github/workflows/ci.yml`）、bad case 回流
   （`scripts/export_review_set.py` → 人工审核 → `build_intent_dataset.py --extra` → 重训 → 门禁）。
```
