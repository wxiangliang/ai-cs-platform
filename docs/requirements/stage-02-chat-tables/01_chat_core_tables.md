# Stage 02：聊天核心表

本阶段用于实现聊天系统的 PostgreSQL 核心表、SQLAlchemy Models、Repository 和 Alembic migration。

---

## 1. 阶段目标

创建以下四张核心表：

```text
1. chat_session
2. chat_message
3. chat_dialog_state
4. chat_decision_log
```

并实现：

```text
1. SQLAlchemy Models
2. Alembic migration
3. Repository 基础方法
```

---

## 2. 本阶段不做

```text
1. 不实现完整 LangGraph 聊天流程。
2. 不接 LLM。
3. 不接 RAG。
4. 不接 FAQ。
5. 不接向量数据库。
6. 不接真实订单、商品、售后工具。
7. 不实现复杂业务状态机，只建表和数据访问层。
```

---

## 3. 技术要求

```text
1. 使用 SQLAlchemy 2.x async ORM。
2. 使用 PostgreSQL。
3. 使用 Alembic 管理 migration。
4. 所有 JSON 字段使用 PostgreSQL JSONB。
5. 所有表必须包含 tenant_id。
6. 所有表必须包含 created_at。
7. 需要 updated_at 的表必须包含 updated_at。
8. 关键字段需要建立索引。
9. 中文注释说明每张表用途。
```

---

## 4. 目录要求

新增或修改：

```text
app/models/
  chat_session.py
  chat_message.py
  chat_dialog_state.py
  chat_decision_log.py

app/repositories/
  chat_session_repository.py
  chat_message_repository.py
  chat_dialog_state_repository.py
  chat_decision_log_repository.py
```

确保 `app/db/base.py` 能导入所有模型，支持 Alembic autogenerate。

---

## 5. 表设计

请严格参考：

```text
docs/database/chat_tables.md
```

核心要求：

```text
chat_session：保存会话
chat_message：保存消息
chat_dialog_state：保存状态机
chat_decision_log：保存决策过程
```

---

## 6. Repository 要求

每个 Repository 提供基础方法：

```text
create
get_by_id
update
```

按表情况提供：

```text
list_by_session_id
get_by_session_id
upsert_by_session_id
```

其中：

```text
chat_dialog_state_repository 必须提供 get_by_session_id 和 upsert_by_session_id。
```

Repository 只做数据访问，不写业务流程。

---

## 7. Alembic 要求

生成 migration：

```bash
uv run alembic revision --autogenerate -m "create chat core tables"
```

执行 migration：

```bash
uv run alembic upgrade head
```

---

## 8. 代码质量要求

```text
1. 所有表、字段、Repository 方法需要中文注释。
2. 不要在 Repository 中写业务流程。
3. Repository 只做数据访问。
4. 不要接 LangGraph。
5. 不要接 LLM。
6. 不要接 RAG / FAQ / 向量库。
7. 不要手工 SQL 绕过 Alembic。
```

---

## 9. 验证方式

```bash
uv run alembic upgrade head
```

然后检查 PostgreSQL：

```sql
\dt
```

应看到：

```text
chat_session
chat_message
chat_dialog_state
chat_decision_log
```

---

## 10. Codex 执行提示词

```text
请先阅读根目录 AGENTS.md，
再阅读 docs/requirements/stage-02-chat-tables/01_chat_core_tables.md，
并参考 docs/database/chat_tables.md。

本次只实现 Stage 02 聊天核心表。
严格按文档实现，不要超范围实现。
完成后说明新增文件、修改文件、migration 文件、执行命令和验证方式。
```
