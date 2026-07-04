# Stage 08 需求：鉴权与多租户加固（生产门槛）

> 前置阅读：`docs/architecture/roadmap.md` 3.5、`docs/api/chat_api.md`（现有"开发模式"临时约定）。
> **状态：✅ 已实现（2026-07-02，实现记录见文末附录）。**
> 本阶段是**上生产的硬门槛**：当前 tenant_id/user_id 由请求体明文传入、
> 管理面靠单一 KB_ADMIN_TOKEN、无限流无幂等——这些临时约定全部在本阶段收口。

---

## 1. 阶段目标

租户身份从「请求体自报」变为「凭证解析」；管理面与聊天面权限分离；
加上限流与幂等两道生产防线。全程保留开发模式开关（`AUTH_ENABLED=false` 时行为不变），
保证联调不被鉴权阻塞。

**信任模型（v1 明确边界）**：本服务的调用方是**租户的业务后端**（服务端对服务端），
不是终端用户。因此 v1 用 **API Key per tenant**；user_id 由租户后端传入（信任已鉴权的租户），
终端用户级鉴权（JWT/OAuth）不在本阶段。

## 2. 本阶段要做什么

1. **api_credential 表**（migration，同步 chat_tables.md）

   | 字段 | 说明 |
   |---|---|
   | id / tenant_id | 标识 |
   | key_id | 公开键标识（`ak_` 前缀，可日志） |
   | secret_hash | 密钥哈希（**bcrypt/argon2，绝不明文落库**；完整密钥 `ak_xxx.sk_yyy` 仅创建时返回一次） |
   | scopes | JSONB：`["chat"]` / `["chat","admin"]`（admin 覆盖 kb/product/handoff 管理面） |
   | status | active / disabled |
   | last_used_at | 最近使用（审计；异步更新，容忍不精确） |
   | created_at / updated_at | 时间 |

2. **鉴权中间件**（FastAPI dependency）
   - `Authorization: Bearer ak_xxx.sk_yyy` → 校验哈希 → 注入 `request.state.tenant_id` 与 scopes；
   - 校验失败统一 401 UNAUTHORIZED（不区分 key 不存在/密钥错误，防探测）；scope 不足 403；
   - key 校验结果进程内短 TTL 缓存（bcrypt 验证有成本，避免每请求全量哈希比对）；
   - health 探针豁免鉴权。

3. **收口请求体 tenant_id**（破坏性变更，`AUTH_ENABLED=true` 时生效）
   - chat/kb/product 全部接口的 tenant_id 一律取自凭证，请求体/查询参数中的 tenant_id
     忽略并在不一致时告警日志（帮助调用方发现配置错误）；
   - `AUTH_ENABLED=false`（默认，开发模式）：行为与现在完全一致，文档标注生产必须开启。

4. **session_id 服务端发号强制化**（`AUTH_ENABLED=true` 时生效）
   - 发消息接口不再隐式创建会话（load_session_state 的自动建会话仅开发模式保留）；
   - 未创建的 session_id → 404 SESSION_NOT_FOUND；防调用方伪造/预占任意 id。

5. **管理面正式鉴权**：kb / product / handoff 管理接口改用 `admin` scope 的 API Key，
   废除 KB_ADMIN_TOKEN（保留一个版本的兼容期，启动时告警提示迁移）。

6. **限流**（Redis 滑动窗口，返回 429 RATE_LIMITED + Retry-After）
   - 租户级：`RATE_LIMIT_TENANT_PER_MINUTE`（默认 600）；
   - 会话级（防单会话刷屏打爆 LLM/检索）：`RATE_LIMIT_SESSION_PER_MINUTE`（默认 30）；
   - Redis 不可用时**放行并告警**（限流是保护措施，不能成为可用性单点）。

7. **发消息幂等**：支持 `Idempotency-Key` 请求头——首次处理结果缓存
   Redis（TTL 10 分钟），重复请求原样返回缓存响应，不重复走决策链/落库。

8. **密钥管理 CLI**：`scripts/manage_api_keys.py create|disable|list --tenant xx --scopes chat,admin`
   （管理面 API 自身管理密钥有鸡生蛋问题，v1 用运维 CLI）。

## 3. 本阶段不做什么

- 终端用户级 JWT/OAuth、完整 RBAC/权限点位、密钥自动轮转（CLI 手动重发）；
- WAF/IP 白名单（部署层职责）；请求签名（HMAC，对接方有需求再加）。

## 4. 技术要求

- 密钥哈希用 argon2/bcrypt（新增依赖走 uv）；任何日志/异常/decision_log 不得出现 secret；
- 限流与幂等的 Redis key 必须带 tenant 前缀；幂等缓存的响应体注意大小上限（超限不缓存）；
- 中间件失败路径也要有 trace_id（当前 trace 在 Service 才生成，本阶段顺带提为中间件——
  代码评审遗留 P2-12 一并清偿）；
- 请求体大小限制（默认 1MB）与 CORS 配置走 settings。

## 5. 目录和文件要求

```text
app/core/auth.py                 # 凭证解析 dependency + scope 校验
app/core/rate_limit.py           # Redis 滑动窗口
app/core/idempotency.py          # 幂等缓存
app/models/api_credential.py
app/repositories/api_credential_repository.py
scripts/manage_api_keys.py
alembic/versions/xxxx_add_api_credential.py
tests/stage08/
```

## 6. 具体实现要求

- 所有新错误码入 chat_api.md 错误契约表：401 UNAUTHORIZED / 403 FORBIDDEN /
  429 RATE_LIMITED；429 响应带 Retry-After 头。
- AUTH_ENABLED 开关的两套行为都要有测试（开发模式回归 + 生产模式全断言）。
- e2e 场景必须包含「租户 A 的 key 访问租户 B 的会话 → 404」（凭证级隔离验证，
  替代现在靠请求体自觉的隔离）。

## 7. 代码质量要求

- 单测：哈希与验证、scope 判定、限流窗口边界、幂等命中/过期、开关两态；
- ruff / mypy 通过；核心逻辑中文注释。

## 8. 验证方式

1. 无 key / 错 key → 401；chat scope 的 key 调 kb 管理接口 → 403。
2. 正确 key 全链路对话正常，且请求体伪造他租户 tenant_id 被忽略（凭证为准）。
3. 租户 A key 访问租户 B session → 404；未创建的 session 发消息 → 404。
4. 超限请求 → 429 + Retry-After；停 Redis → 放行 + 告警日志。
5. 同 Idempotency-Key 重发 → 响应一致且 chat_message 不重复落库。
6. `AUTH_ENABLED=false` → 现有全部 e2e 回归不变。

## 9. 执行提示词

```text
请先阅读 AGENTS.md、docs/api/chat_api.md、本文档。
本次只实现 Stage 08，按第 2 节逐项实现，第 3 节不要做；
注意 AUTH_ENABLED 开关必须保证开发模式行为零回归。
完成后说明新增/修改文件、迁移脚本、密钥创建方式、验证步骤。
```

---

## 附录：实现记录（2026-07-02）

### 已实现清单

1. **api_credential 表**（migration `540fed33963e`）：key_id 唯一 + secret bcrypt 哈希
   （完整密钥 `ak_xxx.sk_yyy` 仅 CLI 创建时打印一次）+ scopes(JSONB) + last_used_at。
2. **鉴权依赖**（`app/core/auth.py`）：`get_auth_context`（Bearer 解析 → 库查 →
   bcrypt 线程池校验 → 进程内 TTL 缓存，缓存键为完整密钥 sha256 不存明文）；
   `require_chat` / `require_admin`（scope 分离）；401 统一话术防探测、scope 不足 403；
   `resolve_tenant_id`（鉴权=凭证为准且不一致告警；开发模式=请求参数，缺失 400）。
3. **收口**：chat/kb/product 全部路由接入；schema 的 tenant_id 改为可选并标注
   「鉴权开启后忽略」；管理面 KB_ADMIN_TOKEN 保留兼容期（启动告警提示迁移）。
4. **session 强制服务端发号**：AUTH_ENABLED=true 时 load_session_state 不再隐式建会话，
   未创建 session 一律 404。
5. **限流**（`app/core/rate_limit.py`）：Redis ZSET 滑动窗口，租户级+会话级两道，
   429 + Retry-After 头；**Redis 故障放行并告警**（不做可用性单点）。
6. **幂等**（`app/core/idempotency.py`）：Idempotency-Key → Redis 响应缓存
   （TTL 600s、64KB 上限超限不缓存）；重复请求原样返回，不重复走链路/落库。
7. **trace 中间件化**（清偿代码评审遗留 P2-12）：入口生成/透传 X-Trace-Id，
   401/422 等早期失败也有 trace；响应回写头；ChatService 改为复用。
8. **请求体限长（413）与 CORS** 走 settings；**密钥 CLI**
   `scripts/manage_api_keys.py create|list|disable`。

### 验证记录（全部通过）

- 单测 13 个（累计 66）：凭证生成/验证/缓存命中只查一次库、401 各形态、
  错误密钥、scope 判定、tenant 解析两态、限流窗口与会话级、Redis 故障放行、
  幂等读写与大小上限；
- 鉴权模式 e2e 10 场景：无 key/错 key 401（统一话术）、凭证建会话（不传 tenant）、
  **请求体伪造他租户被忽略**、chat key 调管理面 403、admin key 放行、
  **租户 B key 访问租户 A 会话 404**、伪造 session id 404、
  幂等重发响应一致且 DB 零重复（消息 1 条/工单调用 1 次）、429 + Retry-After: 60；
- 开发模式零回归：无 key + 请求体 tenant 全链路一致（退款闭环/商品价格）、
  缺 tenant 400、X-Trace-Id 响应头、管理面兼容期放行。

### 遗留

```text
1. 密钥轮转靠 CLI 手动重发（原需求范围内）；disable 后进程缓存最长 AUTH_CACHE_TTL 秒失效，
   多实例部署时可改用 Redis 共享缓存（接口已收口在 auth.py，改动局部）。
2. HMAC 请求签名、终端用户级 JWT 按对接方需求再加（见第 3 节不做清单）。
3. KB_ADMIN_TOKEN 兼容期计划在 Stage 09 完成后移除。
```
