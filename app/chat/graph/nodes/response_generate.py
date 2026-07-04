"""节点：response_generate —— 生成回复（模板底稿 + LLM 润色）。

面向用户文案走 i18n（Stage 19）：locale 来自 state（load_session_state 决策）；
LLM 润色路径的多语言由 prompt 的「按用户语言回复」指令处理，不查语言包。
"""

from typing import Any

from app.chat.graph.state import GraphState
from app.chat.skills.llm_responder import polish_reply
from app.chat.skills.registry import skill_registry
from app.chat.skills.responder import render_reply
from app.chat.state.types import TurnStatus
from app.core.i18n import t


async def response_generate(state: GraphState) -> dict[str, Any]:
    """生成回复文本。"""
    locale = state.get("locale")
    # 人工接管静默轮次（Stage 07）：固定等待话术，不进任何决策/生成
    if state.get("handoff_silent"):
        return {
            "reply": t("handoff.silent_waiting", locale),
            "graph_trace": ["response_generate"],
        }
    # CSAT 评分捕获轮次（Stage 15）：致谢话术（低分附致歉），落库在 save_turn
    if state.get("csat_capture"):
        score = int(state["csat_capture"].get("score", 0))
        key = "csat.thanks_low" if score <= 2 else "csat.thanks_high"
        return {"reply": t(key, locale), "graph_trace": ["response_generate"]}
    # 被护栏拦截：优先用护栏给出的场景话术（注入/违禁/灌注各有其词，Stage 14），
    # 否则回落通用安全回复
    if state.get("blocked"):
        if state.get("guardrail_reply"):
            return {"reply": state["guardrail_reply"], "graph_trace": ["response_generate"]}
        skill = skill_registry.get("")  # 兜底 skill
        reply = render_reply(TurnStatus.FAILED, skill, {}, locale)
        return {"reply": reply, "graph_trace": ["response_generate"]}

    intent_dict = state.get("intent_result") or {}
    final_intent = intent_dict.get("final_intent") or intent_dict.get("pred_label", "")
    skill = skill_registry.get(final_intent)

    # 槽位优先级：状态机给出的最终累计槽位（含任务历史收集、任务结束后仍可用）
    # > 当前任务槽位 > 本轮抽取槽位
    active_task = state.get("active_task") or {}
    collected = (
        state.get("effective_slots")
        or active_task.get("collected_slots")
        or state.get("slots")
        or {}
    )

    status = state.get("status", TurnStatus.FALLBACK)
    # 追问超限放弃任务：给明确的放弃+转人工建议话术（Stage 10 流转治理）
    if state.get("task_gave_up"):
        return {"reply": t("task.gave_up", locale), "graph_trace": ["response_generate"]}
    draft = render_reply(status, skill, collected, locale)
    # L3 弱确认降级（Stage 13）：明确告知需回复「确认」，避免「好的」被误当放行
    if state.get("weak_confirm_recheck"):
        return {
            "reply": t("confirm.weak_recheck_prefix", locale, draft=draft),
            "graph_trace": ["response_generate"],
        }
    # LLM 润色（仅 DONE/FALLBACK；事实保护与降级见 llm_responder；带记忆上下文；
    # 情绪标记轮次要求安抚语气，Stage 14）
    reply = await polish_reply(
        draft,
        status,
        state.get("normalized_text", ""),
        memory=state.get("memory"),
        soften=state.get("emotion") == "negative",
    )
    return {
        "reply": reply,
        "graph_trace": ["response_generate"],
    }
