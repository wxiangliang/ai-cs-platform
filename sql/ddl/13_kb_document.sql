-- =============================================================
-- 表：kb_document
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE kb_document (
	source_type VARCHAR(32) NOT NULL, 
	title VARCHAR(256) NOT NULL, 
	raw_content TEXT NOT NULL, 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	published_version INTEGER, 
	needs_reindex BOOLEAN DEFAULT 'false' NOT NULL, 
	file_name VARCHAR(256), 
	parser VARCHAR(32), 
	metadata_json JSONB, 
	effective_from TIMESTAMP WITH TIME ZONE, 
	expire_at TIMESTAMP WITH TIME ZONE, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_kb_document_tenant_source ON kb_document (tenant_id, source_type);

CREATE INDEX ix_kb_document_tenant_status ON kb_document (tenant_id, status);

COMMENT ON COLUMN kb_document.source_type IS '来源类型';
COMMENT ON COLUMN kb_document.title IS '文档标题';
COMMENT ON COLUMN kb_document.raw_content IS '原始内容';
COMMENT ON COLUMN kb_document.status IS '状态';
COMMENT ON COLUMN kb_document.published_version IS '当前线上版本号';
COMMENT ON COLUMN kb_document.needs_reindex IS '待重建索引';
COMMENT ON COLUMN kb_document.file_name IS '原始文件名';
COMMENT ON COLUMN kb_document.parser IS '解析器';
COMMENT ON COLUMN kb_document.metadata_json IS '扩展字段（分类/商品ID等过滤维度）';
COMMENT ON COLUMN kb_document.effective_from IS '定时生效时间';
COMMENT ON COLUMN kb_document.expire_at IS '定时失效时间';
COMMENT ON COLUMN kb_document.id IS '主键，UUID 字符串';
COMMENT ON COLUMN kb_document.tenant_id IS '租户 ID';
COMMENT ON COLUMN kb_document.updated_at IS '更新时间';
COMMENT ON COLUMN kb_document.created_at IS '创建时间';
