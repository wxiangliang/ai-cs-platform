"""节点：intent_classify —— 意图识别。

通过工厂按配置获取 IntentClassifier 实现（INTENT_CLASSIFIER=rule|hybrid），
节点不依赖具体分类器。
分类是上下文敏感的：需要当前状态机状态与是否有进行中任务
（确认门应答 META.CONFIRM/DENY 仅在 CONFIRMING 状态判定）。
"""

from typing import Any

from app.chat.graph.state import GraphState
from app.chat.intent.factory import get_intent_classifier
from app.chat.intent.multi_intent import detect_multi_intent
from app.chat.intent.types import DecisionSource, IntentResult
from app.chat.state.types import DialogStateValue
from app.core.config import settings
from app.core.metrics import count_intent


async def intent_classify(state: GraphState) -> dict[str, Any]:
    """对归一化文本做意图识别（含多意图拆分）。被护栏拦截时跳过。"""
    if state.get("blocked"):
        return {"graph_trace": ["intent_classify"]}

    text = state.get("normalized_text", "")
    current_state = state.get("current_state", DialogStateValue.IDLE)
    active_task = state.get("active_task") or {}
    has_task = bool(active_task)
    classifier = get_intent_classifier()

    # —— Stage 41 主动建议接受通道：上轮真实展示过建议（窗口键在）且本轮是
    # 纯接受短句 → 按 accept_intent 开普通任务。窗口读取需异步 Redis，且纯接受
    # 短句必然不含多意图并列标记/不会进语义层，收口在节点侧与规则控制层等价。
    # 顺序红线（需求 3.2）由状态门控保证：CONFIRMING（确认门应答优先）与
    # COLLECTING（任务否定/补槽守护优先）一律不判——窗口只可能在 DONE 轮后
    # 写入，无任务空闲态才轮到营销应答语义。随 PROACTIVE_APPLY 联动
    # （无真实展示就没有接受语境）——
    if (
        settings.PROACTIVE_ENABLED
        and settings.PROACTIVE_APPLY
        and not has_task
        and current_state not in (DialogStateValue.COLLECTING, DialogStateValue.CONFIRMING)
    ):
        from app.chat.proactive.accept import pop_offer_accept

        offer = await pop_offer_accept(state["tenant_id"], state["session_id"], text)
        if offer is not None:
            from app.core.metrics import count_proactive

            count_proactive(offer["action"] or "NONE", "accepted")
            result = IntentResult(
                pred_label=offer["accept_intent"],
                confidence=0.9,
                decision_source=DecisionSource.RULE_PROACTIVE_ACCEPT,
                proactive_accept=offer,
            )
            count_intent(result.pred_label, result.decision_source)
            return {"intent_result": result.to_dict(), "graph_trace": ["intent_classify"]}

    # —— Stage 26 补槽守护：COLLECTING 下任务在等的第一个缺失槽位随分类传入，
    # 「订单号是12345678」这类回答式消息在控制层直接续接，不进语义层 ——
    pending_slot: str | None = None
    collected_slots: dict[str, Any] = {}
    if has_task and current_state == DialogStateValue.COLLECTING:
        collected_slots = active_task.get("collected_slots") or {}
        for slot_name in active_task.get("required_slots", []):
            if not collected_slots.get(slot_name):
                pending_slot = slot_name
                break

    # —— 多意图（Stage 10）：拆分成功时主意图走本轮，次要意图入 pending ——
    multi = await detect_multi_intent(
        text,
        classifier,
        current_state=current_state,
        has_active_task=has_task,
        pending_slot=pending_slot,
        collected_slots=collected_slots,
    )
    if multi is not None:
        count_intent(multi.primary.pred_label, multi.primary.decision_source)
        return {
            "intent_result": {**multi.primary.to_dict(), "multi_intent": True},
            # 主意图段文本：槽位抽取只在该段进行，防止次要段的槽位串入主任务
            "slot_text": multi.primary_text,
            "pending_intents": multi.pending,
            "graph_trace": ["intent_classify"],
        }

    result = await classifier.classify(
        text,
        current_state=current_state,
        has_active_task=has_task,
        pending_slot=pending_slot,
        collected_slots=collected_slots,
    )
    count_intent(result.pred_label, result.decision_source)
    return {
        "intent_result": result.to_dict(),
        "graph_trace": ["intent_classify"],
    }
