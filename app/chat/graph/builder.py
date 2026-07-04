"""LangGraph 聊天主链路构建器。

主链路（skill_resolve 后为条件路由；检索路由矩阵见 stage-06-03，执行闭环见 stage-05）：

START → load_session_state → preprocess_message → guardrail_check
      → intent_classify → slot_extract → confirmation_parse → dialog_state_resolve
      → skill_resolve ─┬→ response_generate ─┐
                       ├→ rag_answer ────────┤
                       ├→ product_answer ────┼→ save_turn → END
                       ├→ tool_invoke ───────┤
                       └→ action_execute ────┘

路由条件（其余走模板回复 response_generate）：
- action_execute（Stage 05）：确认门通过（status=CONFIRMED 且 finished_task 存在）
  → ActionExecutor 执行写工具（唯一写入口）；
- tool_invoke（Stage 05）：订单/物流查询类意图且槽位齐全 → mock 工具真实数据回答；
- product_answer（R3）：商品信息类意图且槽位齐全 → 商品库优先；
- rag_answer（R1/R2）：FAQ.GENERAL；或 META.UNKNOWN 兜底且无进行中任务；
- R5 约束：补槽/确认门轮次（NEEDS_SLOT/NEEDS_CONFIRM）不触发任何检索与工具。

图编译一次后复用（compile 成本不低，避免每次请求重建）。
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.chat.graph.nodes.action_execute import action_execute
from app.chat.graph.nodes.confirmation_parse import confirmation_parse
from app.chat.graph.nodes.dialog_state_resolve import dialog_state_resolve
from app.chat.graph.nodes.guardrail_check import guardrail_check
from app.chat.graph.nodes.intent_classify import intent_classify
from app.chat.graph.nodes.load_session_state import load_session_state
from app.chat.graph.nodes.preprocess_message import preprocess_message
from app.chat.graph.nodes.product_answer import PRODUCT_FACT_INTENTS, product_answer
from app.chat.graph.nodes.rag_answer import rag_answer
from app.chat.graph.nodes.response_generate import response_generate
from app.chat.graph.nodes.save_turn import save_turn
from app.chat.graph.nodes.skill_resolve import skill_resolve
from app.chat.graph.nodes.slot_extract import slot_extract
from app.chat.graph.nodes.tool_invoke import TOOL_QUERY_INTENTS, tool_invoke
from app.chat.graph.state import GraphState
from app.chat.intent.types import IntentLabel
from app.chat.state.types import TurnStatus
from app.core.config import settings

# skill_resolve 之前的线性段
_LINEAR_SEQUENCE = [
    ("load_session_state", load_session_state),
    ("preprocess_message", preprocess_message),
    ("guardrail_check", guardrail_check),
    ("intent_classify", intent_classify),
    ("slot_extract", slot_extract),
    ("confirmation_parse", confirmation_parse),
    ("dialog_state_resolve", dialog_state_resolve),
    ("skill_resolve", skill_resolve),
]

# 回复分支节点：全部汇入 save_turn
_REPLY_NODES = {
    "response_generate": response_generate,
    "rag_answer": rag_answer,
    "product_answer": product_answer,
    "tool_invoke": tool_invoke,
    "action_execute": action_execute,
}


def _route_after_skill(state: GraphState) -> str:
    """skill_resolve 之后的路由（检索路由矩阵 R1-R5 + Stage 05 执行/工具分支）。

    R5 约束由此保证：补槽（NEEDS_SLOT）/确认门（NEEDS_CONFIRM）轮次
    status 不为 DONE/CONFIRMED/FALLBACK，永远落到 response_generate。
    """
    if state.get("blocked"):
        return "response_generate"

    intent_dict = state.get("intent_result") or {}
    final_intent = intent_dict.get("final_intent") or intent_dict.get("pred_label", "")
    status = state.get("status", "")

    # Stage 05：确认门通过 → 写操作执行（唯一写入口）
    if status == TurnStatus.CONFIRMED and state.get("finished_task"):
        return "action_execute"

    # Stage 05：订单/物流查询类意图且槽位齐全 → 工具查询
    if final_intent in TOOL_QUERY_INTENTS and status == TurnStatus.DONE:
        return "tool_invoke"

    # R3：商品信息类意图且槽位齐全（本轮可产出答案）→ 商品库优先
    if final_intent in PRODUCT_FACT_INTENTS and status == TurnStatus.DONE:
        return "product_answer"

    if not settings.KB_ENABLED:
        return "response_generate"
    # R1：平台政策/规则问答
    if final_intent == IntentLabel.FAQ_GENERAL:
        return "rag_answer"
    # R2：UNKNOWN 兜底且没有进行中任务：先过一层知识库（长尾问题第一道网）
    if final_intent == IntentLabel.META_UNKNOWN and not state.get("active_task"):
        return "rag_answer"
    return "response_generate"


def build_chat_graph():
    """构建并编译聊天主链路图。"""
    graph = StateGraph(GraphState)

    for name, fn in _LINEAR_SEQUENCE:
        graph.add_node(name, fn)
    for name, fn in _REPLY_NODES.items():
        graph.add_node(name, fn)
    graph.add_node("save_turn", save_turn)

    # 线性段
    graph.add_edge(START, _LINEAR_SEQUENCE[0][0])
    for (prev_name, _), (next_name, _) in zip(_LINEAR_SEQUENCE, _LINEAR_SEQUENCE[1:]):
        graph.add_edge(prev_name, next_name)

    # 条件路由：五个回复分支都汇入 save_turn
    graph.add_conditional_edges(
        "skill_resolve",
        _route_after_skill,
        {name: name for name in _REPLY_NODES},
    )
    for name in _REPLY_NODES:
        graph.add_edge(name, "save_turn")
    graph.add_edge("save_turn", END)

    return graph.compile()


@lru_cache(maxsize=1)
def get_chat_graph():
    """获取编译后的图（进程内单例，懒加载）。"""
    return build_chat_graph()
