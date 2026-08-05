# 推荐开发顺序

## Phase A：通用 Playbook 内核

先实现：

- Playbook 定义与注册；
- 进入条件；
- 步骤状态；
- 槽位收集；
- 工具调用请求；
- 成功、失败、放弃、超时；
- active_task 与 active_playbook 的关联；
- 决策日志。

验收标准：在不修改现有业务意图判断的情况下，能够运行一个纯 mock 的注册流程。

## Phase B：新手注册引导

实现确定性最高的 `NEW_USER_ONBOARDING`：

```text
检查注册状态
→ 选择手机号/邮箱
→ 发送验证码
→ 校验验证码
→ 创建账户
→ 可选基础资料
→ 完成
```

写操作继续走现有确认门和 ActionExecutor；Playbook 不直接写 DB 或外部系统。

## Phase C：选品顾问与商品推荐

先规则化：

```text
需求槽位收集
→ 硬约束过滤
→ 候选召回
→ 规则排序
→ 结构化推荐理由
→ LLM 只负责语言表达
```

禁止第一版直接训练推荐模型，因为没有真实点击、购买和负反馈数据。

## Phase D：Next Best Action

NBA 第一版只输出候选动作，不直接影响主流程：

```text
NO_PROACTIVE_ACTION
START_ONBOARDING
START_PRODUCT_DISCOVERY
OFFER_PRODUCT_COMPARE
MENTION_CAMPAIGN
OFFER_PURCHASE_HELP
```

先影子记录，再逐个动作灰度接管。

## Phase E：活动与促成交

必须先实现：

- 活动资格；
- 生效时间；
- 商品范围；
- 展示频控；
- 冷却时间；
- 拒绝后退出；
- 投诉/退款/负面情绪抑制。

促成交只允许提供真实信息、降低决策成本和帮助完成购买，不允许虚假稀缺或压力销售。

## Phase F：真实数据学习

数据达到要求后才考虑：

- 商品学习排序；
- NBA 策略模型；
- 转化概率；
- uplift/增量效果；
- 按客户和会话分组的离线评估；
- 在线 A/B 与长期反向指标。
