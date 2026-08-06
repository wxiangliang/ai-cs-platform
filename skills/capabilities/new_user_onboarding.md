---
capability_id: new_user_onboarding
status: implemented   # 验证码环节遗留（任务中途工具机制）
implemented_by:
  intents: [MEMBER.REGISTER]
  modules: [app/chat/tools/mock_provider.py]
---

# NEW_USER_ONBOARDING Skill

## 目标

帮助未注册或注册未完成的用户完成账号注册，并在成功后提供一次可选的基础使用引导。

## 进入条件

满足任一条件：

- 用户意图为 `ACCOUNT.REGISTRATION_HELP`；
- 用户主动选择“开始注册”；
- 当前 Journey 为 `NEW_USER`，且用户询问只有注册后才能使用的功能；
- NBA 在无冲突情况下推荐启动，且用户接受。

## 禁止主动启动

- 投诉、退款、人工接管中；
- 用户明确拒绝注册；
- 已注册；
- 当前处于其他写操作确认门；
- 注册服务不可用。

## 槽位

必填：

```text
registration_channel: PHONE | EMAIL
phone_or_email
verification_code
consent_accepted
```

可选：

```text
nickname
region
preferred_language
```

## 步骤

```text
CHECK_STATUS
→ CHOOSE_CHANNEL
→ COLLECT_IDENTIFIER
→ SEND_CODE
→ COLLECT_CODE
→ VERIFY_CODE
→ CONFIRM_CREATE_ACCOUNT
→ CREATE_ACCOUNT
→ OPTIONAL_PROFILE
→ COMPLETE
```

## 工具

只读：

- `get_registration_status`
- `check_identifier_available`

写操作：

- `send_verification_code`
- `verify_verification_code`
- `create_account`
- `update_basic_profile`

`create_account` 必须经确认门，`idempotency_key = playbook_instance_id`。

## 退出

成功：

- 账户创建成功；
- Journey → `REGISTERED`。

中止：

- 用户明确放弃；
- 验证码错误达到上限；
- 超时；
- 转人工；
- 服务不可用。

## 回复策略

一次只问一个关键问题。验证码失败时不要泄露账户是否存在等敏感信息。
注册完成后最多提供一个下一步建议，不直接插入活动营销。

## 核心指标

- registration_start_rate
- verification_success_rate
- registration_completion_rate
- registration_abort_rate
- duplicate_account_attempt_total
