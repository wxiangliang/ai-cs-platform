-- =============================================================
-- 表：chat_decision_log
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

CREATE TABLE chat_decision_log (
	session_id VARCHAR(36) NOT NULL, 
	message_id VARCHAR(36), 
	original_text TEXT NOT NULL, 
	normalized_text TEXT, 
	intent_result_json JSONB, 
	slot_result_json JSONB, 
	selected_skill VARCHAR(64), 
	status VARCHAR(32), 
	decision_source VARCHAR(32), 
	graph_trace_json JSONB, 
	latency_json JSONB, 
	retrieval_json JSONB, 
	error_json JSONB, 
	experiment_json JSONB, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_decision_log_created_at ON chat_decision_log (created_at);

CREATE INDEX ix_chat_decision_log_tenant_session_created ON chat_decision_log (tenant_id, session_id, created_at);

CREATE INDEX ix_chat_decision_log_tenant_skill ON chat_decision_log (tenant_id, selected_skill);

CREATE INDEX ix_chat_decision_log_tenant_status ON chat_decision_log (tenant_id, status);

COMMENT ON COLUMN chat_decision_log.session_id IS '会话 ID';
COMMENT ON COLUMN chat_decision_log.message_id IS '用户消息 ID';
COMMENT ON COLUMN chat_decision_log.original_text IS '原始文本';
COMMENT ON COLUMN chat_decision_log.normalized_text IS '归一化文本';
COMMENT ON COLUMN chat_decision_log.intent_result_json IS '意图识别结果';
COMMENT ON COLUMN chat_decision_log.slot_result_json IS '槽位抽取结果';
COMMENT ON COLUMN chat_decision_log.selected_skill IS '命中的 Skill';
COMMENT ON COLUMN chat_decision_log.status IS '最终状态';
COMMENT ON COLUMN chat_decision_log.decision_source IS '决策来源';
COMMENT ON COLUMN chat_decision_log.graph_trace_json IS '图执行轨迹';
COMMENT ON COLUMN chat_decision_log.latency_json IS '各阶段耗时';
COMMENT ON COLUMN chat_decision_log.retrieval_json IS '检索过程';
COMMENT ON COLUMN chat_decision_log.error_json IS '错误信息';
COMMENT ON COLUMN chat_decision_log.experiment_json IS 'A/B 实验变体分配';
COMMENT ON COLUMN chat_decision_log.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_decision_log.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_decision_log.created_at IS '创建时间';
