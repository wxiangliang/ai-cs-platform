"""节点：tool_invoke —— 读操作工具查询（Stage 05）。

进入条件（builder 路由）：订单/物流查询类意图且槽位齐全（status=DONE）。
按 Skill 声明的 required_tools 依次调用（mock），把「帮您核实后回复」
升级为真实（mock）数据回答；全部工具失败时按 R4 走 RAG 兜底
（skill.rag_fallback）或降级原模板。
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.chat.agents.diagnose import needs_diagnosis, run_diagnose
from app.chat.graph.state import GraphState, get_db_session_from_config
from app.chat.intent.types import IntentLabel
from app.chat.skills.registry import skill_registry
from app.chat.skills.responder import render_reply
from app.chat.tools.base import mask_sensitive
from app.chat.tools.factory import get_tool_provider
from app.core.config import settings
from app.core.i18n import t as t_
from app.core.logging import get_logger
from app.repositories.chat_tool_call_repository import chat_tool_call_repository

logger = get_logger(__name__)

# 本节点服务的读查询意图（builder 路由条件与此保持一致）
TOOL_QUERY_INTENTS = frozenset(
    {
        IntentLabel.ORDER_QUERY_STATUS,
        IntentLabel.LOGISTICS_TRACK,
        "LOGISTICS.DELIVERY_TIME",
    }
)


def _format_reply(intent: str, data: dict[str, Any]) -> str:
    """按意图把工具返回组装成事实回复（数字/状态原样引用，不经生成）。"""
    order_id = data.get("order_id", "")
    if intent == IntentLabel.ORDER_QUERY_STATUS:
        text = f"您的订单「{order_id}」当前状态：{data.get('status', '未知')}"
        if data.get("tracking_number"):
            text += f"，运单号 {data['tracking_number']}"
        return text + "。"
    if intent == IntentLabel.LOGISTICS_TRACK:
        events = data.get("events") or []
        latest = data.get("latest", "")
        lines = [f"订单「{order_id}」物流进度：{latest}"]
        lines += [f"- {e}" for e in events[-3:]]
        if data.get("eta"):
            lines.append(data["eta"])
        return "\n".join(lines)
    # LOGISTICS.DELIVERY_TIME
    return f"订单「{order_id}」{data.get('eta', '发货时间我帮您核实后回复')}。"


async def tool_invoke(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """读操作工具查询与回复组装。"""
    session = get_db_session_from_config(config)
    tenant_id = state["tenant_id"]
    session_id = state["session_id"]

    intent_dict = state.get("intent_result") or {}
    intent = intent_dict.get("final_intent") or intent_dict.get("pred_label", "")
    slots = state.get("effective_slots") or state.get("slots") or {}
    skill = skill_registry.get(intent)
    provider = get_tool_provider()

    # 按 Skill 声明的工具链依次调用，返回数据逐步合并（后者可用前者的返回，
    # 如 query_order 的 tracking_number 供 query_logistics_track 使用）
    tool_ids = [t.tool_id for t in skill.required_tools if not t.optional] or ["query_order"]

    # —— Stage 35 身份等级执法（读侧）：任一必需工具等级不足 → 不调用，
    # 回核实话术 + 建单交人工核实（涉个人数据的查询不能只凭「知道订单号」）——
    from app.core.identity import identity_sufficient

    lacking = [x for x in tool_ids if not identity_sufficient(x)]
    if lacking:
        identity_trace: dict[str, Any] = {"tool_calls": [
            {"tool_id": x, "ok": False, "error_code": "IDENTITY_REQUIRED"} for x in lacking
        ]}
        try:
            from app.services.handoff_service import handoff_service

            ticket_id, created = await handoff_service.ensure_ticket(
                session,
                tenant_id=tenant_id,
                session_id=session_id,
                user_id=state.get("user_id", ""),
                reason="IDENTITY_VERIFY",
                source_intent=intent,
            )
            identity_trace["handoff"] = {"reason": "IDENTITY_VERIFY",
                                         "ticket_id": ticket_id, "created": created}
        except Exception:  # noqa: BLE001 - 建单失败只告警
            logger.exception("handoff ticket for identity verify failed")
        return {
            "reply": t_("identity.verify_required", state.get("locale")),
            "answer_source": "tool",
            "retrieval": identity_trace,
            "graph_trace": ["tool_invoke:identity"],
        }
    merged: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []
    params = {**slots}
    active_task_id = (state.get("active_task") or {}).get("task_id")
    for tool_id in tool_ids:
        result = await provider.invoke(tool_id, {**params, **merged}, tenant_id=tenant_id)
        await chat_tool_call_repository.create(
            session,
            tenant_id=tenant_id,
            session_id=session_id,
            task_id=active_task_id,
            tool_id=tool_id,
            request_json=mask_sensitive(params),
            response_json=result.data if result.ok else None,
            ok=result.ok,
            error_code=result.error_code,
            latency_ms=result.latency_ms,
        )
        calls.append({"tool_id": tool_id, "ok": result.ok, "error_code": result.error_code})
        if result.ok:
            merged.update(result.data)

    if merged:
        reply = _format_reply(intent, merged)
        # —— Stage 22 只读诊断 agent（默认关）：解释性问句（"为什么还没到"）
        # 才触发多步只读调查，解释段追加在事实底稿之后（底稿不经生成改写，
        # 红线不动）；任一环节失败走原路径，行为与静态链一致 ——
        normalized = state.get("normalized_text", "")
        if settings.DIAGNOSE_AGENT_ENABLED and needs_diagnosis(normalized):
            from app.core.metrics import count_diagnose

            outcome = None
            try:
                outcome = await run_diagnose(
                    session,
                    tenant_id,
                    session_id,
                    user_text=normalized,
                    observations=merged,
                    slots=dict(slots),
                    task_id=active_task_id,
                )
            except Exception:  # noqa: BLE001 - 诊断失败绝不打断静态链回复
                logger.exception("diagnose agent failed, fallback to static reply")
            if outcome is not None:
                count_diagnose("answered")
                return {
                    "reply": f"{reply}\n{outcome.explanation}",
                    "answer_source": "tool",
                    "retrieval": {
                        "tool_calls": calls + outcome.steps,
                        "diagnose": True,
                    },
                    "graph_trace": ["tool_invoke:diagnose"],
                }
            count_diagnose("degraded")
        return {
            "reply": reply,
            "answer_source": "tool",
            "retrieval": {"tool_calls": calls},
            "graph_trace": ["tool_invoke"],
        }

    # 工具全部失败：requires_human_if 机读安全阀（Stage 07）——
    # Skill 声明了转人工条件的，自动建工单（v1 接「工具返回异常」这一机读条件）
    logger.warning("tool_invoke all tools failed: intent=%s calls=%s", intent, calls)
    handoff_trace: dict[str, Any] = {}
    if skill.constraints.get("requires_human_if"):
        try:
            from app.services.handoff_service import REASON_SKILL_RULE, handoff_service

            ticket_id, created = await handoff_service.ensure_ticket(
                session,
                tenant_id=tenant_id,
                session_id=session_id,
                user_id=state.get("user_id", ""),
                reason=REASON_SKILL_RULE,
                source_intent=intent,
                extra_context={"failed_tools": calls},
            )
            handoff_trace = {
                "handoff": {"reason": REASON_SKILL_RULE, "ticket_id": ticket_id, "created": created}
            }
        except Exception:  # noqa: BLE001
            logger.exception("handoff ticket for skill rule failed")
    if skill.rag_fallback:
        from app.kb.answerer import rag_answerer

        try:
            answer, trace = await rag_answerer.answer(
                session, tenant_id, state.get("normalized_text", "")
            )
            if answer is not None:
                return {
                    "reply": answer.reply,
                    "answer_source": answer.source,
                    "retrieval": {**trace.to_dict(), "tool_calls": calls, **handoff_trace},
                    "graph_trace": ["tool_invoke"],
                }
        except Exception:  # noqa: BLE001
            logger.exception("tool_invoke rag fallback failed")

    reply = render_reply(state.get("status", ""), skill, slots, state.get("locale"))
    return {
        "reply": reply,
        "answer_source": "template",
        "retrieval": {"tool_calls": calls, "degraded": True, **handoff_trace},
        "graph_trace": ["tool_invoke"],
    }
