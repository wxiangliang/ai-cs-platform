-- =============================================================
-- 表：chat_tool_call
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE chat_tool_call (
	session_id VARCHAR(36) NOT NULL, 
	task_id VARCHAR(36), 
	tool_id VARCHAR(64) NOT NULL, 
	request_json JSONB, 
	response_json JSONB, 
	ok BOOLEAN NOT NULL, 
	error_code VARCHAR(32), 
	latency_ms FLOAT, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_tool_call_tenant_session ON chat_tool_call (tenant_id, session_id);

CREATE INDEX ix_chat_tool_call_tenant_tool ON chat_tool_call (tenant_id, tool_id);

COMMENT ON COLUMN chat_tool_call.session_id IS '会话 ID';
COMMENT ON COLUMN chat_tool_call.task_id IS '任务 ID';
COMMENT ON COLUMN chat_tool_call.tool_id IS '工具标识';
COMMENT ON COLUMN chat_tool_call.request_json IS '入参（脱敏）';
COMMENT ON COLUMN chat_tool_call.response_json IS '返回数据';
COMMENT ON COLUMN chat_tool_call.ok IS '是否成功';
COMMENT ON COLUMN chat_tool_call.error_code IS '错误码';
COMMENT ON COLUMN chat_tool_call.latency_ms IS '耗时（毫秒）';
COMMENT ON COLUMN chat_tool_call.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_tool_call.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_tool_call.created_at IS '创建时间';
