"""节点：product_answer —— 商品意图的事实回答（检索路由 R3）。

进入条件（builder 条件路由）：PRODUCT.ASK_PRICE / ASK_STOCK / ASK_INFO
且商品槽位齐全（状态机判 DONE）。

规则（stage-06-03 需求第 2 节）：
1. 商品库结构化查询（唯一事实源）：
   - 唯一命中 → 价格/库存/规格用商品库字段回答；ASK_INFO 叠加商品知识 RAG（附引用）；
   - 多命中 → 列候选让用户选（最多 3 个），不猜；
   - 无命中 → ASK_INFO 走知识库 RAG 兜底；PRICE/STOCK 回「没找到」模板（宁缺勿编）。
2. 红线：价格/库存禁止用 RAG 片段回答（文档会过期，价格错误是资损级事故）。
3. 商品库/知识库故障不打断主链路，降级原模板回复。
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.chat.graph.state import GraphState, get_db_session_from_config
from app.chat.intent.types import IntentLabel
from app.chat.skills.registry import skill_registry
from app.chat.skills.responder import render_reply
from app.core.i18n import t
from app.core.logging import get_logger
from app.kb.answerer import rag_answerer
from app.product.provider import ProductInfo, product_provider

logger = get_logger(__name__)

# 本节点服务的意图（builder 路由条件与此保持一致）
PRODUCT_FACT_INTENTS = frozenset(
    {
        IntentLabel.PRODUCT_ASK_PRICE,
        IntentLabel.PRODUCT_ASK_STOCK,
        IntentLabel.PRODUCT_ASK_INFO,
    }
)


def _format_fact_reply(intent: str, p: ProductInfo) -> str:
    """用商品库字段组装事实回复（价格/库存来源唯一，不经生成）。"""
    if intent == IntentLabel.PRODUCT_ASK_PRICE:
        return f"「{p.name}」目前价格为 {p.price_text}。"
    if intent == IntentLabel.PRODUCT_ASK_STOCK:
        return f"「{p.name}」库存情况：{p.stock_text}。"
    # ASK_INFO：名称 + 分类 + 规格 + 简介
    parts = [f"「{p.name}」"]
    if p.category:
        parts.append(f"分类：{p.category}")
    if p.attrs:
        attrs_text = "、".join(f"{k}:{v}" for k, v in list(p.attrs.items())[:6])
        parts.append(f"规格：{attrs_text}")
    if p.description:
        parts.append(p.description[:200].rstrip("。"))
    return "；".join(parts) + "。"


async def product_answer(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """商品库优先的事实回答；无命中按意图降级 RAG 或模板。"""
    session = get_db_session_from_config(config)
    tenant_id = state["tenant_id"]

    intent_dict = state.get("intent_result") or {}
    intent = intent_dict.get("final_intent") or intent_dict.get("pred_label", "")
    slots = state.get("effective_slots") or state.get("slots") or {}
    product_query = str(slots.get("product_name") or slots.get("product_id") or "")

    retrieval: dict[str, Any] = {"query": product_query, "product_hits": []}
    try:
        products = await product_provider.search(session, tenant_id, product_query, limit=3)
    except Exception:  # noqa: BLE001 - 商品库故障降级模板，不打断主链路
        logger.exception("product search failed, fallback to template")
        products = []
        retrieval["degraded"] = True

    retrieval["product_hits"] = [
        {"id": p.id, "name": p.name} for p in products
    ]

    # —— 唯一命中：事实回答 ——
    if len(products) == 1:
        reply = _format_fact_reply(intent, products[0])
        # ASK_INFO 叠加商品知识 RAG（描述类长文），命中则追加引用内容
        if intent == IntentLabel.PRODUCT_ASK_INFO:
            try:
                answer, trace = await rag_answerer.answer(
                    session, tenant_id,
                    f"{products[0].name} {state.get('normalized_text', '')}",
                    memory=state.get("memory"),
                )
                retrieval.update(trace.to_dict() | {"product_hits": retrieval["product_hits"]})
                if answer is not None and answer.source != "faq":
                    reply = f"{reply}\n{answer.reply}"
            except Exception:  # noqa: BLE001
                logger.exception("product rag enrich failed")
        return {
            "reply": reply,
            "answer_source": "product_db",
            "retrieval": retrieval,
            "graph_trace": ["product_answer"],
        }

    # —— 多命中：列候选，不猜 ——
    if len(products) > 1:
        names = "、".join(f"「{p.name}」" for p in products)
        return {
            "reply": t("product.multi", state.get("locale"), names=names),
            "answer_source": "product_db",
            "retrieval": retrieval,
            "graph_trace": ["product_answer"],
        }

    # —— 无命中 ——
    if intent == IntentLabel.PRODUCT_ASK_INFO:
        # 信息类允许知识库 RAG 兜底（描述性内容）
        try:
            answer, trace = await rag_answerer.answer(
                session, tenant_id, state.get("normalized_text", ""), memory=state.get("memory")
            )
            retrieval.update(trace.to_dict() | {"product_hits": []})
            if answer is not None:
                return {
                    "reply": answer.reply,
                    "answer_source": answer.source,
                    "retrieval": retrieval,
                    "graph_trace": ["product_answer"],
                }
        except Exception:  # noqa: BLE001
            logger.exception("product rag fallback failed")
    # 价格/库存无命中：宁缺勿编，不走 RAG（红线）
    if intent in (IntentLabel.PRODUCT_ASK_PRICE, IntentLabel.PRODUCT_ASK_STOCK) and product_query:
        return {
            "reply": t("product.not_found", state.get("locale"), query=product_query),
            "answer_source": "product_db",
            "retrieval": retrieval,
            "graph_trace": ["product_answer"],
        }

    # 最终降级：原 Skill 模板
    skill = skill_registry.get(intent)
    reply = render_reply(state.get("status", ""), skill, slots)
    return {
        "reply": reply,
        "answer_source": "template",
        "retrieval": retrieval,
        "graph_trace": ["product_answer"],
    }
