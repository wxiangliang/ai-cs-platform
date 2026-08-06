# Stage 40 需求：行为准则层（Behavior Guidelines，借鉴 Parlant）

> 评估依据：`docs/architecture/parlant_evaluation.md`。借鉴的是 Parlant 的
> **condition-action 准则模型与每轮动态注入**思想；不引入其框架（两个大脑/
> 成本模型冲突，评估报告第 5 节）。定位一句话：**护栏管「绝不允许」
> （拦截），准则层管「应该怎样」（引导）**——越界即删。

---

## 1. 解决什么问题

「客户不满时先具体共情再问细节」「不得承诺退款到账时效」「推荐不超过
3 款不空洞夸」这类**业务行为规范**，今天要么写死在模板里，要么散落在
skill md 的 forbidden/response_format 里只对人可见，要么全局塞进润色
prompt——堆多了就是 Parlant 批判的「系统提示词不可扩展：指令越多遵循
度越差」。本 Stage 把它们数据化，**每轮只注入命中的少数几条**。

## 2. 设计（对照 Parlant 逐点取舍）

| Parlant 机制 | 本仓库落法 |
|---|---|
| Guideline（condition-action） | `configs/guidelines.json`（campaigns 模式：mtime 缓存 / example 入库 / 实配 gitignore / 损坏 fail-open 空表）。字段：id / condition{intents, states, emotion, keywords, tenants} / action / criticality / exclusion_group |
| 每轮 LLM 评估条件 | **v1 全规则匹配零 LLM 成本**（它也支持 custom matcher，我们直接从这起步）：condition 各维度 AND、维度内 OR、空维度不限；embedding/LLM 匹配是 v2（进 token 预算） |
| criticality | HIGH/NORMAL/LOW 排序 + **注入条数封顶** `GUIDELINES_MAX_INJECT=3`（对应其「长 action 增加延迟」教训，也是防提示词膨胀的硬闸） |
| Exclusion 关系 | exclusion_group：同组只取 criticality 最高一条（平级取先声明） |
| 注入位置 | **只进 LLM 增强路径**：润色（llm_responder）/ RAG 生成（answerer）/ 智能澄清（llm_clarifier）。确定性模板路径不注入（不需要）；无 Key 时注入点本不存在=天然无效 |
| 可解释性（每轮记录命中） | 命中 id 经 turn 级 contextvar 收集器 → save_turn 落 `graph_trace_json.guidelines`（零 GraphState/契约改动）；`guidelines_injected_total{criticality}` 指标 |
| 多租户 | condition.tenants（空=全租户）——**Parlant 没有的能力**，每租户话术规范 |

## 3. 红线

1. 准则层不做拦截（那是护栏的职责边界）；不含事实数据（价格/政策数字
   永远来自工具/知识库）；
2. 注入条数封顶 + criticality 分级——防重蹈系统提示词膨胀；
3. 命中率可观测：decision_log 可按 id 聚合，长期无人命中的准则定期清理
   （运营纪律写进 example 文件头注释）；
4. 默认 `GUIDELINES_ENABLED=false` 零回归。

## 4. 种子准则（盘点自 skill md forbidden/response_format 与既有纪律）

example 文件带 8 条可用种子：情绪共情（negative flag）/ 不承诺退款时效
（AFTERSALE 域）/ 投诉先具体道歉（COMPLAIN）/ 推荐克制（RECOMMEND：
不空洞夸、说适合谁）/ 对比不拉踩（COMPARE：不绝对评价）/ 澄清一次一问
（UNKNOWN）/ 预约时间明示时区（APPOINTMENT 域）/ 租户敬语示例（tenant
维度演示）。

## 5. 验收

1. 匹配：各维度 AND/维度内 OR/空维度不限/域前缀/租户过滤全覆盖测试；
2. 排序与封顶：criticality 降序、同 exclusion_group 去重、超限截断；
3. 注入：三个 LLM 路径 system prompt 追加准则块；关闭/空表/无命中零改动；
4. 留痕：命中 id 落 graph_trace_json.guidelines；
5. 全量零回归（默认关）。

## 6. 遗留

1. embedding/LLM 匹配器（难例语义条件，进 Stage 17 预算）；
2. 坐席草稿注入（随 Agent Assist 启动）；
3. 准则命中率/违反率看板 SQL（真实流量后）；
4. Strict/Fluid per-skill 输出模式字段（现有 polish 开关够用，观察）。
