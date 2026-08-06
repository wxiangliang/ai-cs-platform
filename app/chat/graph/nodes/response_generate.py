"""节点：response_generate —— 生成回复（模板底稿 + LLM 润色）。

面向用户文案走 i18n（Stage 19）：locale 来自 state（load_session_state 决策）；
LLM 润色路径的多语言由 prompt 的「按用户语言回复」指令处理，不查语言包。
"""

from typing import Any

from app.chat.graph.state import GraphState
from app.chat.intent.types import DecisionSource, IntentLabel
from app.chat.skills.llm_responder import polish_reply
from app.core.config import settings
from app.chat.skills.registry import skill_registry
from app.chat.skills.responder import render_reply
from app.chat.skills.types import SkillKind
from app.chat.state.types import DialogStateValue, TurnStatus
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
    # 任务中途否定（Stage 23 方向纠偏）：重定向话术——确认停止误开的任务，
    # 引导用户直接说出真实诉求；有挂起任务时 save_turn 会追加续办提示
    denied = state.get("denied_task")
    if denied:
        from app.core.metrics import count_direction

        count_direction("task_denied")
        denied_name = skill_registry.get(denied.get("intent", "")).name
        return {
            "reply": t("task.denied_redirect", locale, name=denied_name),
            "graph_trace": ["response_generate:task_denied"],
        }
    # 智能澄清（Stage 21）：意图不明轮次用 top_k 候选 + 近期对话生成针对性
    # 澄清问句替代固定模板；失败/无 Key 走原模板（零回归）。
    # 不过 polish——问句本身已是 LLM 输出，二次润色浪费且可能改坏选项
    # Stage 40 行为准则：本轮命中块（未启用/无命中=None，下游零改动）
    from app.chat.guidelines import guidelines_for_state

    guide_block = guidelines_for_state(state)

    if status == TurnStatus.FALLBACK and final_intent == IntentLabel.META_UNKNOWN:
        from app.chat.skills.llm_clarifier import generate_clarify_question

        question = await generate_clarify_question(
            state.get("normalized_text", ""),
            intent_dict.get("top_k") or [],
            state.get("memory"),
            locale,
            mode_gate=intent_dict.get("mode_gate"),
            guidelines=guide_block,
        )
        if question:
            return {"reply": question, "graph_trace": ["response_generate:clarify"]}
    draft = render_reply(status, skill, collected, locale)

    # —— Stage 30 SOCIAL_HOLD：模式门直通的闲聊插话且有任务在补槽 ——
    # 状态机已保证任务/状态不动（CHITCHAT 类 DONE 语义），这里补续办提示：
    # 闲聊回复后带一句「刚才的 XX 还在办理中」，不让社交插话中断补槽节奏。
    # 确定性策略不进润色（Stage 30 需求第 5 节）
    if (
        intent_dict.get("decision_source") == DecisionSource.MODE_SOCIAL
        and active_task
        and state.get("current_state") == DialogStateValue.COLLECTING
    ):
        task_name = skill_registry.get(active_task.get("intent", "")).name
        return {
            "reply": draft + t("mode.social_resume", locale, name=task_name),
            "graph_trace": ["response_generate:social_hold"],
        }

    # —— Stage 26 切换守护拦截：任务进行中新意图证据不足，回复二选一澄清
    # （当前任务的追问/确认话术 + 候选新意图的明确引导），不进润色保持确定性 ——
    switch_candidate = state.get("switch_candidate")
    if switch_candidate:
        from app.core.metrics import count_direction

        count_direction("switch_guard")
        new_name = skill_registry.get(switch_candidate).name
        key = (
            "intent.switch_clarify_confirming"
            if status == TurnStatus.NEEDS_CONFIRM
            else "intent.switch_clarify_collecting"
        )
        return {
            "reply": t(key, locale, name=skill.name, new_name=new_name, question=draft),
            "graph_trace": ["response_generate:switch_guard"],
        }
    # —— Stage 26 UNKNOWN 无证据保持：不原话重问吞掉用户诉求，
    # 回复「继续补充还是想办别的」二选一澄清 ——
    if state.get("unknown_with_task") and status == TurnStatus.NEEDS_SLOT:
        from app.core.metrics import count_direction

        count_direction("unknown_hold")
        return {
            "reply": t("intent.unknown_with_task", locale, name=skill.name, question=draft),
            "graph_trace": ["response_generate:unknown_hold"],
        }

    # 中置信软确认（Stage 23 方向纠偏，Stage 26 修订）：**新开**任务
    # （无 task_id 且非恢复）追问话术前复述意图——不阻塞流程不加轮次，
    # 用户扫一眼即可发现方向错了（配合任务中途否定通道退出）。触发条件：
    # - LLM 二判来源：难例本身，一律复述（修复固定置信 0.7 导致的死条件）；
    # - SETFIT_LOW_MARGIN：top1/top2 分差小且二判不可用，天然模糊；
    # - SETFIT：置信低于软确认线——写意图单列更高线（修复采纳线==软确认线
    #   导致写意图永不软确认的死区）
    source = intent_dict.get("decision_source")
    soft_line = (
        settings.INTENT_SOFT_CONFIRM_THRESHOLD_WRITE
        if skill.kind == SkillKind.WRITE
        else settings.INTENT_SOFT_CONFIRM_THRESHOLD
    )
    if (
        status == TurnStatus.NEEDS_SLOT
        and active_task
        and not active_task.get("task_id")
        and not state.get("resumed_task")
        and (
            source in (DecisionSource.LLM, DecisionSource.SETFIT_LOW_MARGIN)
            or (
                # KNN 确认来源按 SETFIT 同规则：近邻同意解决的是 margin 疑虑，
                # 置信度本身仍低时新开任务照样复述纠偏
                source in (DecisionSource.SETFIT, DecisionSource.SETFIT_KNN_CONFIRMED)
                and float(intent_dict.get("confidence", 1.0)) < soft_line
            )
        )
    ):
        from app.core.metrics import count_direction

        count_direction("soft_confirm")
        draft = t("intent.soft_confirm", locale, name=skill.name) + draft
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
        guidelines=guide_block,
    )
    return {
        "reply": reply,
        "graph_trace": ["response_generate"],
    }
