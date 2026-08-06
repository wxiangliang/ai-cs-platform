-- =============================================================
-- 表：customer_journey
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE customer_journey (
	user_id VARCHAR(64) NOT NULL, 
	stage VARCHAR(20) DEFAULT 'NEW' NOT NULL, 
	at_risk BOOLEAN DEFAULT 'false' NOT NULL, 
	signals_json JSONB, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_customer_journey_stage ON customer_journey (tenant_id, stage);

CREATE UNIQUE INDEX uq_customer_journey_tenant_user ON customer_journey (tenant_id, user_id);

COMMENT ON COLUMN customer_journey.user_id IS '客户标识';
COMMENT ON COLUMN customer_journey.stage IS '旅程阶段';
COMMENT ON COLUMN customer_journey.at_risk IS '流失风险叠加标记（非阶段）';
COMMENT ON COLUMN customer_journey.signals_json IS '转移证据史';
COMMENT ON COLUMN customer_journey.id IS '主键，UUID 字符串';
COMMENT ON COLUMN customer_journey.tenant_id IS '租户 ID';
COMMENT ON COLUMN customer_journey.updated_at IS '更新时间';
COMMENT ON COLUMN customer_journey.created_at IS '创建时间';
