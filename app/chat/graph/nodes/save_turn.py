"""节点：save_turn —— 保存消息与决策日志。

落库内容：
1. 用户消息（chat_message, role=user）；
2. AI 回复消息（chat_message, role=assistant）；
3. 对话状态机（chat_dialog_state，upsert，含任务栈与上下文栈）；
4. chat_task 行同步（Stage 05：创建/状态推进/挂起/结束）；
5. 转人工触发收口（Stage 07：USER_REQUEST / PAYMENT_ISSUE / REPEATED_UNKNOWN 建单）；
6. 决策日志（chat_decision_log）。

另：本轮从栈恢复了挂起任务时（resumed_task），在回复末尾追加续办提示——
回复文本在此定稿（各回复节点之后、落库之前的唯一收口点）。

仅做 flush，事务提交由上层（get_db_session 依赖）统一处理。
"""

import time
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.graph.state import GraphState, get_db_session_from_config
from app.chat.intent.types import IntentLabel
from app.chat.logging.decision_logger import build_log_data, decision_logger
from app.chat.skills.registry import skill_registry
from app.chat.skills.responder import render_reply
from app.chat.state.types import DialogStateValue, TurnStatus
from app.core.config import settings
from app.core.i18n import t
from app.core.metrics import count_confirm_gate
from app.repositories.chat_dialog_state_repository import chat_dialog_state_repository
from app.repositories.chat_message_repository import chat_message_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.repositories.chat_task_repository import chat_task_repository
from app.services.handoff_service import (
    REASON_PAYMENT_ISSUE,
    REASON_REPEATED_UNKNOWN,
    REASON_USER_REQUEST,
    handoff_service,
)

# 状态机状态 → chat_task 行状态
_TASK_STATUS_BY_STATE = {
    DialogStateValue.COLLECTING: "COLLECTING",
    DialogStateValue.CONFIRMING: "CONFIRMING",
}


async def _sync_task_rows(session: AsyncSession, state: GraphState) -> None:
    """同步 chat_task 表（Stage 05）。

    - 当前任务无 task_id → 创建行并回填 task_id（随 active_task_json 持久化）；
    - 有 task_id → 推进行状态与已收集槽位；
    - 栈中挂起任务 → 行状态 SUSPENDED；
    - 读任务完成/否认取消（无 finished_task 执行路径）→ 行状态 DONE/ABORTED
      （执行路径的行状态由 ActionExecutor 维护）。
    """
    tenant_id = state["tenant_id"]
    session_id = state["session_id"]
    active_task = state.get("active_task")
    status = state.get("status")

    # 1. 当前任务：建行 / 推进
    if active_task is not None:
        task_id = active_task.get("task_id")
        row_status = _TASK_STATUS_BY_STATE.get(state.get("new_state", ""), "COLLECTING")
        if not task_id:
            skill = skill_registry.get(active_task.get("intent", ""))
            created = await chat_task_repository.create(
                session,
                tenant_id=tenant_id,
                session_id=session_id,
                intent=active_task.get("intent", ""),
                skill_id=skill.skill_id,
                status=row_status,
                collected_slots_json=dict(active_task.get("collected_slots", {})),
            )
            active_task["task_id"] = created.id  # 回填引用，随 dialog_state 持久化
        else:
            row = await chat_task_repository.get_owned(session, tenant_id, task_id)
            if row is not None and row.status not in ("DONE", "FAILED"):
                row.status = row_status
                row.collected_slots_json = dict(active_task.get("collected_slots", {}))

    # 2. 挂起栈中的任务 → SUSPENDED
    for suspended in state.get("task_stack") or []:
        sid = suspended.get("task_id")
        if sid:
            srow = await chat_task_repository.get_owned(session, tenant_id, sid)
            if srow is not None and srow.status not in ("DONE", "FAILED", "SUSPENDED"):
                srow.status = "SUSPENDED"

    # 3. 非执行路径的任务终结：上一轮任务消失且未走 ActionExecutor
    prev_task = state.get("prev_active_task")
    if prev_task and prev_task.get("task_id"):
        prev_id = prev_task["task_id"]
        still_active = active_task is not None and active_task.get("task_id") == prev_id
        in_stack = any(t.get("task_id") == prev_id for t in state.get("task_stack") or [])
        executed = (state.get("finished_task") or {}).get("task_id") == prev_id
        if not still_active and not in_stack and not executed:
            prow = await chat_task_repository.get_owned(session, tenant_id, prev_id)
            if prow is not None and prow.status not in ("DONE", "FAILED"):
                prow.status = "ABORTED" if status in (TurnStatus.ABORTED, TurnStatus.HANDOFF) else "DONE"
    await session.flush()


def _resume_note(state: GraphState) -> str:
    """恢复挂起任务的续办提示（追加到本轮回复末尾）。

    有 task_id 的是此前挂起的任务（「继续刚才的」）；
    没有 task_id 的是本轮多意图拆出的次要诉求（「关于您提到的」，Stage 10）。
    """
    resumed = state.get("resumed_task")
    if not resumed:
        return ""
    skill = skill_registry.get(resumed.get("intent", ""))
    # 恢复任务的等待状态 → 对应追问/确认话术
    resume_status = (
        TurnStatus.NEEDS_CONFIRM
        if state.get("new_state") == DialogStateValue.CONFIRMING
        else TurnStatus.NEEDS_SLOT
    )
    locale = state.get("locale")
    if resumed.get("ready"):
        # 槽位已齐的读任务：引导用户回复「继续」触发查询（下一轮续接执行）
        ask = t("resume.ready", locale)
    else:
        ask = render_reply(resume_status, skill, resumed.get("collected_slots", {}), locale)
    key = "resume.suspended" if resumed.get("task_id") else "resume.pending"
    # 零进度恢复降调（Stage 23 方向纠偏）：一个槽位都没收集过的挂起任务
    # 很可能是误判开出的——恢复时降调为「可选续办」并给出退出话术，
    # 配合 COLLECTING 否定通道一句话即可干净退出，不再是强制续问
    if (
        key == "resume.suspended"
        and not resumed.get("ready")
        and not resumed.get("collected_slots")
    ):
        key = "resume.suspended_optional"
    return t(key, locale, name=skill.name, ask=ask)


async def save_turn(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """保存本轮用户消息、AI 回复、对话状态、任务行与决策日志。"""
    session = get_db_session_from_config(config)
    tenant_id = state["tenant_id"]
    session_id = state["session_id"]
    trace_id = state.get("trace_id")
    locale = state.get("locale")
    status = state.get("status")

    intent_dict = state.get("intent_result") or {}
    final_intent = intent_dict.get("final_intent") or intent_dict.get("pred_label")

    # 回复初稿：恢复挂起任务时追加续办提示（转人工触发可能再追加工单信息）
    reply = (state.get("reply") or "") + _resume_note(state)

    # 1. 保存用户消息（先于建单：工单上下文包能取到本轮消息）
    user_msg = await chat_message_repository.create(
        session,
        tenant_id=tenant_id,
        session_id=session_id,
        role="user",
        content=state.get("message", ""),
        intent=final_intent,
        status=status,
        slots_json=state.get("slots") or {},
        trace_id=trace_id,
    )

    # 指标：确认门结局（Stage 09）——本轮从 CONFIRMING 状态应答
    if not state.get("blocked") and state.get("current_state") == DialogStateValue.CONFIRMING:
        pred = intent_dict.get("pred_label")
        if pred == IntentLabel.META_CONFIRM:
            count_confirm_gate("confirmed")
        elif pred == IntentLabel.META_DENY:
            count_confirm_gate("denied")
        elif intent_dict.get("decision_source") == "LLM_CONFIRM_GATE":
            count_confirm_gate("modified")

    # —— Stage 15：人工接管期间的用户消息实时推给坐席（publish 内部 fail-open）——
    if status == TurnStatus.HANDOFF_SILENT:
        from app.services.notify_service import agents_channel, ws_hub

        ws_hub.publish_after_commit(
            session,
            agents_channel(tenant_id),
            {"type": "user_message", "session_id": session_id,
             "message_id": user_msg.id, "content": state.get("message", "")},
        )

    # —— Stage 15：CSAT 评分捕获轮（blocked 短路，单独落库 + 清标记）——
    if state.get("csat_capture"):
        from app.repositories.chat_csat_repository import chat_csat_repository

        cap = state["csat_capture"]
        await chat_csat_repository.create(
            session,
            tenant_id=tenant_id,
            session_id=session_id,
            user_id=state.get("user_id", ""),
            score=int(cap["score"]),
            trigger=cap.get("trigger", ""),
        )
        cleared = dict(state.get("context_stacks") or {})
        cleared.pop("csat_pending", None)
        # 只更新上下文栈与状态（任务字段不传即不动）
        await chat_dialog_state_repository.upsert_by_session_id(
            session, tenant_id, session_id,
            state=state.get("current_state") or "IDLE",
            context_stacks_json=cleared,
        )
    # CSAT 一次性失效补全（Stage 15 修复）：csat_pending 轮若被护栏拦截
    # （注入/辱骂/灌注，blocked 但非 csat_capture），下方状态机同步被跳过，
    # 会导致标记泄漏——此处显式清除，保证「询问后下一轮无论什么都失效」
    elif (
        state.get("blocked")
        and not state.get("handoff_silent")
        and (state.get("context_stacks") or {}).get("csat_pending")
    ):
        cleared = dict(state["context_stacks"])
        cleared.pop("csat_pending", None)
        await chat_dialog_state_repository.upsert_by_session_id(
            session, tenant_id, session_id,
            state=state.get("current_state") or "IDLE",
            context_stacks_json=cleared,
        )

    # 2. 任务行同步 + 对话状态机 upsert + 转人工触发收口（Stage 07）。
    # 护栏拦截/静默轮次不触碰状态机：不应改写进行中的任务状态
    new_state = state.get("new_state")
    handoff_info: dict[str, Any] | None = None
    if not state.get("blocked"):
        await _sync_task_rows(session, state)

        # UNKNOWN 连击计数（存上下文栈，随 dialog_state 持久化）
        context_stacks = dict(state.get("context_stacks") or {})
        if status == TurnStatus.FALLBACK and final_intent == IntentLabel.META_UNKNOWN:
            context_stacks["unknown_streak"] = int(context_stacks.get("unknown_streak", 0)) + 1
        else:
            context_stacks.pop("unknown_streak", None)
        # CSAT 询问一次性失效（Stage 15）：非评分回复照常处理并清标记
        context_stacks.pop("csat_pending", None)

        await chat_dialog_state_repository.upsert_by_session_id(
            session,
            tenant_id,
            session_id,
            state=new_state or state.get("current_state") or "IDLE",
            active_task_json=state.get("active_task"),
            task_stack_json=state.get("task_stack") or [],
            context_stacks_json=context_stacks,
        )

        # 转人工触发收口（幂等建单，可能追加回复文案——须在 AI 消息落库前完成）
        user_id = state.get("user_id", "")
        if new_state == DialogStateValue.HANDOFF:
            # 用户要求转人工：会话置 handoff（后续轮次 bot 静默）+ 建单
            await chat_session_repository.update_status(session, tenant_id, session_id, "handoff")
            ticket_id, created = await handoff_service.ensure_ticket(
                session, tenant_id=tenant_id, session_id=session_id, user_id=user_id,
                reason=REASON_USER_REQUEST, source_intent=final_intent,
            )
            handoff_info = {"reason": REASON_USER_REQUEST, "ticket_id": ticket_id, "created": created}
            if created:
                # 排队反馈（Stage 15）：告知当前 PENDING 队列位置
                position = await handoff_service.queue_position(session, tenant_id, ticket_id)
                queue_note = (
                    t("handoff.queue_note", locale, position=position) if position > 1 else ""
                )
                reply += t(
                    "handoff.ticket_suffix", locale, ticket=ticket_id[:8], queue=queue_note
                )
        elif final_intent == "PAYMENT.ISSUE":
            # 支付异常全转人工（skills_design 声明）：建单 + 会话置 handoff
            await chat_session_repository.update_status(session, tenant_id, session_id, "handoff")
            ticket_id, created = await handoff_service.ensure_ticket(
                session, tenant_id=tenant_id, session_id=session_id, user_id=user_id,
                reason=REASON_PAYMENT_ISSUE, source_intent=final_intent,
            )
            handoff_info = {"reason": REASON_PAYMENT_ISSUE, "ticket_id": ticket_id, "created": created}
        elif int(context_stacks.get("unknown_streak", 0)) >= settings.HANDOFF_UNKNOWN_STREAK:
            # 连续兜底：建单（PENDING 供坐席跟进）+ 回复附转人工询问；
            # 不置会话 handoff——用户仍可继续与 bot 交互，坐席可主动介入
            ticket_id, created = await handoff_service.ensure_ticket(
                session, tenant_id=tenant_id, session_id=session_id, user_id=user_id,
                reason=REASON_REPEATED_UNKNOWN, source_intent=final_intent,
            )
            handoff_info = {"reason": REASON_REPEATED_UNKNOWN, "ticket_id": ticket_id, "created": created}
            if created:
                reply += t("handoff.repeated_unknown", locale)

    # —— Stage 31 主动服务（默认关）：主任务闭环后至多追加一条活动提示。
    # 抑制矩阵/频控/拒绝冷却在 decide_proactive 内收口；影子模式只落日志
    # 不改回复；决策证据进 graph_trace_json.proactive（guardrail 同模式）——
    proactive = None
    if settings.PROACTIVE_ENABLED:
        from app.chat.proactive import decide_proactive
        from app.core.metrics import count_proactive

        proactive = await decide_proactive(state)
        if proactive:
            action = proactive.get("action")
            if proactive.get("applied") and action == "MENTION_CAMPAIGN" and proactive.get("hook"):
                # hook 是运营写死的确定性文案（含披露），不经 LLM 改写（红线）
                reply += t("proactive.campaign_mention", locale, hook=proactive["hook"])
                count_proactive(action, "applied")
            elif proactive.get("applied") and action == "START_ONBOARDING":
                # Stage 33：引导显式回复「注册」（确定性入口走规则层触发）
                reply += t("proactive.onboarding_mention", locale)
                count_proactive(action, "applied")
            elif action != "NONE":
                count_proactive(action or "NONE", "shadow")
            else:
                count_proactive("NONE", "suppressed")

    # 3. 保存 AI 回复（此时 reply 已定稿；RAG/工具回答附来源与引用）
    ai_metadata = None
    if state.get("answer_source"):
        retrieval = state.get("retrieval") or {}
        ai_metadata = {
            "answer_source": state["answer_source"],
            "citations": retrieval.get("citations", []),
        }
    ai_msg = await chat_message_repository.create(
        session,
        tenant_id=tenant_id,
        session_id=session_id,
        role="assistant",
        content=reply,
        intent=final_intent,
        status=status,
        trace_id=trace_id,
        metadata_json=ai_metadata,
    )

    # 计算本轮总耗时（毫秒）
    start_ts = state.get("start_ts")
    latency = {"total_ms": round((time.perf_counter() - start_ts) * 1000, 2)} if start_ts else {}

    # 4. 保存决策日志（message_id 关联用户消息；建单轮记录 reason 与 ticket_id）
    log_retrieval = dict(state.get("retrieval") or {})
    if handoff_info:
        log_retrieval["handoff"] = handoff_info
    log_state = {
        **state,
        "retrieval": log_retrieval or None,
        "user_message_id": user_msg.id,
        "latency": latency,
        "proactive": proactive,
    }
    await decision_logger.save(session, build_log_data(log_state))

    return {
        "reply": reply,
        "user_message_id": user_msg.id,
        "ai_message_id": ai_msg.id,
        "latency": latency,
        "graph_trace": ["save_turn"],
    }
