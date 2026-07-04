# Stage 01：基础框架

本阶段用于搭建 `ai-cs-platform` 的基础工程框架。  
只实现基础设施，不实现完整 AI 聊天业务。

---

## 1. 阶段目标

实现一个高可用、高性能、可扩展、可维护的 FastAPI 后端基础框架，支持：

```text
1. FastAPI Web API 基础结构
2. PostgreSQL 异步连接池
3. SQLAlchemy 2.x async ORM 基础封装
4. Alembic migration 初始化配置
5. Redis 异步连接池
6. 全局配置管理
7. 统一响应结构
8. 统一异常处理
9. 基础结构化日志
10. 健康检查接口
11. 后续 LangChain / LangGraph 可以接入的目录结构和基础接口
```

---

## 2. 本阶段不做

```text
1. 不实现完整 AI 聊天流程。
2. 不创建聊天业务表。
3. 不实现 RAG。
4. 不实现 FAQ。
5. 不实现向量数据库。
6. 不接真实商品、订单、售后工具。
7. 不实现真实 LLM 业务调用。
```

---

## 3. 技术要求

```text
Python：使用当前项目 uv pin 的版本
包管理：uv
Web：FastAPI
ASGI：Uvicorn
DB：PostgreSQL
ORM：SQLAlchemy 2.x async
DB Driver：asyncpg
Migration：Alembic
Cache：redis.asyncio
Config：pydantic-settings
Schema：Pydantic v2
```

---

## 4. 目录要求

请按以下结构补充代码：

```text
app/
  main.py

  api/
    routes/
      health.py

  core/
    config.py
    logging.py
    exceptions.py
    responses.py

  db/
    base.py
    session.py

  cache/
    redis_client.py
```

如果目录不存在，请创建。

---

## 5. 具体实现要求

### 5.1 配置系统

实现 `app/core/config.py`。

要求：

```text
1. 使用 pydantic-settings。
2. 支持从 .env 读取配置。
3. 提供全局 settings。
4. 不允许在代码中写死数据库、Redis、API Key。
```

至少包含：

```text
APP_NAME
APP_ENV
DEBUG
DATABASE_URL
REDIS_URL
LOG_LEVEL
OPENAI_API_KEY
OPENAI_BASE_URL
CHAT_MODEL
```

---

### 5.2 PostgreSQL 数据库连接

实现 `app/db/session.py`。

要求：

```text
1. 使用 SQLAlchemy async engine。
2. 使用 asyncpg。
3. 配置连接池。
4. 提供 async_engine。
5. 提供 AsyncSessionLocal。
6. 提供 get_db_session()，用于 FastAPI Depends。
7. session 必须正确 commit / rollback / close。
8. 中文注释说明关键逻辑。
```

---

### 5.3 SQLAlchemy Base

实现 `app/db/base.py`。

要求：

```text
1. 定义 Declarative Base。
2. 预留通用 id、created_at、updated_at mixin。
3. 暂时不用创建业务表。
4. 中文注释说明 mixin 用途。
```

---

### 5.4 Redis 连接

实现 `app/cache/redis_client.py`。

要求：

```text
1. 使用 redis.asyncio。
2. 创建全局 Redis client。
3. 支持 startup 初始化。
4. 支持 shutdown 关闭。
5. 提供 get_redis_client()。
6. 中文注释说明连接复用和关闭逻辑。
```

---

### 5.5 统一异常

实现 `app/core/exceptions.py`。

要求：

```text
1. 定义基础业务异常 AppException。
2. 支持 error_code、message、status_code。
3. 定义全局异常处理函数。
4. FastAPI 启动时注册异常处理。
```

---

### 5.6 统一响应

实现 `app/core/responses.py`。

统一响应结构：

```json
{
  "success": true,
  "code": "OK",
  "message": "success",
  "data": {},
  "trace_id": null
}
```

要求提供：

```text
success_response()
error_response()
```

---

### 5.7 日志

实现 `app/core/logging.py`。

要求：

```text
1. 使用 Python logging。
2. 根据 LOG_LEVEL 初始化。
3. 日志格式包含时间、level、logger name、message。
4. 预留 trace_id 扩展入口。
5. 中文注释说明。
```

---

### 5.8 FastAPI app

修改 `app/main.py`。

要求：

```text
1. 创建 FastAPI app。
2. 注册 health router。
3. 注册异常处理。
4. startup 时初始化 Redis。
5. shutdown 时关闭 Redis。
6. 保留后续注册 chat router 的位置。
```

---

### 5.9 健康检查接口

实现 `app/api/routes/health.py`。

必须提供：

```http
GET /api/health
```

返回：

```json
{
  "success": true,
  "code": "OK",
  "message": "success",
  "data": {
    "status": "ok"
  },
  "trace_id": null
}
```

建议提供：

```http
GET /api/health/ready
```

检查 PostgreSQL 和 Redis 是否可用。

---

### 5.10 Alembic

如果项目还没有 Alembic，请初始化或补齐配置。

要求：

```text
1. Alembic 能读取项目配置中的 DATABASE_URL。
2. 支持 async SQLAlchemy migration。
3. 暂时不需要生成业务表 migration。
4. 确保后续可以执行 revision 和 upgrade。
```

---

## 6. 代码质量要求

```text
1. 所有关键类、函数、复杂逻辑必须有中文注释。
2. 不要写死数据库地址、Redis 地址、API Key。
3. 不要在代码中打印敏感信息。
4. 所有资源连接必须支持优雅关闭。
5. 保持模块边界清晰。
6. 不要把业务逻辑写进 API route。
7. 不要一次性实现 RAG、FAQ、向量库、订单工具、售后工具。
8. 生成代码后，请说明修改了哪些文件，以及如何启动验证。
```

---

## 7. 验证命令

启动：

```bash
uv run uvicorn app.main:app --reload
```

访问：

```text
http://localhost:8000/api/health
```

预期返回：

```json
{
  "success": true,
  "code": "OK",
  "message": "success",
  "data": {
    "status": "ok"
  },
  "trace_id": null
}
```

---

## 8. Codex 执行提示词

```text
请先阅读根目录 AGENTS.md，
再阅读 docs/requirements/stage-01-foundation/01_foundation_framework.md。

本次只实现 Stage 01 基础框架。
严格按文档实现，不要超范围实现。
要求核心类、核心方法、复杂逻辑有中文注释。
完成后说明新增文件、修改文件、启动方式和验证方式。
```
