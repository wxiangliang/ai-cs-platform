-- =============================================================
-- 表：faq_entry
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE faq_entry (
	question VARCHAR(512) NOT NULL, 
	answer TEXT NOT NULL, 
	question_embedding_json JSONB, 
	category VARCHAR(64), 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	hit_count INTEGER DEFAULT '0' NOT NULL, 
	needs_reindex BOOLEAN DEFAULT 'false' NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_faq_entry_tenant_category ON faq_entry (tenant_id, category);

CREATE INDEX ix_faq_entry_tenant_status ON faq_entry (tenant_id, status);

COMMENT ON COLUMN faq_entry.question IS '标准问题';
COMMENT ON COLUMN faq_entry.answer IS '标准答案';
COMMENT ON COLUMN faq_entry.question_embedding_json IS '问题 embedding';
COMMENT ON COLUMN faq_entry.category IS '分类';
COMMENT ON COLUMN faq_entry.status IS '状态';
COMMENT ON COLUMN faq_entry.hit_count IS '命中次数';
COMMENT ON COLUMN faq_entry.needs_reindex IS '待重建索引';
COMMENT ON COLUMN faq_entry.id IS '主键，UUID 字符串';
COMMENT ON COLUMN faq_entry.tenant_id IS '租户 ID';
COMMENT ON COLUMN faq_entry.updated_at IS '更新时间';
COMMENT ON COLUMN faq_entry.created_at IS '创建时间';
