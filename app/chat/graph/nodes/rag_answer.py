"""节点：rag_answer —— 知识库检索回答（Stage 06）。

进入条件（builder 的条件路由决定）：
- 最终意图为 FAQ.GENERAL（平台政策/规则类问答）；
- 或 META.UNKNOWN 兜底且无进行中任务（长尾问题先过一层知识库，命中则回答）。

红线：检索不到就拒答走澄清/核实话术，绝不编造；
知识库故障不允许打断主链路——任何异常都降级为模板回复。
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.chat.cache.semantic_cache import get_semantic_cache, is_cacheable_source
from app.chat.graph.state import GraphState, get_db_session_from_config
from app.chat.intent.types import IntentLabel
from app.chat.skills.registry import skill_registry
from app.chat.skills.responder import render_reply
from app.core.logging import get_logger
from app.core.metrics import count_rag
from app.kb.answerer import rag_answerer

logger = get_logger(__name__)


async def rag_answer(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """尝试用知识库回答；拒答或异常时降级为对应意图的模板回复。"""
    session = get_db_session_from_config(config)
    tenant_id = state["tenant_id"]
    query = state.get("normalized_text", "")

    intent_dict = state.get("intent_result") or {}
    final_intent = intent_dict.get("final_intent") or intent_dict.get("pred_label", "")

    # 语义缓存（Stage 17）：同义问命中历史缓存直答，省一次检索/LLM；
    # 故障 fail-open（lookup 内部兜底返回 None），默认关闭时零开销
    cache = get_semantic_cache()
    cached = await cache.lookup(tenant_id, query)
    if cached is not None:
        return {
            "reply": cached["reply"],
            "answer_source": cached["source"],
            "retrieval": {"cache_hit": True, "citations": cached["citations"]},
            "graph_trace": ["rag_answer:cache"],
        }

    trace_dict: dict[str, Any] = {"query": query, "refused": True}
    degraded = False
    try:
        answer, trace = await rag_answerer.answer(
            session, tenant_id, query, memory=state.get("memory")
        )
        trace_dict = trace.to_dict()
    except Exception:  # noqa: BLE001 - 知识库故障不打断主链路
        logger.exception("rag answer failed, fallback to template")
        answer = None
        degraded = True
        trace_dict["degraded"] = True

    # 指标：检索结局四分类（faq 命中 / rag 回答 / 拒答 / 降级）
    if degraded:
        count_rag("degraded")
    elif answer is None:
        count_rag("refused")
    else:
        count_rag("faq_hit" if "faq" in (answer.source or "") else "rag_answer")

    if answer is not None:
        # 写语义缓存（Stage 17）：仅无副作用来源（faq/rag_*）；store 内部再校验白名单
        if is_cacheable_source(answer.source):
            await cache.store(
                tenant_id,
                query,
                {
                    "reply": answer.reply,
                    "source": answer.source,
                    "citations": answer.citations,
                },
            )
        return {
            "reply": answer.reply,
            "answer_source": answer.source,
            "retrieval": {**trace_dict, "citations": answer.citations},
            "graph_trace": ["rag_answer"],
        }

    # 拒答/未命中：FAQ 意图给「需核实」话术，UNKNOWN 维持澄清话术。
    # 检索轨迹（含未达阈值的命中分数）仍完整落库，供排查与调阈值
    skill = skill_registry.get(final_intent or IntentLabel.META_UNKNOWN)
    reply = render_reply(
        state.get("status", ""), skill, state.get("effective_slots") or {}, state.get("locale")
    )
    return {
        "reply": reply,
        "answer_source": "refused",
        "retrieval": trace_dict,
        "graph_trace": ["rag_answer"],
    }
