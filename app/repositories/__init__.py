"""数据访问层（Repository）包。

Repository 只负责数据访问（增删改查），不写业务流程。
事务的 commit / rollback 由上层（如 get_db_session 依赖或 Service）统一控制，
Repository 内部只做 flush，保证拿到数据库生成的默认值（如主键、created_at）。
"""
