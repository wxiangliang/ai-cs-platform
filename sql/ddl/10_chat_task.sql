-- =============================================================
-- 表：chat_task
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE chat_task (
	session_id VARCHAR(36) NOT NULL, 
	intent VARCHAR(64) NOT NULL, 
	skill_id VARCHAR(64) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	collected_slots_json JSONB, 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	executed_at TIMESTAMP WITH TIME ZONE, 
	result_json JSONB, 
	version INTEGER DEFAULT '1' NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_task_tenant_session ON chat_task (tenant_id, session_id);

CREATE INDEX ix_chat_task_tenant_status ON chat_task (tenant_id, status);

COMMENT ON COLUMN chat_task.session_id IS '会话 ID';
COMMENT ON COLUMN chat_task.intent IS '任务意图';
COMMENT ON COLUMN chat_task.skill_id IS 'Skill 标识';
COMMENT ON COLUMN chat_task.status IS '任务状态';
COMMENT ON COLUMN chat_task.collected_slots_json IS '已收集槽位';
COMMENT ON COLUMN chat_task.confirmed_at IS '用户确认时间';
COMMENT ON COLUMN chat_task.executed_at IS '执行时间';
COMMENT ON COLUMN chat_task.result_json IS '执行结果（工单号等）';
COMMENT ON COLUMN chat_task.version IS '乐观锁版本号';
COMMENT ON COLUMN chat_task.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_task.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_task.updated_at IS '更新时间';
COMMENT ON COLUMN chat_task.created_at IS '创建时间';
