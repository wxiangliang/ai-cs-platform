-- =============================================================
-- 表：chat_session
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE chat_session (
	user_id VARCHAR(64) NOT NULL, 
	channel VARCHAR(32) NOT NULL, 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	metadata_json JSONB, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_session_created_at ON chat_session (created_at);

CREATE INDEX ix_chat_session_tenant_status ON chat_session (tenant_id, status);

CREATE INDEX ix_chat_session_tenant_user ON chat_session (tenant_id, user_id);

COMMENT ON COLUMN chat_session.user_id IS '用户 ID';
COMMENT ON COLUMN chat_session.channel IS '渠道';
COMMENT ON COLUMN chat_session.status IS '会话状态：active/closed/handoff';
COMMENT ON COLUMN chat_session.metadata_json IS '扩展字段';
COMMENT ON COLUMN chat_session.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_session.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_session.updated_at IS '更新时间';
COMMENT ON COLUMN chat_session.created_at IS '创建时间';
