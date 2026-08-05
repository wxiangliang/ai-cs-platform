-- =============================================================
-- 表：user_memory
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE user_memory (
	user_id VARCHAR(64) NOT NULL, 
	kind VARCHAR(16) DEFAULT 'fact' NOT NULL, 
	content TEXT NOT NULL, 
	source_session_id VARCHAR(36), 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_user_memory_tenant_user ON user_memory (tenant_id, user_id, status);

COMMENT ON COLUMN user_memory.user_id IS '用户 ID';
COMMENT ON COLUMN user_memory.kind IS '类别';
COMMENT ON COLUMN user_memory.content IS '记忆内容';
COMMENT ON COLUMN user_memory.source_session_id IS '来源会话';
COMMENT ON COLUMN user_memory.status IS '状态';
COMMENT ON COLUMN user_memory.id IS '主键，UUID 字符串';
COMMENT ON COLUMN user_memory.tenant_id IS '租户 ID';
COMMENT ON COLUMN user_memory.updated_at IS '更新时间';
COMMENT ON COLUMN user_memory.created_at IS '创建时间';
