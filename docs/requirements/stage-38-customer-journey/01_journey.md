# Stage 38 需求：客户旅程（Customer Journey）

> 来源：skill pack `customer_journey` 规格（skills/README 落点表最后一块）。
> 定位：**跨会话的客户阶段**，为 NBA 提供长期上下文；**不直接驱动写操作**
> （包红线采纳）——旅程只影响「要不要建议/推什么」，永不影响确认门与执行。

---

## 1. 阶段与推进规则（v1 全规则，证据分级采纳）

阶段序（单调 rank，弱证据不能倒退也不能跳高风险阶段）：

```text
NEW → REGISTERED → DISCOVERING → CONSIDERING → READY_TO_BUY → PURCHASED
                                                    AT_RISK = 叠加标记（非阶段）
```

对包 11 阶段的取舍：VISITOR/NEW_USER 合并为 NEW（沙盒无匿名访客态）；
REGISTERING 是瞬时态不落库；REPEAT_CUSTOMER/AFTER_SALES 并入 PURCHASED
（signals 里留证据，细分待真实订单量）；AT_RISK 从阶段改为**叠加布尔**
——「已购客户在闹退款」应同时保留 PURCHASED 与风险标记，不互斥。

推进证据（决策日志本轮信号 → 目标阶段，强证据才跳高阶段）：

| 本轮信号 | 目标 | 强度 |
|---|---|---|
| MEMBER.REGISTER 执行成功（CONFIRMED） | REGISTERED | 强 |
| PRODUCT.ASK_INFO / RECOMMEND | DISCOVERING | 弱 |
| PRODUCT.ASK_PRICE / COMPARE / ASK_STOCK | CONSIDERING | 弱 |
| ORDER.CREATE | READY_TO_BUY | 强 |
| ORDER.QUERY_STATUS / LOGISTICS.* / AFTERSALE.*（有订单可问=已购） | PURCHASED | 强 |
| AFTERSALE.COMPLAIN / REFUND / CSAT≤2 | at_risk=true（叠加） | 强 |
| RESOLVED Case / CSAT≥4 | at_risk=false（解除） | — |

规则：`new_stage = max(rank(当前), rank(目标))`（旅程不倒退）；
弱证据目标 rank 若跳超当前 +1 只推进一格（防抖）；同会话同阶段目标
不重复写（包防抖采纳）。

## 2. 存储与接入

- `customer_journey` 表（migration）：tenant+user 唯一，stage / at_risk /
  signals_json（近 20 条转移史 {turn 信号, from, to, at}）/ 时间戳；
- **更新收口 save_turn**（本轮意图+状态推导，SAVEPOINT fail-open 不打断
  主链路；无 user_id 跳过）；更新先于 NBA 决策——本轮阶段立即可用；
- **消费点**：
  1. 活动资格：campaign 配置新增可选 `eligible_journey_stages`
     （包 campaign_example.yaml 字段照收）——声明了而客户阶段未知/不符
     → 不推（营销保守方向）；
  2. NBA 决策证据：reason_codes 加 `journey:<stage>`，落 decision_log
     可回放；
  3. 观测：`GET /api/observe/journeys/{user_id}`（admin）。

## 3. 红线

1. 旅程不驱动写操作（只影响主动建议相关性）；
2. v1 全规则推导，不用 LLM 猜阶段；
3. AT_RISK 客户的营销由既有抑制矩阵挡（高风险流程/负面情绪），
   旅程标记是分析口径不是新的营销开关。

## 4. 遗留

1. REPEAT_CUSTOMER/AFTER_SALES 细分（真实订单量后）；
2. 流失挽回 Playbook 消费 at_risk（roadmap 3.7 backlog 项的前置现已就位）；
3. 移交包/Case 附旅程阶段；低置信候选转移（包「防抖」高级形态）。
