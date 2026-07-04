"""节点：skill_resolve —— 根据最终意图选择 Skill。"""

from typing import Any

from app.chat.graph.state import GraphState
from app.chat.skills.registry import skill_registry


async def skill_resolve(state: GraphState) -> dict[str, Any]:
    """按最终生效意图解析 Skill，写入 selected_skill。被护栏拦截时跳过。"""
    if state.get("blocked"):
        return {"graph_trace": ["skill_resolve"]}

    intent_dict = state.get("intent_result") or {}
    # 优先使用状态机回写的 final_intent（续接任务时与原意图一致）
    final_intent = intent_dict.get("final_intent") or intent_dict.get("pred_label", "")
    skill = skill_registry.get(final_intent)

    return {
        "selected_skill": skill.skill_id,
        "graph_trace": ["skill_resolve"],
    }
