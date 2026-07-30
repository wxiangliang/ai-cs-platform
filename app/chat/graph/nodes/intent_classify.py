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
from app.chat.state.types import DialogStateValue
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
