# FastAPI + LangGraph 聊天框架需求

本文件是聊天系统整体框架设计参考。  
具体执行以各阶段文档为准。

---

## 1. 目标

实现一个可扩展的 AI 客服聊天主链路。

技术栈：

```text
FastAPI
LangChain
LangGraph
PostgreSQL
Redis
SQLAlchemy async
Alembic
Pydantic v2
pydantic-settings
```

---

## 2. 第一阶段先实现

```text
1. Web API 基础结构
2. PostgreSQL 异步连接池
3. Redis 异步连接池
4. 统一配置
5. 统一响应
6. 统一异常
7. 基础日志
8. Health API
9. Alembic 基础配置
10. 预留 Chat API 和 LangGraph 目录
```

---

## 3. 第二阶段实现

```text
1. chat_session
2. chat_message
3. chat_dialog_state
4. chat_decision_log
5. SQLAlchemy Models
6. Alembic migration
7. Repository 数据访问层
```

---

## 4. 第三阶段实现

```text
1. Chat API
2. ChatService
3. LangGraph 基础节点
4. RuleIntentClassifier
5. SlotExtractor
6. DialogStateManager
7. SkillResolver
8. 模板回复
9. decision_log 落库
```

---

## 5. 推荐目录

```text
app/
  main.py

  api/
    routes/
      health.py
      chat.py

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

  models/
    chat_session.py
    chat_message.py
    chat_dialog_state.py
    chat_decision_log.py

  repositories/
    chat_session_repository.py
    chat_message_repository.py
    chat_dialog_state_repository.py
    chat_decision_log_repository.py

  schemas/
    chat.py

  services/
    chat_service.py

  chat/
    graph/
      builder.py
      state.py
      nodes/
        load_session_state.py
        preprocess_message.py
        guardrail_check.py
        intent_classify.py
        slot_extract.py
        dialog_state_resolve.py
        skill_resolve.py
        response_generate.py
        save_turn.py

    intent/
      rule_classifier.py
      llm_classifier.py
      types.py

    slots/
      extractor.py
      patterns.py

    state/
      manager.py
      types.py

    skills/
      registry.py
      loader.py
      types.py

    llm/
      factory.py
      prompts.py

    logging/
      decision_logger.py
```

---

## 6. 高可用与性能要求

```text
1. PostgreSQL 使用连接池，不要每次请求创建 engine。
2. Redis 使用全局 async client，应用关闭时优雅释放。
3. 所有外部访问必须有 timeout。
4. 所有异常必须记录日志并返回统一响应。
5. 不在 route 中写复杂业务。
6. 会话状态变更需要考虑并发，后续使用 version 乐观锁。
7. 决策日志异步化可后续优化，但接口先保留。
```

---

## 7. 第一阶段不做

```text
RAG
FAQ
向量数据库
真实商品查询
真实订单查询
真实售后写操作
模型训练
复杂人工客服工作台
```
