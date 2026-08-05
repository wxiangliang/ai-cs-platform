-- =============================================================
-- 表：kb_chunk
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE kb_chunk (
	document_id VARCHAR(36) NOT NULL, 
	chunk_index INTEGER NOT NULL, 
	section_path VARCHAR(512), 
	content TEXT NOT NULL, 
	embedding_json JSONB, 
	token_count INTEGER, 
	metadata_json JSONB, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_kb_chunk_content_trgm ON kb_chunk USING gin (content gin_trgm_ops);

CREATE INDEX ix_kb_chunk_doc_section ON kb_chunk (document_id, section_path);

CREATE INDEX ix_kb_chunk_tenant_document ON kb_chunk (tenant_id, document_id);

COMMENT ON COLUMN kb_chunk.document_id IS '所属文档 ID';
COMMENT ON COLUMN kb_chunk.chunk_index IS '分块序号';
COMMENT ON COLUMN kb_chunk.section_path IS '章节路径';
COMMENT ON COLUMN kb_chunk.content IS '分块内容';
COMMENT ON COLUMN kb_chunk.embedding_json IS 'embedding 向量';
COMMENT ON COLUMN kb_chunk.token_count IS 'token 数';
COMMENT ON COLUMN kb_chunk.metadata_json IS '过滤维度（category/product_id 等）';
COMMENT ON COLUMN kb_chunk.id IS '主键，UUID 字符串';
COMMENT ON COLUMN kb_chunk.tenant_id IS '租户 ID';
COMMENT ON COLUMN kb_chunk.created_at IS '创建时间';
