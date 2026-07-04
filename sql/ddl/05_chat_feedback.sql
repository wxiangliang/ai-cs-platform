-- =============================================================
-- 表：chat_feedback
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE chat_feedback (
	session_id VARCHAR(36) NOT NULL, 
	message_id VARCHAR(36) NOT NULL, 
	rating VARCHAR(8) NOT NULL, 
	comment TEXT, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_feedback_tenant_rating ON chat_feedback (tenant_id, rating, created_at);

CREATE UNIQUE INDEX uq_chat_feedback_message ON chat_feedback (tenant_id, message_id);

COMMENT ON COLUMN chat_feedback.session_id IS '会话 ID';
COMMENT ON COLUMN chat_feedback.message_id IS '消息 ID';
COMMENT ON COLUMN chat_feedback.rating IS '评价 up/down';
COMMENT ON COLUMN chat_feedback.comment IS '补充说明';
COMMENT ON COLUMN chat_feedback.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_feedback.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_feedback.created_at IS '创建时间';
