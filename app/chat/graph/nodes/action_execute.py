"""节点：action_execute —— 确认门通过后的写操作执行（Stage 05）。

进入条件（builder 路由）：status=CONFIRMED 且 finished_task 存在。
调 ActionExecutor（唯一写入口）执行任务的 action 工具（mock），
成功回执带工单号；失败给安抚话术并建议人工跟进——执行失败绝不谎报成功。
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.chat.actions.executor import action_executor
from app.chat.graph.state import GraphState, get_db_session_from_config
from app.chat.skills.registry import skill_registry
from app.chat.skills.responder import render_reply
from app.core.i18n import t
from app.core.logging import get_logger

logger = get_logger(__name__)


async def action_execute(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """执行确认通过的任务并生成回执。"""
    session = get_db_session_from_config(config)
    tenant_id = state["tenant_id"]
    session_id = state["session_id"]
    task = state.get("finished_task") or {}
    intent = task.get("intent", "")
    slots = task.get("collected_slots", {})
    skill = skill_registry.get(intent)

    try:
        outcome = await action_executor.execute(
            session, tenant_id=tenant_id, session_id=session_id, task=task
        )
    except Exception:  # noqa: BLE001 - 执行器异常降级为受理话术，不打断主链路
        logger.exception("action execute failed: intent=%s", intent)
        outcome = None

    tool_trace: dict[str, Any] = {
        "tool_calls": [
            {
                "tool_id": getattr(outcome, "tool_id", None),
                "ok": bool(outcome and outcome.ok),
                "error_code": getattr(outcome, "error_code", "EXECUTOR_ERROR"),
            }
        ]
    }

    if outcome is not None and outcome.ok:
        # 成功回执：受理模板 + 工单号（事实来自工具返回，不经生成改写）
        base = render_reply(state.get("status", ""), skill, slots, state.get("locale"))
        ticket = (
            t("action.ticket_no", state.get("locale"), ticket_no=outcome.ticket_no)
            if outcome.ticket_no else ""
        )
        reply = f"{base}{ticket}".strip()
        return {
            "reply": reply,
            "answer_source": "action_executor",
            "retrieval": tool_trace,
            "graph_trace": ["action_execute"],
        }

    if outcome is not None and outcome.error_code == "ALREADY_EXECUTED":
        reply = t("action.already_executed", state.get("locale"))
        return {
            "reply": reply,
            "answer_source": "action_executor",
            "retrieval": tool_trace,
            "graph_trace": ["action_execute"],
        }

    # 执行失败：自动建转人工工单（Stage 07，「已记录由人工跟进」不再是空话）
    reply = t("action.failed", state.get("locale"))
    try:
        from app.services.handoff_service import REASON_EXECUTION_FAILED, handoff_service

        ticket_id, created = await handoff_service.ensure_ticket(
            session,
            tenant_id=tenant_id,
            session_id=session_id,
            user_id=state.get("user_id", ""),
            reason=REASON_EXECUTION_FAILED,
            source_intent=intent,
            extra_context={"failed_action": tool_trace["tool_calls"]},
        )
        tool_trace["handoff"] = {
            "reason": REASON_EXECUTION_FAILED, "ticket_id": ticket_id, "created": created,
        }
    except Exception:  # noqa: BLE001 - 建单失败只告警，不影响回复
        logger.exception("handoff ticket for failed action failed")
    return {
        "reply": reply,
        "answer_source": "action_executor",
        "retrieval": tool_trace,
        "graph_trace": ["action_execute"],
    }
