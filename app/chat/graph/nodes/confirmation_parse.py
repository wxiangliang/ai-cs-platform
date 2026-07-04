"""节点：confirmation_parse —— 确认门含糊应答的 LLM 解析（Stage 05）。

位置：slot_extract 之后、dialog_state_resolve 之前。
只在「CONFIRMING 状态 + 分类结果不是确定性控制意图」时介入：
- CONFIRM/DENY → 覆写意图（decision_source=LLM_CONFIRM_GATE）；
- MODIFY → 槽位修正并入本轮 slots，意图改 SLOT_ONLY（状态机合并后重新进确认门）；
- UNRELATED / LLM 不可用 → 原样放行（业务意图会触发任务挂起，UNKNOWN 续接补槽）。
"""

from typing import Any

from app.chat.confirmation.parser import confirmation_parser
from app.chat.graph.state import GraphState
from app.chat.intent.types import DecisionSource, IntentLabel
from app.chat.skills.registry import skill_registry
from app.chat.state.types import DialogStateValue

# 这些意图已由规则/分类层确定性给出，无需 LLM 复核
_SETTLED_INTENTS = {
    IntentLabel.META_CONFIRM,
    IntentLabel.META_DENY,
    IntentLabel.META_ABORT,
    IntentLabel.META_TRANSFER_HUMAN,
    IntentLabel.META_SLOT_ONLY,
}

# 弱确认词（Stage 13）：语气附和类，对 L3 不可逆操作（退款/取消/改址）不足以放行——
# 「好的我知道了」不等于「确认执行」。命中时降级为重确认，要求明确动作词
_WEAK_CONFIRM_WORDS = frozenset({"嗯", "哦", "好", "好的", "好呀", "好啊", "可以", "行", "中", "ok", "yes"})


async def confirmation_parse(state: GraphState) -> dict[str, Any]:
    """确认门下的含糊应答解析。非 CONFIRMING 或无需解析时为透传节点。"""
    if state.get("blocked"):
        return {"graph_trace": ["confirmation_parse"]}
    if state.get("current_state") != DialogStateValue.CONFIRMING:
        return {"graph_trace": ["confirmation_parse"]}
    active_task = state.get("active_task")
    if not active_task:
        return {"graph_trace": ["confirmation_parse"]}

    intent_dict = state.get("intent_result") or {}

    # —— L3 高风险弱确认收紧（Stage 13）——
    # 规则确认门把「嗯/好的/ok」判为 META.CONFIRM，但对退款/取消/改址（L3）
    # 直接执行的资损代价高：降级为 SLOT_ONLY 重进确认门（重发确认话术），
    # 用户须回复含明确动作词的确认（「确认」「确定取消」等）才放行。L1/L2 不受影响
    if intent_dict.get("pred_label") == IntentLabel.META_CONFIRM:
        skill = skill_registry.get(active_task.get("intent", ""))
        text = state.get("normalized_text", "").strip().lower().rstrip("。！!~？?")
        if skill.risk_level == "L3" and text in _WEAK_CONFIRM_WORDS:
            return {
                "intent_result": {
                    **intent_dict,
                    "pred_label": IntentLabel.META_SLOT_ONLY,
                    "decision_source": "RULE_WEAK_CONFIRM_RECHECK",
                },
                "weak_confirm_recheck": True,
                "graph_trace": ["confirmation_parse"],
            }

    if intent_dict.get("pred_label") in _SETTLED_INTENTS:
        return {"graph_trace": ["confirmation_parse"]}

    outcome = await confirmation_parser.parse(state.get("normalized_text", ""), active_task)
    if outcome is None or outcome.verdict == "UNRELATED":
        # LLM 不可用/判定在说别的事：交回正常意图流程
        return {"graph_trace": ["confirmation_parse"]}

    if outcome.verdict == "CONFIRM":
        new_intent = IntentLabel.META_CONFIRM
        slots_update: dict[str, Any] = {}
    elif outcome.verdict == "DENY":
        new_intent = IntentLabel.META_DENY
        slots_update = {}
    else:  # MODIFY：修正值并入槽位，按纯槽位续接（状态机合并后重新确认）
        new_intent = IntentLabel.META_SLOT_ONLY
        slots_update = outcome.slot_updates

    result: dict[str, Any] = {
        "intent_result": {
            **intent_dict,
            "pred_label": new_intent,
            "confidence": 0.8,
            "decision_source": DecisionSource.LLM_CONFIRM_GATE,
        },
        "graph_trace": ["confirmation_parse"],
    }
    if slots_update:
        result["slots"] = {**(state.get("slots") or {}), **slots_update}
    return result
