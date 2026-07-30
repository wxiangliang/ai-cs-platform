"""节点：slot_extract —— 槽位抽取（规则优先 + LLM 兜底）。

规则抽取先行；当本轮意图的必填槽位仍缺失且非纯槽位输入时，
用 LLM 补抽缺失槽位（Stage 04-01，规则结果优先、LLM 只补空缺；
未配置 API Key 时自动跳过，行为与 Stage 03 一致）。
"""

from typing import Any

from app.chat.graph.state import GraphState
from app.chat.intent.types import DecisionSource, IntentLabel
from app.chat.skills.registry import skill_registry
from app.chat.slots.extractor import slot_extractor
from app.chat.slots.llm_extractor import extract_missing_slots


async def slot_extract(state: GraphState) -> dict[str, Any]:
    """从归一化文本抽取槽位。被护栏拦截时跳过。"""
    if state.get("blocked"):
        return {"slots": {}, "graph_trace": ["slot_extract"]}

    # —— Stage 26 补槽守护命中：直接采信定向提取的键值，跳过全量抽取 ——
    # （防同轮其他数字串被通用正则误抽、污染任务槽位）
    intent_pre = state.get("intent_result") or {}
    if intent_pre.get("decision_source") == DecisionSource.RULE_PENDING_SLOT:
        fill = intent_pre.get("pending_fill") or {}
        if fill.get("slot"):
            return {
                "slots": {fill["slot"]: fill["value"]},
                "graph_trace": ["slot_extract"],
            }

    # 多意图拆分时只在主意图段内抽槽（slot_text），防止次要段槽位串入主任务
    text = state.get("slot_text") or state.get("normalized_text", "")
    slots = slot_extractor.extract(text)

    # —— LLM 兜底：必填槽位缺失时补抽（规则优先，只补空缺）——
    intent_dict = state.get("intent_result") or {}
    intent = intent_dict.get("pred_label", "")
    if intent and intent not in (IntentLabel.META_SLOT_ONLY, IntentLabel.META_UNKNOWN):
        # 已收集槽位（含任务历史）也算就绪，不重复抽取
        task_slots = (state.get("active_task") or {}).get("collected_slots", {})
        skill = skill_registry.get(intent)
        missing = [
            s for s in skill.required_slots if not slots.get(s) and not task_slots.get(s)
        ]
        if missing:
            llm_slots = await extract_missing_slots(text, intent, missing)
            # 规则结果优先：LLM 只填规则没抽到的键
            for key, value in llm_slots.items():
                slots.setdefault(key, value)

    return {
        "slots": slots,
        "graph_trace": ["slot_extract"],
    }
