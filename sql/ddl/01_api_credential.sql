-- =============================================================
-- 表：api_credential
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE api_credential (
	key_id VARCHAR(64) NOT NULL, 
	secret_hash VARCHAR(128) NOT NULL, 
	scopes JSONB NOT NULL, 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (key_id)
);

CREATE INDEX ix_api_credential_tenant_status ON api_credential (tenant_id, status);

COMMENT ON COLUMN api_credential.key_id IS '公开键标识';
COMMENT ON COLUMN api_credential.secret_hash IS '密钥哈希';
COMMENT ON COLUMN api_credential.scopes IS '权限范围';
COMMENT ON COLUMN api_credential.status IS '状态';
COMMENT ON COLUMN api_credential.last_used_at IS '最近使用时间';
COMMENT ON COLUMN api_credential.expires_at IS '过期时间';
COMMENT ON COLUMN api_credential.id IS '主键，UUID 字符串';
COMMENT ON COLUMN api_credential.tenant_id IS '租户 ID';
COMMENT ON COLUMN api_credential.updated_at IS '更新时间';
COMMENT ON COLUMN api_credential.created_at IS '创建时间';
