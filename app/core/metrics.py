"""运行时指标（Stage 09，Prometheus 直埋）。

原则：
- 指标注册集中在本模块，埋点方通过助手函数调用，禁止散落重复计数；
- Counter/Histogram 皆进程内存操作，不增加主链路显著延迟；
- **label 基数受控**：多租户维度不进 label（基数爆炸，租户级分析走 SQL）；
  耗时直方图用意图域（9 个）而非 33 个意图码全量；工具 id 只收白名单内的。
"""

import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)

# ---------------------------------------------------------------------------
# 指标定义（单一注册点）
# ---------------------------------------------------------------------------

# 轮次耗时：按意图域 × 回复分支（template/rag/product/tool/action）
CHAT_TURN_DURATION = Histogram(
    "chat_turn_duration_seconds",
    "单轮聊天处理耗时（秒）",
    ["intent_domain", "branch"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

# 知识检索分段耗时：定位链路热点（stage=embed/vector/keyword/rerank/sections/llm/cache_lookup）
# 没有它，各阶段的相对权重只能靠读代码推断，优化收益无法在线上验证
KB_STAGE_DURATION = Histogram(
    "kb_stage_duration_seconds",
    "知识检索管道各阶段耗时（秒）",
    ["stage"],
    buckets=(0.005, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0),
)

# 意图决策：观测 SETFIT / LLM / 规则 / 降级来源占比
INTENT_DECISIONS = Counter(
    "intent_decisions_total",
    "意图识别决策次数",
    ["pred_label", "decision_source"],
)

# LLM 调用与失败（purpose=classify/generate）
LLM_CALLS = Counter("llm_calls_total", "LLM 调用次数", ["purpose"])
LLM_FAILURES = Counter("llm_failures_total", "LLM 调用失败次数", ["purpose"])

# LLM token 用量与预算熔断（Stage 17）
# 基数整改（Stage 25，post-stage-19 P0）：tenant 不进 label（模块原则），
# Prometheus 只保聚合；租户级 token 明细走 budget 模块的 Redis 日计数
# （llm_budget:{tenant}:{day}），查询口径见 docs/ops/monitoring.md
LLM_TOKENS = Counter("llm_tokens_total", "LLM token 用量", ["purpose"])
LLM_BUDGET_EXCEEDED = Counter("llm_budget_exceeded_total", "LLM 预算熔断次数")

# 语义缓存结局：hit / miss / store（Stage 17）
SEMANTIC_CACHE = Counter(
    "semantic_cache_total", "语义缓存命中/未命中/写入次数", ["outcome"]
)

# RAG 检索结局：faq_hit / rag_answer / refused / degraded
RAG_RETRIEVALS = Counter("rag_retrievals_total", "RAG 检索次数", ["outcome"])

# 只读诊断 agent（Stage 22）：answered=解释生效 / degraded=触发但降级回静态链
DIAGNOSE_AGENT = Counter("diagnose_agent_total", "只读诊断 agent 结局", ["outcome"])

# 方向纠偏（Stage 23）：task_denied=任务中途否定 / soft_confirm=中置信软确认
DIRECTION_CORRECTION = Counter(
    "direction_correction_total", "对话方向纠偏触发次数", ["kind"]
)

# 确认门结局：confirmed / denied / modified
CONFIRM_GATE = Counter("confirm_gate_total", "确认门应答次数", ["outcome"])

# Meta-classifier 影子模式（Stage 27）：decision=影子预测的 6 类决策，
# agree=与实际链路决策（Stage 26 阈值）是否一致——分歧率是接管评估的核心口径
META_SHADOW = Counter(
    "meta_shadow_total", "Meta-classifier 影子预测次数", ["decision", "agree"]
)

# 写操作执行（tool_id 白名单控制基数，ok=true/false）
ACTION_EXECUTIONS = Counter(
    "action_executions_total", "写操作执行次数", ["tool_id", "ok"]
)

# 转人工建单（reason 枚举 6 个）
HANDOFF_TICKETS = Counter("handoff_tickets_total", "转人工建单次数", ["reason"])

# 限流触发（scope=tenant/session）
RATE_LIMITED = Counter("rate_limited_total", "限流触发次数", ["scope"])

# 反馈（rating=up/down）
FEEDBACK = Counter("chat_feedback_total", "用户反馈次数", ["rating"])

# 护栏拦截（rule=规则 id，规则库有限集合，基数受控；Stage 14）
GUARDRAIL_BLOCKS = Counter("guardrail_blocks_total", "护栏拦截次数", ["rule"])

# 写操作 tool_id 白名单（防未知 id 撑爆 label 基数；白名单外统一记 other）
_ACTION_TOOL_WHITELIST = frozenset(
    {
        "create_refund_ticket",
        "create_return_ticket",
        "create_exchange_ticket",
        "create_repair_ticket",
        "create_complaint_ticket",
        "cancel_order",
        "update_order_address",
        "create_invoice",
    }
)


# ---------------------------------------------------------------------------
# 埋点助手
# ---------------------------------------------------------------------------


def intent_domain(intent: str | None) -> str:
    """意图码 → 意图域（ORDER.CANCEL → ORDER），空值归 UNKNOWN。"""
    if not intent:
        return "UNKNOWN"
    return intent.split(".", 1)[0]


def observe_turn(intent: str | None, branch: str | None, seconds: float) -> None:
    """记录一轮耗时（chat_service 收口调用）。"""
    CHAT_TURN_DURATION.labels(
        intent_domain=intent_domain(intent), branch=branch or "template"
    ).observe(seconds)


def count_intent(pred_label: str | None, decision_source: str | None) -> None:
    """记录一次意图决策（intent_classify 节点收口调用）。"""
    INTENT_DECISIONS.labels(
        pred_label=pred_label or "META.UNKNOWN",
        decision_source=decision_source or "UNKNOWN",
    ).inc()


def count_llm_call(purpose: str, ok: bool) -> None:
    """记录一次 LLM 调用（llm 工厂收口调用）。"""
    LLM_CALLS.labels(purpose=purpose).inc()
    if not ok:
        LLM_FAILURES.labels(purpose=purpose).inc()


def count_llm_tokens(purpose: str, tokens: int) -> None:
    """累计 LLM token 用量（Stage 17；Stage 25 起不带租户维度，租户明细走 Redis/SQL）。"""
    if tokens > 0:
        LLM_TOKENS.labels(purpose=purpose).inc(tokens)


def count_llm_budget_exceeded() -> None:
    """记录一次 LLM 预算熔断（Stage 17；Stage 25 起不带租户维度）。"""
    LLM_BUDGET_EXCEEDED.inc()


def count_semantic_cache(outcome: str) -> None:
    """记录一次语义缓存结局（Stage 17，outcome=hit/miss/store）。"""
    SEMANTIC_CACHE.labels(outcome=outcome).inc()


def count_rag(outcome: str) -> None:
    """记录一次 RAG 检索结局（rag_answer 节点收口调用）。"""
    RAG_RETRIEVALS.labels(outcome=outcome).inc()


def observe_kb_stage(stage: str, seconds: float) -> None:
    """记录知识检索某阶段的耗时（retriever/answerer 打点）。"""
    KB_STAGE_DURATION.labels(stage=stage).observe(seconds)


def count_diagnose(outcome: str) -> None:
    """记录一次只读诊断 agent 结局（Stage 22，outcome=answered/degraded）。"""
    DIAGNOSE_AGENT.labels(outcome=outcome).inc()


def count_direction(kind: str) -> None:
    """记录一次方向纠偏触发（Stage 23，kind=task_denied/soft_confirm）。"""
    DIRECTION_CORRECTION.labels(kind=kind).inc()


def count_confirm_gate(outcome: str) -> None:
    """记录一次确认门结局（save_turn 收口调用）。"""
    CONFIRM_GATE.labels(outcome=outcome).inc()


def count_meta_shadow(decision: str, agree: bool) -> None:
    """记录一次 Meta-classifier 影子预测（Stage 27，decision=6 类决策码）。"""
    META_SHADOW.labels(decision=decision, agree=str(agree).lower()).inc()


def count_action(tool_id: str | None, ok: bool) -> None:
    """记录一次写操作执行（ActionExecutor 收口调用）。"""
    label = tool_id if tool_id in _ACTION_TOOL_WHITELIST else "other"
    ACTION_EXECUTIONS.labels(tool_id=label, ok=str(ok).lower()).inc()


def count_handoff(reason: str) -> None:
    """记录一次转人工建单（HandoffService 收口调用，仅新建时）。"""
    HANDOFF_TICKETS.labels(reason=reason).inc()


def count_rate_limited(scope: str) -> None:
    """记录一次限流触发（rate_limit 收口调用）。"""
    RATE_LIMITED.labels(scope=scope).inc()


def count_feedback(rating: str) -> None:
    """记录一次用户反馈。"""
    FEEDBACK.labels(rating=rating).inc()


def count_guardrail_block(rule: str | None) -> None:
    """记录一次护栏拦截（guardrail_check / 输出护栏收口调用）。"""
    GUARDRAIL_BLOCKS.labels(rule=rule or "UNKNOWN").inc()


def render_metrics() -> tuple[bytes, str]:
    """导出 Prometheus 文本格式：(payload, content_type)。

    多进程部署（Stage 13）：设置 PROMETHEUS_MULTIPROC_DIR 环境变量后，
    prometheus_client 自动把计数写共享 mmap 文件，此处聚合全部 worker——
    否则 `--workers N` 下抓取随机命中单个进程，计数严重偏低。
    """
    mp_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if mp_dir:
        from prometheus_client import CollectorRegistry, multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry), CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST
