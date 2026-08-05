# Stage 32 需求：选品顾问与商品对比（PRODUCT.RECOMMEND / COMPARE 实做）

> 来源：`docs/ai_customer_service_skill_pack_v1/` 的 stage-31（Playbook）、
> stage-32（选品顾问）、stage-35（购买辅助）三份需求，结合本仓库取舍。
> 关键盘点发现：**`PRODUCT.RECOMMEND`（830 训练样本）与 `PRODUCT.COMPARE`
> （832 样本）已是 SetFit 训练意图、已注册 taxonomy、已有 skill——但均为
> 空壳**（不收槽位、不查商品库、回一句客套话即 DONE）。本 Stage 不新增
> 意图、不动分类器，把两个空壳实做为真实能力。

---

## 1. 对包三份需求的取舍

### 1.1 stage-32 选品顾问 → **本 Stage 主体**

| 包要求 | v1 落法 |
|---|---|
| 模糊需求→结构化约束 | `PRODUCT.RECOMMEND` 补 required_slots=[category, budget]（use_scene 可选）——任务状态机收集，一条 collect 模板一次问齐两项（包原则「一轮最多两个高度相关问题」） |
| 动态槽位/最大信息增益提问 | v1 固定两槽位（品类+预算就是对候选集影响最大的两个硬约束）；动态增益提问等真实品类丰富后 |
| 商品候选召回 + **硬约束过滤** | `product_repository.search_by_constraints`：status=active + stock>0 + category/名称匹配 + price≤budget，**SQL 层过滤，不满足硬约束的商品根本不进候选** |
| 规则排序 | 价格升序 top 4（预算内从低到高，无商业权重——包红线「商业权重不得覆盖用户需求」结构性满足：根本没有商业权重输入） |
| trade-off | 候选 ≥2 时给「最低价 vs 最高配」两端提示（只引用结构化字段） |
| 实时价格库存核验 | 事实直接来自 product_item 表（价格库存唯一事实源，Stage 06-03 红线沿用：**禁走 RAG/LLM**） |
| LLM 仅做表达 | v1 连表达都不用 LLM——确定性模板列候选（数字事实零编造空间）；润色仍走既有 polish 通道（有事实校验） |
| 推荐过程落证据 | 候选/约束/命中数落 `retrieval` 轨迹（product_answer 既有模式）进决策日志 |

### 1.2 stage-35 购买辅助 → **只取「商品对比」**

`PRODUCT.COMPARE` 实做：required_slots=[compare_items]（两款名称，
「A和B」「A vs B」切分）→ 逐个走 ProductProvider 精确/名称检索 →
结构化对比（价格/库存/参数/简介）确定性模板。找不齐两款如实说明
（宁缺勿编），不猜测。其余（blocker 识别/下单引导）推迟：
下单经确认门的结构已由 ORDER.CREATE + ActionExecutor 覆盖，
「引导进入下单」属主动动作归 NBA 轴（Stage 31），等真实转化数据。

### 1.3 stage-31 Playbook Engine → **不建第二引擎，逐条映射到既有机制**

包验收条目与本仓库机制一一对应（这就是「已实现」的证明，不再造轮子）：

| 包 Playbook 概念 | 本仓库既有机制 |
|---|---|
| Playbook Registry / Instance | SkillRegistry 声明 + active_task（task_id/collected_slots） |
| step transition / slot state | 任务状态机 IDLE→COLLECTING→(CONFIRMING)→DONE + required_slots 顺序 |
| 可暂停/可恢复 | task_stack 挂起恢复 + 上下文槽位继承（Stage 05/10），恢复不重问已收集槽位 |
| 超时/放弃/失败 | TASK_TTL_MINUTES / META.ABORT·任务中途否定 / TASK_MAX_ASKS 追问超限 |
| ToolRequest / ActionRequest | ToolProvider 协议 / ActionExecutor 唯一写入口+确认门 |
| 写并发恰好一次 | claim_for_execution 条件 UPDATE（Stage 13） |
| 每步可回放 | chat_decision_log 逐轮 + replay_trace.py |
| 无 LLM 可运行 / 默认关闭语义等价 | 全链路无 Key 降级既有纪律；本 Stage 无新开关（见第 3 节） |

选品顾问就是第一个用这套「playbook=skill 声明」跑通的多轮引导流。
将来若出现「跨任务分支流程」（如注册引导的验证码重试分支）再评估独立引擎。

## 2. 槽位与理解层（零重训）

- 分类不动：RECOMMEND/COMPARE 是既有训练意图，SetFit 直接路由；
- 抽取新增（`app/chat/slots/patterns.py` + extractor）：
  - `budget`：「预算3000」「3000以内/以下/左右」「不超过3000」，支持 千/万/k/w
    后缀，归一化为元（整数）；
  - `category`：常见家电/3C 品类词表（沙盒商品域）；词表未命中 → collect
    模板追问，回答由 Stage 26 pending_fill（contextual_answer 证据）接住；
  - `compare_items`：「A和B」「A跟B」「A vs B」整段捕获，节点内切分；
- 一次性表达直达：「预算三千以内推荐个风扇」两槽位同轮抽满 → 免追问直接出候选。

## 3. 默认开启的理由（与 30/31 默认关不同）

本 Stage 是**响应式读能力**：用户显式要求推荐/对比才触发（P4 用户主动请求，
不是 P5/P6 系统主动），无写操作、无营销、无模型行为变化——今天这两个意图
命中后回的是「方便说下预算吗」死胡同客套话，实做后是真实候选，纯粹变好。
故不设开关，作为普通功能阶段交付（同 Stage 06-03 商品问答先例）。

## 4. 红线（沿用+新增）

1. 价格/库存/候选**只来自 product_item 表**，禁 RAG、禁 LLM 生成（既有红线）；
2. 不满足硬约束（预算/有货/上架）的商品**不进候选**（SQL 层保证）；
3. 无命中/服务异常**宁缺勿编**：如实说明并给调整建议，不伪造候选；
4. 推荐理由只引用结构化字段（价格/库存/attrs/简介原文），无主观形容词生成；
5. 排序无商业权重输入（v1 价格升序；将来加权需过本文档修订）。

## 5. 验收标准

1. 「帮我推荐一款风扇，预算300以内」→ 一轮出候选（价格全部 ≤300、有货、
   风扇类），按价格升序，附 trade-off 与对比引导；
2. 「帮我推荐个商品」→ 追问品类+预算（一条模板问齐）→ 分轮补齐后出候选；
3. 预算内无货 → 如实拒绝并建议调整（决不越预算展示）；
4. 「对比凉风X1和凉风X2」→ 两款结构化对比；只找到一款 → 如实说明；
5. 选品中途插入售后意图 → 任务挂起，处理完恢复不重问已收集槽位
   （既有 task_stack 回归覆盖）;
6. 候选与约束落 retrieval 轨迹，决策日志可回放。

## 6. 遗留

1. 动态槽位/信息增益提问（品类丰富后）；
2. use_scene/preference 参与排序（现只收集不排序——排序输入透明红线）；
3. NBA 低风险动作 OFFER_PRODUCT_COMPARE（选品完成后主动建议对比——
   归 Stage 31 主动轴，影子先行）；
4. 购买辅助其余（blocker 识别/下单引导）；
5. 训练数据侧：RECOMMEND/COMPARE 样本与新回复路径的一致性回看
   （回流机制既有）。
