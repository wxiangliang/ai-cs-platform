# Parlant 评估报告（2026-08-06）

> 评估对象：https://github.com/emcie-co/parlant （Emcie 出品）
> 评估问题：有什么值得学习借鉴？我们什么场景用得上？要不要引入？
> 结论先行：**不引入框架，借鉴核心思想自研「行为准则层」**（候选 Stage 40，
> 已入 roadmap 3.7 backlog）。理由见第 4/5 节。

---

## 1. Parlant 是什么（事实卡）

| 项 | 事实 |
|---|---|
| 定位 | 客服型 LLM Agent 的**对话行为控制框架**（Agentic Behavior Modeling） |
| 解决的问题 | 系统提示词不可扩展（指令越多遵循度越差）、路由图复杂后脆弱——用**每轮动态上下文工程**替代两者 |
| 核心机制 | Guidelines（条件-行为对，每轮 LLM 评估条件、只注入命中的）· Journeys（多轮 SOP 可跳可回溯）· 工具挂 guideline（条件命中才可调）· Canned Responses（关键时刻模板替代生成=零幻觉）· Glossary · ARQs（结构化推理，arXiv:2503.03669） |
| 形态 | 独立 async server + Python SDK（`p.Server()`/`create_agent()`），拥有自己的会话管理与对话主循环；React 聊天组件；OpenTelemetry 全链路 |
| 成熟度 | 18.2k stars / 1.5k forks / 5400+ commits，银行等强监管行业有生产部署 |
| 许可证 | Apache-2.0（与本仓库一致，兼容） |
| 依赖特性 | Python 3.10+；模型无关（OpenAI/Anthropic/LiteLLM）；**guideline 匹配默认每轮走 LLM**（可自定义 matcher：regex/embedding/DB） |

## 2. 核心哲学对照：两条相反的路线

```text
Parlant：LLM-first 生成 + 运行时行为约束
  自由生成为主体 → 每轮动态匹配注入行为准则拉住模型
本仓库：规则-first 确定性管线 + LLM 增强
  模板/状态机为主体 → LLM 只在润色/二判/澄清等增强点出现，无 Key 全降级
```

两者都是对「纯 prompt 工程不可靠」的回应，方向相反。**我们的路线在写操作
安全与成本可控上更强**（确认门/唯一写入口是结构保证，Parlant 的工具调用
仍由 LLM 决策触发）；**Parlant 在自由生成占比高的场景更强**（生成内容的
行为一致性有系统化机制，我们目前只有护栏红线+事实校验）。

## 3. 机制逐项对照（我们已有什么、缺什么）

| Parlant 机制 | 本仓库对应物 | 差距评估 |
|---|---|---|
| Canned Responses | **模板-first 就是我们的默认形态**（responder + 确认门话术不经生成） | 无差距，我们更彻底 |
| 工具挂条件（防假正例调用） | skills 声明 required_tools/actions + 确认门 + 只读白名单 | 无差距，我们是结构保证更强 |
| Journeys（多轮 SOP） | 任务状态机 + task_stack 挂起恢复 + TTL/MAX_ASKS（Stage 32 已论证不建二套引擎） | 基本无差距；其「可跳步/回溯」对应我们的遗留「任务中途工具机制」 |
| 可解释性（每轮记录命中了哪些规则） | decision_log + reason_codes + replay_trace | 决策轴无差距；**生成轴有差距**（润色/RAG 生成注入了什么约束没有逐轮留痕） |
| Glossary | query_normalize 同义词 + intent catalog | 基本覆盖 |
| **Guidelines 每轮动态匹配注入** | **无对应物**——prompt_fragment 按 skill 静态注入，护栏红线全局恒注入 | **核心差距**，见第 4 节 |
| Guideline 关系（exclusion/dependency）+ criticality 分级 | 无 | 随准则层一起借鉴 |
| ARQs 结构化推理 | 二判/确认解析已是受限输出，但无结构化推理链 | 真实 LLM 接入后可参考其论文 |
| Strict/Fluid 输出模式切换 | polish 开关 + 数字事实校验回退 | 形态相近，可形式化为 per-skill 字段 |

## 4. 值得借鉴的核心思想：行为准则层（建议自研，候选 Stage 40）

我们唯一的真实差距：**「业务行为准则」目前要么写死在模板里，要么全局
塞进护栏红线**。「客户表达不满时先具体共情再问细节」「不得承诺退款到账
时效」「企业客户用敬语」这类租户级话术规范，今天没有系统化的存放与
生效机制——加多了就是 Parlant 批判的「系统提示词不可扩展」问题。

借鉴 Parlant 的 condition-action 模型，按本仓库纪律落地：

```text
1. 准则数据化：guidelines 机器可读表格（guardrails.md 先例——单一事实
   来源，运营改表不改代码），字段：condition（意图域/状态/情绪/关键词
   等结构化条件）/ action（一句行为指令）/ criticality / 互斥组 / 租户；
2. 匹配器 v1 全规则（零 LLM 成本零延迟——Parlant 也支持 custom matcher，
   我们直接从这里起步）：按本轮 intent/state/emotion/关键词命中；
   embedding 匹配是 v2，LLM 匹配只给难例且进 token 预算（Stage 17 纪律）；
3. 注入点只在 LLM 增强路径：润色 / RAG 生成 / 智能澄清 /（未来）坐席
   草稿——**确定性模板路径不注入**（不需要）；按 criticality 排序 +
   注入条数封顶（对应其「长 action 增加延迟」的教训）；
4. 留痕：命中的准则 id 落 graph_trace_json.guidelines（补齐第 3 节
   「生成轴可解释性」差距），Langfuse span 可见；
5. 默认关（零回归），无 Key 天然无效（注入点本来就不存在）。
```

**多租户是我们的差异化机会**：Parlant 是单 agent 配置形态；我们的准则表
天然带 tenant 维度——每租户自己的话术规范，这是它没有的能力。

## 5. 为什么不建议直接引入框架

1. **两个大脑问题**：Parlant 是拥有对话主循环/会话存储的独立 server，
   引入即与 LangGraph 管线抢会话所有权；工具调用由其 LLM 决策触发，
   与「确认门 + ActionExecutor 唯一写入口」结构冲突——我们最强的安全
   资产会被绕过或需在其内部重建；
2. **成本模型冲突**：guideline 匹配默认每轮每条走 LLM 评估，与零 Key
   可跑、token 预算熔断、轮级时间预算的既有纪律相悖；
3. **重复建设**：多租户/鉴权/审计/决策日志/回放/质量看板都要在其体系
   重做一遍；
4. **沙盒收益低**：其价值在自由生成占比高 + 真实流量的场景，我们当前
   生成占比被刻意压低（模板-first）；
5. 许可证无障碍（Apache-2.0 ↔ Apache-2.0），排除项不是法务而是架构。

**可选的低成本接触方式**：真实 LLM 接入后，用 Parlant 跑同场景对照
（行为一致性基准），或在坐席 Agent Assist（本来就是生成型、非用户直面、
错误代价低）做 sidecar 试点——都不动主链路。

## 6. 应用场景判断（什么时候这套东西对我们真正起作用）

| 场景 | 时机 | 借鉴生效点 |
|---|---|---|
| 合规话术约束（不承诺时效/费用表述/禁贬竞品） | 真实 LLM 润色/RAG 生成开启后 | 准则层注入 + criticality=HIGH |
| 租户话术定制（称呼/语气/行业术语） | 多租户真实客户接入 | 租户维度准则表 |
| 情绪场景行为（不满→先共情后询问） | 现有 emotion flag 已就位 | 准则层第一批种子规则 |
| 坐席草稿约束（Agent Assist） | Stage 34 遗留启动时 | 草稿生成 prompt 注入 |
| 行为一致性评估基准 | 真实流量 + Key | Parlant sidecar 对照实验 |

## 7. 风险与边界

- 准则层自研的最大风险是**重蹈系统提示词膨胀**——靠三条守住：条数封顶、
  criticality 分级、准则命中率/违反率观测（无人命中的准则定期清理）；
- 不把准则层变成第二个护栏：护栏管「绝不允许」（拦截），准则层管
  「应该怎样」（引导），越界即删；
- ARQs/attentive reasoning 论文（arXiv:2503.03669）在真实 LLM 联调时
  值得精读，可能改进二判与确认解析的 prompt 结构。

## 8. 建议行动

1. ✅ 本报告入库（本文件）；roadmap 3.7 backlog 增「行为准则层
   （Guidelines v1，借鉴 Parlant）」候选 Stage 40——前置：真实 LLM Key
   （无生成即无注入点价值）；
2. 种子准则可先攒：把散在 skill md `forbidden`/`response_format` 里的
   行为性条目盘点成准则表初稿（数据准备不依赖前置）；
3. 真实 LLM 接入后：准则层 v1（规则匹配版）+ Parlant 对照基准实验。
