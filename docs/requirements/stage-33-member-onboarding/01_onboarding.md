# Stage 33 需求：会员注册引导（new_user_onboarding，mock 版）

> 来源：`docs/ai_customer_service_skill_pack_v1/skills/new_user_onboarding/`
> 结合本仓库取舍。前提澄清：平台无真实注册/验证体系（`user_id` 调用方直传），
> 本 Stage 用 **mock 会员工具**把机制链路整条打通——对接真实会员系统时
> 只需替换工具内部实现（Stage 05/11 抽象层的既定路线）。

---

## 1. 三件套设计（全部复用既有机制，零新机制）

### 1.1 Mock 会员工具（工具目录 + MockToolProvider）

| 工具 | readonly | 行为（mock） |
|---|---|---|
| `query_member_status` | ✅ | 按 tenant+user 查是否已注册（进程内 MEMBERS 集合）；注册后再查返回已注册+会员号——**闭环**：NBA 不再重复建议 |
| `register_member` | ❌ 写 | 校验手机号槽位 → 生成确定性会员号（digest）→ 入 MEMBERS；**恒走 ActionExecutor 唯一写入口**（防重放/幂等键/审计既有纪律全继承） |

工具目录（post-stage-27 单一事实来源）同步登记：readonly 声明自动进
诊断 agent 白名单推导；MCP 服务端暂不加（`TOOL_MCP_FALLBACK` 下写操作
恒走 mock、读操作服务未覆盖回落 mock——行为正确，服务端补齐记遗留）。

### 1.2 MEMBER.REGISTER 意图与技能（用户主动路径）

- **新增 MEMBER 域**（taxonomy 第 10 域）+ `MEMBER.REGISTER`（L2 写操作）；
- **规则层触发（零重训）**：「注册会员/开通会员/我要注册」确定性短路
  （ORDER.CANCEL 先例）。三条防误伤：否定前缀（「不想开通会员」）不触发；
  第三方平台语境（「怎么注册抖音账号」——mode hard test 的 OOS 样本）不触发；
  裸「注册」只接受短句形态。语义层兜底：意图进 LLM 二判目录
  （catalog 程序化生成），「我还是想开个会员」这类改写有 Key 时可被二判接住；
- **技能**：WRITE kind，required_slots=[phone]（既有手机号正则直接复用），
  槽位齐 → **确认门 CONFIRMING**（「将以手机号 {phone} 注册会员，确认吗？」）
  → ActionExecutor 执行 `register_member` → 回执含会员号。
  **写操作绝不因引导语境跳过确认门**（包红线「无确认下单」同族）。

### 1.3 NBA 主动触发（START_ONBOARDING，包 stage-33 低风险动作第一批）

- 位置：`decide_proactive` 活动候选**之前**（获客优先于营销）；
- 条件：`PROACTIVE_ONBOARDING_ENABLED`（默认 true，受主动服务总开关双关
  管控=默认不生效）+ 有稳定 user_id + `query_member_status` 返回**未注册**
  （查询失败/不可用 → 跳过不建议，fail-closed 不骚扰）+ 共享全局抑制矩阵
  （退款/投诉/确认门/负面情绪/闲聊轮禁提）+ 共享会话频控/冷却/opt-out
  + 客户级建议上限（impression 键 campaign=`onboarding`）；
- 话术引导**显式回复「注册」**（确定性入口走规则层触发，不做弱确认解析——
  L3 弱确认收紧同哲学）；拒绝语冷却与活动共用（「不用推荐」冷却一切主动建议）。

## 2. 对包 new_user_onboarding 的取舍

| 包要求 | 决策 |
|---|---|
| 多轮注册/验证/资料引导 Playbook | v1 = phone → 确认门 → 注册（复用任务状态机）。**验证码环节推迟**：需要「任务中途调用工具再继续收集」的新机制（现有工具调用都在回复分支），等真实会员系统的验证形态定了再做，不为 mock 发明机制 |
| 旅程阶段（REGISTERED/…） | 推迟（journey 属长期画像，Stage 31 已记录）；「已注册不再建议」由 query_member_status 闭环承担 |
| 基础资料引导（昵称/偏好） | 推迟——注册后资料补全是低价值追问，等真实字段需求 |
| 引导中断可恢复 | 既有 task_stack 挂起/恢复直接覆盖（选品同款） |

## 3. 验收标准

1. 主动路径：未注册用户业务办完（DONE 轮）→ 回复尾部追加注册建议
   （APPLY 开启时）；已注册/查询失败/矩阵抑制 → 不追加；
2. 用户主动路径：「我要注册会员」→ 收手机号 → 确认门 → 回执含会员号；
   再问 query_member_status=已注册，NBA 不再建议；
3. 「怎么注册抖音账号」不触发（OOS 语义不劫持）；「不想开通会员」不触发；
4. 写操作过确认门+防重放+审计（ActionExecutor 既有测试面覆盖）；
5. 默认（主动服务双关）零回归：规则触发的用户主动路径独立可用。

## 4. 遗留

1. 验证码环节（真实会员系统对接时随任务中途工具机制一起做）；
2. MCP 服务端补 member 工具（当前 mock 兜底行为正确）；
3. 注册成功后的旅程阶段起点（journey 依赖）；
4. MEMBER.REGISTER 训练样本回流（规则触发日志→意图数据集，README 第 5 节流程）。
