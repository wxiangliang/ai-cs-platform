-- =============================================================
-- 表：chat_handoff_ticket
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE chat_handoff_ticket (
	session_id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	reason VARCHAR(32) NOT NULL, 
	source_intent VARCHAR(64), 
	status VARCHAR(16) DEFAULT 'PENDING' NOT NULL, 
	assignee VARCHAR(64), 
	context_json JSONB, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_handoff_tenant_session ON chat_handoff_ticket (tenant_id, session_id);

CREATE INDEX ix_chat_handoff_tenant_status ON chat_handoff_ticket (tenant_id, status);

CREATE UNIQUE INDEX uq_chat_handoff_open_session ON chat_handoff_ticket (tenant_id, session_id) WHERE status IN ('PENDING', 'ASSIGNED');

COMMENT ON COLUMN chat_handoff_ticket.session_id IS '会话 ID';
COMMENT ON COLUMN chat_handoff_ticket.user_id IS '用户 ID';
COMMENT ON COLUMN chat_handoff_ticket.reason IS '触发原因';
COMMENT ON COLUMN chat_handoff_ticket.source_intent IS '触发时的意图码';
COMMENT ON COLUMN chat_handoff_ticket.status IS '工单状态';
COMMENT ON COLUMN chat_handoff_ticket.assignee IS '坐席标识';
COMMENT ON COLUMN chat_handoff_ticket.context_json IS '上下文移交包';
COMMENT ON COLUMN chat_handoff_ticket.resolved_at IS '解决时间';
COMMENT ON COLUMN chat_handoff_ticket.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_handoff_ticket.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_handoff_ticket.updated_at IS '更新时间';
COMMENT ON COLUMN chat_handoff_ticket.created_at IS '创建时间';
