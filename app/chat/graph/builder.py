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

blocked 条件边：load_session_state（接管静默/CSAT 捕获）与 guardrail_check
（护栏拦截）后各有一条到 response_generate 的短路边——blocked 轮次不再空跑
中间透传节点（graph_trace 相应变短，决策日志反映真实执行路径）。

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
from app.chat.graph.nodes.product_answer import (
    ADVISOR_INTENTS,
    PRODUCT_FACT_INTENTS,
    product_answer,
)
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


def _route_after_load(state: GraphState) -> str:
    """load_session_state 之后：上游短路轮次（人工接管静默 / CSAT 评分捕获）
    直达回复生成，不再空跑预处理到技能解析的 7 个透传节点。"""
    if state.get("blocked"):
        return "response_generate"
    return "preprocess_message"


def _route_after_guardrail(state: GraphState) -> str:
    """guardrail_check 之后：拦截轮次（注入/违禁/灌注）直达回复生成
    （护栏话术），跳过意图/槽位/确认门/状态机/技能 5 个透传节点。
    各节点内保留 blocked 防御检查（直接调用节点的测试路径仍成立）。"""
    if state.get("blocked"):
        return "response_generate"
    return "intent_classify"

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

    # R3：商品信息类意图且槽位齐全（本轮可产出答案）→ 商品库优先；
    # Stage 32：选品/对比同路（槽位齐→硬约束候选/结构化对比，补槽轮不进）
    if final_intent in (PRODUCT_FACT_INTENTS | ADVISOR_INTENTS) and status == TurnStatus.DONE:
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
    """构建并编译聊天主链路图。

    节点一律经写契约包装（post-stage-27 工程护栏）：越权写 GraphState
    字段在 dev/test 直接抛错、prod 只告警——契约表见 graph/contracts.py。
    """
    from app.chat.graph.contracts import enforce_write_contract

    graph = StateGraph(GraphState)

    for name, fn in _LINEAR_SEQUENCE:
        graph.add_node(name, enforce_write_contract(name, fn))
    for name, fn in _REPLY_NODES.items():
        graph.add_node(name, enforce_write_contract(name, fn))
    graph.add_node("save_turn", enforce_write_contract("save_turn", save_turn))

    # 线性段（blocked 条件边：短路/拦截轮次提前跳到回复生成，不空跑透传节点）
    graph.add_edge(START, "load_session_state")
    graph.add_conditional_edges(
        "load_session_state",
        _route_after_load,
        {"preprocess_message": "preprocess_message", "response_generate": "response_generate"},
    )
    graph.add_edge("preprocess_message", "guardrail_check")
    graph.add_conditional_edges(
        "guardrail_check",
        _route_after_guardrail,
        {"intent_classify": "intent_classify", "response_generate": "response_generate"},
    )
    for (prev_name, _), (next_name, _) in zip(
        _LINEAR_SEQUENCE[3:], _LINEAR_SEQUENCE[4:]
    ):
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
