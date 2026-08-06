-- =============================================================
-- 表：product_item
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE product_item (
	product_code VARCHAR(64), 
	name VARCHAR(256) NOT NULL, 
	category VARCHAR(64), 
	price_cents INTEGER, 
	stock INTEGER, 
	attrs_json JSONB, 
	description TEXT, 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_product_item_name_trgm ON product_item USING gin (name gin_trgm_ops);

CREATE INDEX ix_product_item_tenant_code ON product_item (tenant_id, product_code);

CREATE INDEX ix_product_item_tenant_status ON product_item (tenant_id, status);

COMMENT ON COLUMN product_item.product_code IS '商品编码';
COMMENT ON COLUMN product_item.name IS '商品名称';
COMMENT ON COLUMN product_item.category IS '分类';
COMMENT ON COLUMN product_item.price_cents IS '价格（分）';
COMMENT ON COLUMN product_item.stock IS '库存';
COMMENT ON COLUMN product_item.attrs_json IS '规格属性';
COMMENT ON COLUMN product_item.description IS '商品简介';
COMMENT ON COLUMN product_item.status IS '状态';
COMMENT ON COLUMN product_item.id IS '主键，UUID 字符串';
COMMENT ON COLUMN product_item.tenant_id IS '租户 ID';
COMMENT ON COLUMN product_item.updated_at IS '更新时间';
COMMENT ON COLUMN product_item.created_at IS '创建时间';
