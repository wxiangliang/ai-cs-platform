# Stage 36 需求：事件驱动主动客服

> 来源：roadmap 3.7 第三项。范式补全：从「用户发消息 → 系统工作」补上
> 「业务事件发生 → 系统判断是否需要联系客户」。这不是意图识别，是
> **事件触发的主动服务**——NBA 基建（退订/冷却/审计纪律）的另一半。

---

## 1. 链路

```text
业务系统（webhook）→ POST /api/events（admin scope）
  → 事件路由（event_type 白名单映射）
  → 幂等（event_id Redis SETNX 7 天——同一事件只通知一次）
  → 退订/冷却检查（复用 proactive:optout / proactive:cool——用户说过
    「以后都别推」对服务通知同样生效）
  → 静默时间检查（EVENTS_QUIET_HOURS，默认关）
  → 渠道定位（该用户最近会话；无会话=无渠道，如实记录不硬发）
  → **发送前重查最新事实**（映射声明 verify_tool，经工具层查实时状态——
    旧事件绝不带着过期事实发出；查询失败 fail-closed 不发）
  → 组装 i18n 模板消息 → 落会话消息（metadata 带 event_id/模板=发送依据）
    + WS 实时推送（复用 Stage 15 主动消息通道）
```

## 2. v1 事件白名单（配置在 `event_service.EVENT_RULES`）

| event_type | verify_tool（发送前重查） | 模板 |
|---|---|---|
| SHIPMENT_DELAYED | query_logistics_track | 配送延迟致歉 + 最新轨迹/ETA（用重查结果，不用事件旧值） |
| REFUND_STATUS_CHANGED | query_order | 退款状态更新 |
| BACK_IN_STOCK | query_product | 到货提醒 |
| COUPON_EXPIRING | —（事件即事实） | 优惠券到期提醒 |

白名单外事件 → `unknown_type` 拒收（记录不处理，防事件源打错）。

## 3. 红线（评审全部采纳）

1. 事件必须幂等（event_id 去重，Redis 故障宁可不发=fail-closed）；
2. 发送前重查最新状态，避免旧事件发出错误通知；
3. 同一事件只通知一次；用户退订后不得继续；
4. 主动通知与营销分开管理：事件通知不占营销会话频控，但**退订对两者
   同时生效**（用户的「别打扰」优先于任何分类）；
5. 发送依据可追溯：消息 metadata 落 event_id/event_type/模板 key。

## 4. 配置与观测

`EVENTS_ENABLED=false`（默认关=零回归）；`EVENTS_QUIET_HOURS`（如 `22-8`，
默认空=不启用）；指标 `proactive_events_total{event_type, outcome}`
（outcome=delivered/duplicate/opted_out/quiet_hours/no_channel/
verify_failed/unknown_type/disabled）。

## 5. 遗留

1. Outbox 表 + 消费位点（当前 webhook 直推；真实业务系统对接时补投递保证）；
2. 多渠道下发（Email/短信/WhatsApp——渠道接入层 backlog 既有项）；
3. 事件驱动 Playbook（延迟→自助选项→必要时开 Case 的多步流程，
   等真实事件形态；当前单条通知 + 用户回复自然进入响应式链路）；
4. 每类事件的静默级差（服务紧急事件可豁免静默时间）。
