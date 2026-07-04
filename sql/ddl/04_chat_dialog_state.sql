-- =============================================================
-- 表：chat_dialog_state
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE chat_dialog_state (
	session_id VARCHAR(36) NOT NULL, 
	state VARCHAR(16) DEFAULT 'IDLE' NOT NULL, 
	active_task_json JSONB, 
	task_stack_json JSONB, 
	context_stacks_json JSONB, 
	version INTEGER DEFAULT '1' NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_chat_dialog_state_tenant_session UNIQUE (tenant_id, session_id)
);

COMMENT ON COLUMN chat_dialog_state.session_id IS '会话 ID';
COMMENT ON COLUMN chat_dialog_state.state IS '状态机状态';
COMMENT ON COLUMN chat_dialog_state.active_task_json IS '当前任务';
COMMENT ON COLUMN chat_dialog_state.task_stack_json IS '挂起任务栈';
COMMENT ON COLUMN chat_dialog_state.context_stacks_json IS '上下文对象栈';
COMMENT ON COLUMN chat_dialog_state.version IS '乐观锁版本号';
COMMENT ON COLUMN chat_dialog_state.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_dialog_state.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_dialog_state.updated_at IS '更新时间';
COMMENT ON COLUMN chat_dialog_state.created_at IS '创建时间';
