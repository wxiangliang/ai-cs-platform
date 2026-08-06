"""决策日志记录器（DecisionLogger）。

把一轮聊天的完整决策过程组装成 chat_decision_log 记录并落库。
第一版字段可以简化，但所有字段入口必须保留，方便后续训练与排查。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.logging.types import DecisionLogData
from app.repositories.chat_decision_log_repository import chat_decision_log_repository


class DecisionLogger:
    """决策日志写入器。只负责把决策数据落库。"""

    async def save(self, session: AsyncSession, data: DecisionLogData) -> str:
        """写入一条决策日志，返回日志 ID。"""
        record = await chat_decision_log_repository.create(
            session,
            tenant_id=data.tenant_id,
            session_id=data.session_id,
            message_id=data.message_id,
            original_text=data.original_text,
            normalized_text=data.normalized_text,
            intent_result_json=data.intent_result_json,
            slot_result_json=data.slot_result_json,
            selected_skill=data.selected_skill,
            status=data.status,
            decision_source=data.decision_source,
            graph_trace_json=data.graph_trace_json,
            latency_json=data.latency_json,
            error_json=data.error_json,
            retrieval_json=data.retrieval_json,
            experiment_json=data.experiment_json,
        )
        return record.id


def build_log_data(state: dict[str, Any]) -> DecisionLogData:
    """从 LangGraph 最终 state 组装决策日志数据。

    脱敏（Stage 13）：原文/归一化文本/槽位过 mask_sensitive（手机号+地址打码）
    再落库——决策日志会被回放 CLI 与回流导出消费，PII 不得随之外流。
    """
    from app.chat.tools.base import mask_sensitive

    intent_result = state.get("intent_result") or {}
    masked_texts = mask_sensitive(
        {"original": state.get("message", ""), "normalized": state.get("normalized_text") or ""}
    )
    return DecisionLogData(
        tenant_id=state["tenant_id"],
        session_id=state["session_id"],
        message_id=state.get("user_message_id"),
        original_text=masked_texts["original"],
        normalized_text=masked_texts["normalized"] or None,
        intent_result_json=intent_result,
        slot_result_json=mask_sensitive(dict(state.get("slots") or {})),
        selected_skill=state.get("selected_skill"),
        status=state.get("status"),
        decision_source=intent_result.get("decision_source"),
        # 护栏结论随图轨迹落库（Stage 14）：命中规则 id / 类别 / 动作（block/flag/pass）
        # Meta 影子预测随图轨迹落库（Stage 27）：decision/actual/agree/model——
        # 分歧率分析与真实特征表重训的数据来源
        graph_trace_json={
            "trace": state.get("graph_trace", []),
            **({"guardrail": state["guardrail"]} if state.get("guardrail") else {}),
            **({"meta_shadow": state["meta_shadow"]} if state.get("meta_shadow") else {}),
            **({"proactive": state["proactive"]} if state.get("proactive") else {}),
            **({"guidelines": state["guidelines"]} if state.get("guidelines") else {}),
        },
        latency_json=state.get("latency") or {},
        error_json=state.get("error"),
        retrieval_json=state.get("retrieval"),
        # A/B 实验变体分配（Stage 18）：resolve_experiment 在图入口写入 state
        experiment_json=state.get("experiment"),
    )


# 模块级单例
decision_logger = DecisionLogger()
