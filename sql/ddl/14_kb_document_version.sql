-- =============================================================
-- 表：kb_document_version
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE kb_document_version (
	document_id VARCHAR(36) NOT NULL, 
	version INTEGER NOT NULL, 
	title VARCHAR(256) NOT NULL, 
	raw_content TEXT NOT NULL, 
	source_type VARCHAR(32) NOT NULL, 
	editor VARCHAR(64), 
	note TEXT, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_kb_doc_version_tenant_doc ON kb_document_version (tenant_id, document_id, version);

COMMENT ON COLUMN kb_document_version.document_id IS '所属文档 ID';
COMMENT ON COLUMN kb_document_version.version IS '版本号';
COMMENT ON COLUMN kb_document_version.title IS '标题';
COMMENT ON COLUMN kb_document_version.raw_content IS '原始内容';
COMMENT ON COLUMN kb_document_version.source_type IS '来源类型';
COMMENT ON COLUMN kb_document_version.editor IS '编辑者';
COMMENT ON COLUMN kb_document_version.note IS '编辑说明';
COMMENT ON COLUMN kb_document_version.id IS '主键，UUID 字符串';
COMMENT ON COLUMN kb_document_version.tenant_id IS '租户 ID';
COMMENT ON COLUMN kb_document_version.created_at IS '创建时间';
