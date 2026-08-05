# Stage 33：Next Best Action

## 目标

在主任务处理后，规则化选择最多一个主动动作。

## 第一阶段

影子模式：

- 生成候选；
- 应用全局抑制；
- 排序；
- 记录 selected_action；
- 不进入用户回复。

## 第二阶段

只灰度低风险动作：

- RESUME_PLAYBOOK_HINT
- START_PRODUCT_DISCOVERY
- OFFER_PRODUCT_COMPARE

最后才考虑 `MENTION_CAMPAIGN`。

## 验收门禁

- 投诉/退款/确认门营销触发数为 0；
- 每次输出 reason_codes；
- 用户拒绝后冷却生效；
- 与当前主任务无关动作不展示；
- 一键关闭后恢复仅响应式客服。
