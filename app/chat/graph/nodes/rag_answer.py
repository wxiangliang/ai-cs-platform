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
from app.core.config import settings
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

    # 含糊查询改写（WeKnora 对齐：先重写再检索）：指代/省略式查询结合近期
    # 对话改写成独立检索查询（「刚才那个多少钱」→带上具体商品）。
    # 清晰查询零调用；无 Key 自动失效
    retrieve_query = query
    rewritten: str | None = None
    if settings.RAG_QUERY_REWRITE_ENABLED:
        from app.kb.query_rewrite import rewrite_vague_query

        rewritten = await rewrite_vague_query(query, state.get("memory"))
        if rewritten:
            retrieve_query = rewritten

    # embedding 去重（延迟修复）：查询向量在此算一次，缓存查询/FAQ 层/文档层/
    # 缓存写入全程复用（原实现一轮最多 4 次 embedding 网络调用）。
    # 失败置 None：各消费方按原逻辑自行计算/降级，异常路径不变
    query_vec: list[float] | None = None
    try:
        import time

        from app.core.metrics import observe_kb_stage
        from app.kb.embedding import embedding_client
        from app.kb.query_normalize import normalize_query

        t0 = time.perf_counter()
        query_vec = (await embedding_client.embed([normalize_query(retrieve_query).text]))[0]
        observe_kb_stage("embed", time.perf_counter() - t0)
    except Exception:  # noqa: BLE001 - embedding 故障由下游各层自行降级
        logger.warning("query embedding failed, downstream will retry/degrade", exc_info=True)

    # 语义缓存（Stage 17）：同义问命中历史缓存直答，省一次检索/LLM；
    # 故障 fail-open（lookup 内部兜底返回 None），默认关闭时零开销。
    # 绕过条件：A/B 实验轮次（Stage 18 污染修复——变体参数下命中/写入共享缓存
    # 会让变体互相污染）；改写轮次（改写结果依赖对话上下文，与个性化同理，
    # 同一句「刚才那个多少钱」对不同用户指向不同商品，进共享缓存必错答）
    cache_bypassed = bool(state.get("experiment")) or rewritten is not None
    cache = get_semantic_cache()
    cached = None if cache_bypassed else await cache.lookup(tenant_id, query, emb=query_vec)
    if cached is not None:
        return {
            "reply": cached["reply"],
            "answer_source": cached["source"],
            "retrieval": {"cache_hit": True, "citations": cached["citations"]},
            "graph_trace": ["rag_answer:cache"],
        }

    trace_dict: dict[str, Any] = {"query": retrieve_query, "refused": True}
    degraded = False
    try:
        answer, trace = await rag_answerer.answer(
            session, tenant_id, retrieve_query, memory=state.get("memory"), query_vec=query_vec
        )
        trace_dict = trace.to_dict()
    except Exception:  # noqa: BLE001 - 知识库故障不打断主链路
        logger.exception("rag answer failed, fallback to template")
        answer = None
        degraded = True
        trace_dict["degraded"] = True
    if rewritten:
        # 改写留痕：排查「检索的查询」与「用户原话」的对应关系
        trace_dict["rewritten_from"] = query

    # 指标：检索结局四分类（faq 命中 / rag 回答 / 拒答 / 降级）
    if degraded:
        count_rag("degraded")
    elif answer is None:
        count_rag("refused")
    else:
        count_rag("faq_hit" if "faq" in (answer.source or "") else "rag_answer")

    if answer is not None:
        # 写语义缓存（Stage 17）：仅无副作用来源（faq/rag_*）且非个性化回答
        # （注入过用户记忆的生成结果不进租户共享缓存——防跨用户泄漏）、
        # 且非实验/改写轮次（变体参数或上下文相关的结果不进共享缓存）；
        # store 内部再校验白名单与 personalized 红线
        if is_cacheable_source(answer.source) and not answer.personalized and not cache_bypassed:
            await cache.store(
                tenant_id,
                query,
                {
                    "reply": answer.reply,
                    "source": answer.source,
                    "citations": answer.citations,
                },
                emb=query_vec,
            )
        return {
            "reply": answer.reply,
            "answer_source": answer.source,
            "retrieval": {**trace_dict, "citations": answer.citations},
            "graph_trace": ["rag_answer"],
        }

    # 智能澄清（Stage 21）：UNKNOWN 兜底检索也拒答时，用 top_k 候选 + 近期
    # 对话生成针对性澄清问句；失败降级下方固定模板。
    # answer_source 维持 "refused"（count_rag 指标口径不变），trace 标 clarify
    if final_intent == IntentLabel.META_UNKNOWN:
        from app.chat.skills.llm_clarifier import generate_clarify_question

        question = await generate_clarify_question(
            query, intent_dict.get("top_k") or [], state.get("memory"), state.get("locale")
        )
        if question:
            trace_dict["clarify"] = True
            return {
                "reply": question,
                "answer_source": "refused",
                "retrieval": trace_dict,
                "graph_trace": ["rag_answer:clarify"],
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
