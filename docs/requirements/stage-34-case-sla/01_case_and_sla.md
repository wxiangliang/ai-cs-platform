# Stage 34 需求：Case 工单与 SLA 治理（含 Service Recovery 并入）

> 来源：roadmap 3.7 服务闭环规划第一项（外部评审最高优先项采纳）。
> 核心区分：**Task 是「当前对话在办什么」，Case 是「客户问题从创建到彻底
> 解决的完整服务记录」**——跨会话、跨渠道长期存在。今天系统只知道
> 「这一轮回答了什么」，有了 Case 才知道「问题到底解决没有」。

---

## 1. Case 实体（`chat_service_case`，新表 + migration）

| 字段 | 说明 |
|---|---|
| case_no | 人读编号 `CS<yyyymmdd><uuid8>` |
| user_id / case_type / priority | 客户 · 类型（触发映射见第 2 节）· LOW/NORMAL/HIGH |
| status | 9 态：OPEN / IN_PROGRESS / WAITING_CUSTOMER / WAITING_INTERNAL / WAITING_EXTERNAL / RESOLVED / CLOSED / REOPENED / ESCALATED |
| owner_type / owner_id | AI / HUMAN（认领后） |
| sla_due_at | 按优先级算：HIGH 4h / NORMAL 24h / LOW 72h（`CASE_SLA_HOURS_*` 可配） |
| related_json | {sessions[], tickets[], tasks[], orders[]} —— 跨会话关联 |
| resolution_code / resolved_at / closed_at / reopen_count | 解决口径与生命周期时间戳 |
| metadata_json | 升级记录 / 补偿评估快照 / 备注 |

幂等合并（评审「同类问题合并」）：**同客户同类型同时最多一个活跃 Case**
（部分唯一索引，handoff「同会话一张未关闭工单」先例）——再次触发 →
并入既有 Case（追加 session/ticket 关联，新触发更高优先级则提级并重算 SLA）。

## 2. 自动创建：一个收口点覆盖全部触发

**不改五个触发方**：`handoff_service.ensure_ticket` 内建单成功后统一
开/并 Case（转人工工单是 Case 的一次人工协作动作，不是 Case 本身）：

| 触发（既有） | case_type | priority |
|---|---|---|
| USER_REQUEST / MANUAL | GENERAL_SUPPORT | NORMAL |
| PAYMENT_ISSUE | PAYMENT_ISSUE | HIGH |
| EXECUTION_FAILED | EXECUTION_FAILURE | HIGH |
| REPEATED_UNKNOWN | UNRESOLVED_QUERY | NORMAL |
| SKILL_RULE | POLICY_REVIEW | NORMAL |
| ABUSE | ABUSE | NORMAL |
| （新增）CSAT ≤ 2 | SERVICE_QUALITY | NORMAL |

关联提取：会话 id、ticket id 必带；订单号从移交包槽位尽力提取。
Case 创建失败**绝不打断建单/主链路**（SAVEPOINT + 日志告警）。

## 3. 生命周期与坐席 API（admin scope，`/api/cases`）

```text
GET  /api/cases                     队列（状态/类型/用户过滤 + 分页）
GET  /api/cases/{id}                详情（含关联与 SLA 余量）
POST /api/cases/{id}/assign         认领（owner=HUMAN，状态→IN_PROGRESS）
POST /api/cases/{id}/status         流转（仅允许合法迁移，见状态机表）
POST /api/cases/{id}/resolve        解决（必填 resolution_code）
POST /api/cases/{id}/close          关闭
POST /api/cases/{id}/reopen         重开（RESOLVED/CLOSED → REOPENED，reopen_count+1，SLA 重算）
GET  /api/cases/{id}/compensation   补偿资格评估（只读，见第 5 节）
```

合法迁移由服务层白名单表校验（非法迁移 409）；跨租户/不存在 404。

## 4. SLA 治理（cron，复用 deploy/scheduler）

`scripts/case_sla_check.py`（`CRON_CASE_SLA_INTERVAL`，默认 10 分钟）：

- **超时升级**：活跃 Case `sla_due_at < now` → status=ESCALATED +
  priority 提至 HIGH + metadata 记升级事件（幂等：已 ESCALATED 不重复）；
- **即将超时提醒**：剩余 < 25% → 指标计数（坐席推送记遗留）；
- 指标 `service_cases_total{event}`（opened/merged/resolved/reopened/escalated/sla_warning）。

## 5. Service Recovery：结构化补偿政策（并入本阶段）

`configs/compensation_policies.json`（示例文件入库，实配 gitignore 同 campaigns）：

```jsonc
[{
  "policy_code": "DELIVERY_DELAY_V1",
  "case_types": ["EXECUTION_FAILURE", "GENERAL_SUPPORT"],
  "min_priority": "NORMAL",
  "compensation_type": "COUPON",
  "max_value": 100,
  "requires_approval": true
}]
```

`GET /api/cases/{id}/compensation`：按政策表判定 →
`{eligible, policy_code, compensation_type, max_value, requires_approval,
reason_codes[...]}`，评估快照写入 case metadata（审计可追溯）。

红线（评审采纳，与既有纪律同构）：**LLM 不参与补偿判定**（纯政策表）；
**v1 只做资格判定不做发放**——补偿发放是写操作，AI 端执行需要意图流 +
确认门 + 唯一写入口，随真实优惠券系统对接时补（遗留 1），当前由坐席在
真实系统操作并回填 resolution_code。

## 6. 验收标准

1. 五类转人工触发 + CSAT 低分 → Case 自动开/并；同客户同类型并入不重开；
2. 生命周期：非法迁移 409；resolve 必带 resolution_code；reopen 计数与 SLA 重算；
3. SLA：超时 Case 被 cron 升级为 ESCALATED + HIGH（幂等）；
4. 补偿：政策匹配/不匹配/需审批三态 reason_codes 正确，评估快照落 metadata；
5. Case 创建失败不影响建单与主链路（fail-open）；
6. 迁移可升可降（回滚演练纪律）。

## 7. 遗留

1. 补偿发放执行流（真实优惠券系统 + 意图流 + 确认门）；
2. Web 控制台 Case 页（随下一批前端）；
3. 即将超时的坐席实时推送（WS agents channel）；
4. 部门/技能组指派（现仅 AI/HUMAN + owner_id）；
5. 渠道维度（channel 字段随多渠道接入）；
6. quality_daily 增 Case 解决率/SLA 达标率列（真实流量后）。
