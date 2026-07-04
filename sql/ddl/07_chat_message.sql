-- =============================================================
-- 表：chat_message
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE chat_message (
	created_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, 
	session_id VARCHAR(36) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	content TEXT NOT NULL, 
	intent VARCHAR(64), 
	status VARCHAR(32), 
	slots_json JSONB, 
	trace_id VARCHAR(64), 
	metadata_json JSONB, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_message_tenant_intent ON chat_message (tenant_id, intent);

CREATE INDEX ix_chat_message_tenant_session_created ON chat_message (tenant_id, session_id, created_at);

CREATE INDEX ix_chat_message_trace_id ON chat_message (trace_id);

COMMENT ON COLUMN chat_message.created_at IS '创建时间';
COMMENT ON COLUMN chat_message.session_id IS '会话 ID';
COMMENT ON COLUMN chat_message.role IS '角色';
COMMENT ON COLUMN chat_message.content IS '消息内容';
COMMENT ON COLUMN chat_message.intent IS '最终意图';
COMMENT ON COLUMN chat_message.status IS '本轮处理状态';
COMMENT ON COLUMN chat_message.slots_json IS '槽位';
COMMENT ON COLUMN chat_message.trace_id IS '链路追踪 ID';
COMMENT ON COLUMN chat_message.metadata_json IS '扩展字段';
COMMENT ON COLUMN chat_message.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_message.tenant_id IS '租户 ID';
