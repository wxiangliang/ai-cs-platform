-- =============================================================
-- ai-cs-platform 全量建表脚本（按依赖顺序）
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================


-- ---------- api_credential ----------

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

-- ---------- chat_csat ----------

CREATE TABLE chat_csat (
	session_id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	score INTEGER NOT NULL, 
	comment TEXT, 
	trigger VARCHAR(32) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_csat_tenant_created ON chat_csat (tenant_id, created_at);

CREATE INDEX ix_chat_csat_tenant_session ON chat_csat (tenant_id, session_id);

COMMENT ON COLUMN chat_csat.session_id IS '会话 ID';
COMMENT ON COLUMN chat_csat.user_id IS '用户 ID';
COMMENT ON COLUMN chat_csat.score IS '评分 1-5';
COMMENT ON COLUMN chat_csat.comment IS '附言';
COMMENT ON COLUMN chat_csat.trigger IS '触发场景';
COMMENT ON COLUMN chat_csat.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_csat.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_csat.created_at IS '创建时间';

-- ---------- chat_decision_log ----------

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

-- ---------- chat_dialog_state ----------

CREATE TABLE chat_dialog_state (
	session_id VARCHAR(36) NOT NULL, 
	state VARCHAR(16) DEFAULT 'IDLE' NOT NULL, 
	active_task_json JSONB, 
	task_stack_json JSONB, 
	context_stacks_json JSONB, 
	version INTEGER DEFAULT '1' NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_chat_dialog_state_tenant_session UNIQUE (tenant_id, session_id)
);

COMMENT ON COLUMN chat_dialog_state.session_id IS '会话 ID';
COMMENT ON COLUMN chat_dialog_state.state IS '状态机状态';
COMMENT ON COLUMN chat_dialog_state.active_task_json IS '当前任务';
COMMENT ON COLUMN chat_dialog_state.task_stack_json IS '挂起任务栈';
COMMENT ON COLUMN chat_dialog_state.context_stacks_json IS '上下文对象栈';
COMMENT ON COLUMN chat_dialog_state.version IS '乐观锁版本号';
COMMENT ON COLUMN chat_dialog_state.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_dialog_state.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_dialog_state.updated_at IS '更新时间';
COMMENT ON COLUMN chat_dialog_state.created_at IS '创建时间';

-- ---------- chat_feedback ----------

CREATE TABLE chat_feedback (
	session_id VARCHAR(36) NOT NULL, 
	message_id VARCHAR(36) NOT NULL, 
	rating VARCHAR(8) NOT NULL, 
	comment TEXT, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_feedback_tenant_rating ON chat_feedback (tenant_id, rating, created_at);

CREATE UNIQUE INDEX uq_chat_feedback_message ON chat_feedback (tenant_id, message_id);

COMMENT ON COLUMN chat_feedback.session_id IS '会话 ID';
COMMENT ON COLUMN chat_feedback.message_id IS '消息 ID';
COMMENT ON COLUMN chat_feedback.rating IS '评价 up/down';
COMMENT ON COLUMN chat_feedback.comment IS '补充说明';
COMMENT ON COLUMN chat_feedback.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_feedback.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_feedback.created_at IS '创建时间';

-- ---------- chat_handoff_ticket ----------

CREATE TABLE chat_handoff_ticket (
	session_id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	reason VARCHAR(32) NOT NULL, 
	source_intent VARCHAR(64), 
	status VARCHAR(16) DEFAULT 'PENDING' NOT NULL, 
	assignee VARCHAR(64), 
	context_json JSONB, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_handoff_tenant_session ON chat_handoff_ticket (tenant_id, session_id);

CREATE INDEX ix_chat_handoff_tenant_status ON chat_handoff_ticket (tenant_id, status);

CREATE UNIQUE INDEX uq_chat_handoff_open_session ON chat_handoff_ticket (tenant_id, session_id) WHERE status IN ('PENDING', 'ASSIGNED');

COMMENT ON COLUMN chat_handoff_ticket.session_id IS '会话 ID';
COMMENT ON COLUMN chat_handoff_ticket.user_id IS '用户 ID';
COMMENT ON COLUMN chat_handoff_ticket.reason IS '触发原因';
COMMENT ON COLUMN chat_handoff_ticket.source_intent IS '触发时的意图码';
COMMENT ON COLUMN chat_handoff_ticket.status IS '工单状态';
COMMENT ON COLUMN chat_handoff_ticket.assignee IS '坐席标识';
COMMENT ON COLUMN chat_handoff_ticket.context_json IS '上下文移交包';
COMMENT ON COLUMN chat_handoff_ticket.resolved_at IS '解决时间';
COMMENT ON COLUMN chat_handoff_ticket.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_handoff_ticket.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_handoff_ticket.updated_at IS '更新时间';
COMMENT ON COLUMN chat_handoff_ticket.created_at IS '创建时间';

-- ---------- chat_message ----------

CREATE TABLE chat_message (
	created_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL, 
	session_id VARCHAR(36) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	content TEXT NOT NULL, 
	intent VARCHAR(64), 
	status VARCHAR(32), 
	slots_json JSONB, 
	trace_id VARCHAR(64), 
	metadata_json JSONB, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_message_tenant_intent ON chat_message (tenant_id, intent);

CREATE INDEX ix_chat_message_tenant_session_created ON chat_message (tenant_id, session_id, created_at);

CREATE INDEX ix_chat_message_trace_id ON chat_message (trace_id);

COMMENT ON COLUMN chat_message.created_at IS '创建时间';
COMMENT ON COLUMN chat_message.session_id IS '会话 ID';
COMMENT ON COLUMN chat_message.role IS '角色';
COMMENT ON COLUMN chat_message.content IS '消息内容';
COMMENT ON COLUMN chat_message.intent IS '最终意图';
COMMENT ON COLUMN chat_message.status IS '本轮处理状态';
COMMENT ON COLUMN chat_message.slots_json IS '槽位';
COMMENT ON COLUMN chat_message.trace_id IS '链路追踪 ID';
COMMENT ON COLUMN chat_message.metadata_json IS '扩展字段';
COMMENT ON COLUMN chat_message.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_message.tenant_id IS '租户 ID';

-- ---------- chat_service_case ----------

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

-- ---------- chat_session ----------

CREATE TABLE chat_session (
	user_id VARCHAR(64) NOT NULL, 
	channel VARCHAR(32) NOT NULL, 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	metadata_json JSONB, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_session_created_at ON chat_session (created_at);

CREATE INDEX ix_chat_session_tenant_status ON chat_session (tenant_id, status);

CREATE INDEX ix_chat_session_tenant_user ON chat_session (tenant_id, user_id);

COMMENT ON COLUMN chat_session.user_id IS '用户 ID';
COMMENT ON COLUMN chat_session.channel IS '渠道';
COMMENT ON COLUMN chat_session.status IS '会话状态：active/closed/handoff';
COMMENT ON COLUMN chat_session.metadata_json IS '扩展字段';
COMMENT ON COLUMN chat_session.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_session.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_session.updated_at IS '更新时间';
COMMENT ON COLUMN chat_session.created_at IS '创建时间';

-- ---------- chat_task ----------

CREATE TABLE chat_task (
	session_id VARCHAR(36) NOT NULL, 
	intent VARCHAR(64) NOT NULL, 
	skill_id VARCHAR(64) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	collected_slots_json JSONB, 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	executed_at TIMESTAMP WITH TIME ZONE, 
	result_json JSONB, 
	version INTEGER DEFAULT '1' NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chat_task_tenant_session ON chat_task (tenant_id, session_id);

CREATE INDEX ix_chat_task_tenant_status ON chat_task (tenant_id, status);

COMMENT ON COLUMN chat_task.session_id IS '会话 ID';
COMMENT ON COLUMN chat_task.intent IS '任务意图';
COMMENT ON COLUMN chat_task.skill_id IS 'Skill 标识';
COMMENT ON COLUMN chat_task.status IS '任务状态';
COMMENT ON COLUMN chat_task.collected_slots_json IS '已收集槽位';
COMMENT ON COLUMN chat_task.confirmed_at IS '用户确认时间';
COMMENT ON COLUMN chat_task.executed_at IS '执行时间';
COMMENT ON COLUMN chat_task.result_json IS '执行结果（工单号等）';
COMMENT ON COLUMN chat_task.version IS '乐观锁版本号';
COMMENT ON COLUMN chat_task.id IS '主键，UUID 字符串';
COMMENT ON COLUMN chat_task.tenant_id IS '租户 ID';
COMMENT ON COLUMN chat_task.updated_at IS '更新时间';
COMMENT ON COLUMN chat_task.created_at IS '创建时间';

-- ---------- chat_tool_call ----------

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

-- ---------- faq_entry ----------

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

-- ---------- kb_chunk ----------

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

-- ---------- kb_document ----------

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

-- ---------- kb_document_version ----------

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

-- ---------- product_item ----------

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

-- ---------- user_memory ----------

CREATE TABLE user_memory (
	user_id VARCHAR(64) NOT NULL, 
	kind VARCHAR(16) DEFAULT 'fact' NOT NULL, 
	content TEXT NOT NULL, 
	source_session_id VARCHAR(36), 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	tenant_id VARCHAR(64) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_user_memory_tenant_user ON user_memory (tenant_id, user_id, status);

COMMENT ON COLUMN user_memory.user_id IS '用户 ID';
COMMENT ON COLUMN user_memory.kind IS '类别';
COMMENT ON COLUMN user_memory.content IS '记忆内容';
COMMENT ON COLUMN user_memory.source_session_id IS '来源会话';
COMMENT ON COLUMN user_memory.status IS '状态';
COMMENT ON COLUMN user_memory.id IS '主键，UUID 字符串';
COMMENT ON COLUMN user_memory.tenant_id IS '租户 ID';
COMMENT ON COLUMN user_memory.updated_at IS '更新时间';
COMMENT ON COLUMN user_memory.created_at IS '创建时间';
