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

# Stage 32 选品顾问/对比（槽位齐全即 DONE 后进入本节点）
ADVISOR_INTENTS = frozenset(
    {IntentLabel.PRODUCT_RECOMMEND, IntentLabel.PRODUCT_COMPARE}
)


def _parse_budget_yuan(value: Any) -> int | None:
    """槽位里的预算转整数元；无法解析视为未给预算（不做硬约束）。"""
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _brief(p: ProductInfo) -> str:
    """单商品一行摘要（只引用结构化字段，无生成）。"""
    parts = [f"「{p.name}」{p.price_text}，{p.stock_text}"]
    if p.description:
        parts.append(p.description[:40].rstrip("。"))
    return "；".join(parts)


async def _recommend(
    session: Any, tenant_id: str, state: GraphState, slots: dict[str, Any]
) -> dict[str, Any]:
    """选品顾问（Stage 32）：硬约束过滤 + 价格升序 + trade-off，宁缺勿编。"""
    locale = state.get("locale")
    category = str(slots.get("category") or "")
    budget_yuan = _parse_budget_yuan(slots.get("budget"))
    budget_cents = budget_yuan * 100 if budget_yuan else None
    retrieval: dict[str, Any] = {
        "advisor": {"category": category, "budget_yuan": budget_yuan}
    }
    try:
        products = await product_provider.advise(
            session, tenant_id, category, budget_cents, limit=4
        )
    except Exception:  # noqa: BLE001 - 商品库故障如实说明，不伪造候选（红线）
        logger.exception("product advise failed")
        retrieval["degraded"] = True
        return {
            "reply": t("product.advise_unavailable", locale),
            "answer_source": "product_db",
            "retrieval": retrieval,
            "graph_trace": ["product_answer:recommend"],
        }
    retrieval["advisor"]["hits"] = [{"id": p.id, "name": p.name} for p in products]

    if not products:
        return {
            "reply": t(
                "product.advise_none", locale,
                category=category, budget=budget_yuan or "-",
            ),
            "answer_source": "product_db",
            "retrieval": retrieval,
            "graph_trace": ["product_answer:recommend"],
        }

    budget_part = f"预算 {budget_yuan} 元内、" if budget_yuan else ""
    lines = [f"根据您的需求（{budget_part}品类「{category}」），为您找到 {len(products)} 款有货商品："]
    lines += [f"{i}. {_brief(p)}" for i, p in enumerate(products, 1)]
    # trade-off（包 stage-32 要求）：候选≥2 给两端提示，只引用价格事实
    if len(products) >= 2 and products[0].price_cents is not None:
        lines.append(
            f"其中「{products[0].name}」价格最低；「{products[-1].name}」价格稍高，"
            "可按需权衡。"
        )
        lines.append(
            f"需要详细对比的话，直接说「对比 {products[0].name} 和 {products[-1].name}」即可。"
        )
    return {
        "reply": "\n".join(lines),
        "answer_source": "product_db",
        "retrieval": retrieval,
        "graph_trace": ["product_answer:recommend"],
    }


async def _compare(
    session: Any, tenant_id: str, state: GraphState, slots: dict[str, Any]
) -> dict[str, Any]:
    """商品对比（Stage 32）：两款逐个查库，结构化并列；找不齐如实说明。"""
    locale = state.get("locale")
    raw = str(slots.get("compare_items") or "")
    names = [n.strip() for n in raw.split("|") if n.strip()][:2]
    retrieval: dict[str, Any] = {"compare": {"items": names, "hits": []}}
    found: list[ProductInfo] = []
    missing: list[str] = []
    for name in names:
        try:
            hits = await product_provider.search(session, tenant_id, name, limit=1)
        except Exception:  # noqa: BLE001 - 故障如实说明
            logger.exception("product compare search failed")
            hits = []
            retrieval["degraded"] = True
        if hits:
            found.append(hits[0])
        else:
            missing.append(name)
    retrieval["compare"]["hits"] = [{"id": p.id, "name": p.name} for p in found]

    if len(names) < 2 or missing:
        return {
            "reply": t(
                "product.compare_missing", locale,
                names="、".join(f"「{n}」" for n in (missing or names)) or "商品",
            ),
            "answer_source": "product_db",
            "retrieval": retrieval,
            "graph_trace": ["product_answer:compare"],
        }

    lines = ["为您对比两款商品（信息来自商品库，以商品页为准）："]
    for p in found:
        row = [f"·「{p.name}」{p.price_text}，{p.stock_text}"]
        if p.attrs:
            attrs_text = "、".join(f"{k}:{v}" for k, v in list(p.attrs.items())[:4])
            row.append(f"规格：{attrs_text}")
        if p.description:
            row.append(p.description[:40].rstrip("。"))
        lines.append("；".join(row))
    return {
        "reply": "\n".join(lines),
        "answer_source": "product_db",
        "retrieval": retrieval,
        "graph_trace": ["product_answer:compare"],
    }


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

    # —— Stage 32 选品顾问/对比分支 ——
    if intent == IntentLabel.PRODUCT_RECOMMEND:
        return await _recommend(session, tenant_id, state, slots)
    if intent == IntentLabel.PRODUCT_COMPARE:
        return await _compare(session, tenant_id, state, slots)

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
