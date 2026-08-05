-- =============================================================
-- 表：chat_service_case
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE chat_service_case (
	case_no VARCHAR(32) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	case_type VARCHAR(32) NOT NULL, 
	status VARCHAR(20) DEFAULT 'OPEN' NOT NULL, 
	priority VARCHAR(8) DEFAULT 'NORMAL' NOT NULL, 
	owner_type VARCHAR(8) DEFAULT 'AI' NOT NULL, 
	owner_id VARCHAR(64), 
	sla_due_at TIMESTAMP WITH TIME ZONE, 
	related_json JSONB, 
	resolution_code VARCHAR(64), 
	reopen_count INTEGER DEFAULT '0' NOT NULL, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	closed_at TIMESTAMP WITH TIME ZONE, 
	metadata_json JSONB, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_service_case_sla ON chat_service_case (sla_due_at);

CREATE INDEX ix_chat_service_case_tenant_status ON chat_service_case (tenant_id, status);

CREATE INDEX ix_chat_service_case_tenant_user ON chat_service_case (tenant_id, user_id);

CREATE UNIQUE INDEX uq_chat_service_case_active ON chat_service_case (tenant_id, user_id, case_type) WHERE status IN ('OPEN','IN_PROGRESS','WAITING_CUSTOMER','WAITING_INTERNAL','WAITING_EXTERNAL','REOPENED','ESCALATED');

COMMENT ON COLUMN chat_service_case.case_no IS '人读编号 CS...';
COMMENT ON COLUMN chat_service_case.user_id IS '客户标识';
COMMENT ON COLUMN chat_service_case.case_type IS 'Case 类型';
COMMENT ON COLUMN chat_service_case.status IS '状态';
COMMENT ON COLUMN chat_service_case.priority IS 'LOW/NORMAL/HIGH';
COMMENT ON COLUMN chat_service_case.owner_type IS 'AI/HUMAN';
COMMENT ON COLUMN chat_service_case.owner_id IS '认领者';
COMMENT ON COLUMN chat_service_case.sla_due_at IS 'SLA 解决时限';
COMMENT ON COLUMN chat_service_case.related_json IS '关联对象';
COMMENT ON COLUMN chat_service_case.resolution_code IS '解决口径码';
COMMENT ON COLUMN chat_service_case.reopen_count IS '重开次数';
COMMENT ON COLUMN chat_service_case.resolved_at IS '解决时间';
COMMENT ON COLUMN chat_service_case.closed_at IS '关闭时间';
COMMENT ON COLUMN chat_service_case.metadata_json IS '扩展元数据';
COMMENT ON COLUMN chat_service_case.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_service_case.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_service_case.updated_at IS '更新时间';
COMMENT ON COLUMN chat_service_case.created_at IS '创建时间';
