# 功能正确性审查与整改记录（2026-07-04）

Stage 01-15 全部落地后的一次全局功能审查（两路只读代理 + 人工核实），
专找会答错/漏处理/数据不一致/边界炸的功能性缺陷（非生产加固，那轮见 Stage 13）。
本文件是整改台账；每条整改都有 `tests/stage15/test_audit_fixes.py` 回归用例。

## 已整改

| # | 问题 | 影响 | 整改 |
|---|---|---|---|
| 1 | 护栏 INJ-003 误伤「你现在是不是…/你现在是什么…」等高频口语 | 正常提问被当注入拦截 | 收紧正则：要求「你现在是」后接角色引导（一个/我的/不受限），保留客服/人工负向前瞻；INJ-001「要求」词过泛已移除 |
| 2 | 重复灌注对短确认词跨轮误拦 | 补槽/确认连答「是/好的」第 3 次被拦 | `check_repeat_flood` 跳过 <5 字的短消息 |
| 3 | csat_pending 在被拦截轮不清除 | 违反「询问一次性失效」，泄漏到后续轮污染 CSAT | save_turn 对 blocked（非评分非静默）轮显式清 csat_pending |
| 4 | RAG 纯关键词命中 score=0 被拒答阈值误杀 / 摘录选不中 | 型号/单号精确查询命中正确资料却拒答或摘错段（抵消 v2 关键词提权设计） | retriever 标记 from_keyword；answerer 精确查询有关键词命中不因低向量分拒答，摘录改用融合名次 hits[0] |
| 5 | resolve/定时关单留 CONFIRMING/EXECUTING 僵尸 chat_task 行 | 清 dialog 引用后 TTL 自愈够不到，永不终结 | 新增 `abort_open_by_session`，resolve/close CLI 同步标 ABORTED |
| 6 | executor claim 后主事务回滚 → 卡 EXECUTING → 重试谎报「已提交成功」 | 既未成功又告知成功 | 区分并发在途（confirmed_at≈now→ALREADY_EXECUTED）与被中断（confirmed_at 过期→EXECUTION_INTERRUPTED 走执行失败建单，交人工核实） |
| 7 | SSE 流式端点无幂等 | 断线重连重复落库/重复状态流转 | 对齐普通路径：Idempotency-Key 命中缓存以 done 回放 + 在途占位；提交成功后才缓存 |
| 8 | feedback 并发双提交撞唯一索引 → 500 | 用户连点两下点踩报错 | SAVEPOINT 包裹 create，IntegrityError 回查改更新（幂等） |
| 9 | 幂等在途锁无条件 delete → 超时后删他人锁 | 并发保护失效 | compare-and-delete（Lua，仅删本请求指纹的锁） |
| 10 | WS 事件在事务提交前发出 | 回滚后幽灵工单/消息、事件先于数据可见 | `ws_hub.publish_after_commit(session, …)`：挂 after_commit 事件，提交成功才广播，回滚不发 |
| 11 | 商品 product_id/编码走名称 ILIKE 模糊匹配 | 精确编码常查不出 | provider 先 get_by_code 精确匹配，未命中再名称检索 |

## 复核确认无问题（未改动）

query_normalize 繁简/同义词、加权 RRF 合并、父子分块行级去重、FAQ hit_count 原子自增、
歧义检测、价格/库存红线贯彻、MCP fail 降级语义、handoff reply/resolve 对已终结工单、
guardrail 拦截不动状态机、多意图 LIFO 恢复、限流先判后写、metrics label 基数、
replay_trace/export_review_set SQL、幂等 finally 结构（acquire 在 try 外，409 不误删锁）、
弱确认降级不推进 ask_count、abuse 连击与 handoff 静默时序、closed 重开与 csat 顺序。

## 已知限制（评估后保留，非缺陷）

- **超长会话（>500 条）摘要中段丢失**：`_maybe_summarize` 用绝对下标，会话极长时中段既不进摘要也不在短期窗口。空闲会话 24h 自动关闭已大幅降低触发面；真需支持超长会话再改增量游标。
- **reindex 不删除停用文档的残留向量**：检索侧靠 PG status 过滤兜住（不产生错答），属存储不一致；换 embedding/大改后跑全量重建即可。
- **PENDING 工单长期无人认领 → 会话保持 handoff 静默**：设计上 PENDING 不自动关单（避免丢单），坐席需监控队列深度/时长（quality_queries.md 第 2 节 SQL）。运维应对 PENDING 积压告警，而非靠自动关单。
- **通知监听任务全断开后仍常驻**：资源占用极小，进程 shutdown 时清理；避免频繁重建抖动，有意保留。
- **读工具返回空 dict 判为全失败**：走「未查询到」兜底话术，不编造，可接受。

## 验证

全量 200 tests 通过（新增 `test_audit_fixes.py` 15 例）；ruff/mypy 干净；
e2e 复验：护栏误伤消除且注入仍拦、短确认连答不误拦、双端 WS after-commit 事件秒达、
SSE 幂等回放不重复落库。
