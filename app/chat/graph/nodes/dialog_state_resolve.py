"""节点：dialog_state_resolve —— 状态机流转。

调用 DialogStateManager 计算新状态、本轮处理状态、当前任务与缺失槽位。
"""

from typing import Any

from app.chat.graph.state import GraphState
from app.chat.intent.types import IntentResult
from app.chat.state.manager import SWITCH_SIGNAL_RE, dialog_state_manager


async def dialog_state_resolve(state: GraphState) -> dict[str, Any]:
    """根据意图与槽位计算状态机流转结果。被护栏拦截时跳过。"""
    if state.get("blocked"):
        return {"graph_trace": ["dialog_state_resolve"]}

    intent_dict = state.get("intent_result") or {}
    intent_result = IntentResult(
        pred_label=intent_dict.get("pred_label", ""),
        confidence=intent_dict.get("confidence", 0.0),
        decision_source=intent_dict.get("decision_source", ""),
        top_k=intent_dict.get("top_k", []),
        # margin 参与切换守护判定（Stage 26），无值（规则来源）为 None
        margin=intent_dict.get("margin"),
    )

    result = dialog_state_manager.resolve(
        current_state=state.get("current_state", "IDLE"),
        active_task=state.get("active_task"),
        intent_result=intent_result,
        slots=state.get("slots") or {},
        task_stack=state.get("task_stack"),
        pending_intents=state.get("pending_intents"),
        # 显式切换信号（Stage 26）：状态机是纯决策不碰文本，信号在节点层判定
        explicit_switch=bool(SWITCH_SIGNAL_RE.search(state.get("normalized_text", ""))),
    )

    # —— Stage 27 Meta-classifier 影子预测：只观察不决策 ——
    # 输入是 resolve 前的任务上下文（state）+ 流转结局（对照口径），
    # 结果落 state 供决策日志；部署域外/未启用/产物缺失返回 None
    from app.chat.intent.meta_shadow import shadow_predict

    meta_shadow = shadow_predict(
        dict(state),
        {
            "switch_candidate": result.switch_candidate,
            "unknown_with_task": result.unknown_with_task,
            "active_task": result.active_task,
        },
    )

    return {
        "new_state": result.new_state,
        "status": result.status,
        "active_task": result.active_task,
        "missing_slot": result.missing_slot,
        # —— Stage 27：影子预测（graph_trace_json.meta_shadow 落库）——
        "meta_shadow": meta_shadow,
        # —— Stage 05：任务栈 / 执行交接 / 恢复提示 ——
        "task_stack": result.task_stack,
        "finished_task": result.finished_task,
        "resumed_task": result.resumed_task,
        # —— Stage 10：追问超限放弃 ——
        "task_gave_up": result.gave_up,
        # —— Stage 23：任务中途否定（回复渲染重定向话术）——
        "denied_task": result.denied_task,
        # —— Stage 26：切换守护拦截 / UNKNOWN 无证据保持（回复渲染二选一澄清）——
        "switch_candidate": result.switch_candidate,
        "unknown_with_task": result.unknown_with_task,
        # 最终生效的累计槽位：任务结束（如确认门通过）后 active_task 已清空，
        # 回复模板（如受理回执中的订单号）必须从这里取
        "effective_slots": result.collected_slots or state.get("slots") or {},
        # 续接任务时最终生效意图可能变为原任务意图，回写供 skill_resolve / 回复使用
        "intent_result": {**intent_dict, "final_intent": result.intent},
        "graph_trace": ["dialog_state_resolve"],
    }
