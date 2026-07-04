-- =============================================================
-- 表：chat_csat
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE chat_csat (
	session_id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	score INTEGER NOT NULL, 
	comment TEXT, 
	trigger VARCHAR(32) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_csat_tenant_created ON chat_csat (tenant_id, created_at);

CREATE INDEX ix_chat_csat_tenant_session ON chat_csat (tenant_id, session_id);

COMMENT ON COLUMN chat_csat.session_id IS '会话 ID';
COMMENT ON COLUMN chat_csat.user_id IS '用户 ID';
COMMENT ON COLUMN chat_csat.score IS '评分 1-5';
COMMENT ON COLUMN chat_csat.comment IS '附言';
COMMENT ON COLUMN chat_csat.trigger IS '触发场景';
COMMENT ON COLUMN chat_csat.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_csat.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_csat.created_at IS '创建时间';
