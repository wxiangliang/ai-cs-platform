"""决策日志数据结构。"""

from dataclasses import dataclass
from typing import Any


@dataclass
class DecisionLogData:
    """对应 chat_decision_log 表的一条决策记录（落库前的内存结构）。"""

    tenant_id: str
    session_id: str
    original_text: str
    message_id: str | None = None
    normalized_text: str | None = None
    intent_result_json: dict[str, Any] | None = None
    slot_result_json: dict[str, Any] | None = None
    selected_skill: str | None = None
    status: str | None = None
    decision_source: str | None = None
    graph_trace_json: dict[str, Any] | None = None
    latency_json: dict[str, Any] | None = None
    error_json: dict[str, Any] | None = None
    # 检索过程（Stage 06 RAG）：查询词、命中列表、是否拒答/降级、引用
    retrieval_json: dict[str, Any] | None = None
    # A/B 实验变体分配（Stage 18）：{assignments, overrides}，无实验为空
    experiment_json: dict[str, Any] | None = None
