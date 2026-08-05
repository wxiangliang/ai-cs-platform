# Task、Playbook 与 Journey 状态模型

## 1. Active Task

表示用户当前明确要完成的事项。

```text
例：查询物流、申请退款、选择商品、注册帮助
```

## 2. Active Playbook

表示完成当前目标所采用的多轮流程。

一个 Task 可以不需要 Playbook，例如简单 FAQ；也可以绑定一个 Playbook，例如选品顾问。

## 3. Journey Stage

建议初始枚举：

```text
VISITOR
NEW_USER
REGISTERING
REGISTERED
DISCOVERING
CONSIDERING
READY_TO_BUY
PURCHASED
REPEAT_CUSTOMER
AFTER_SALES
AT_RISK
```

Journey 更新必须有证据和置信度，不因单句话频繁跳转。

## 4. 关系约束

- Task 切换时，Playbook 可挂起，但 Journey 不回退。
- `SOCIAL_HOLD` 不改变 Task、Playbook 或 Journey。
- 用户拒绝活动只影响 Campaign preference，不应终止业务 Task。
- 购买完成可推动 Journey，但必须来自订单事实或确认事件。
