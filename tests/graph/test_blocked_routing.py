"""第四批演进回归：blocked 条件边 + 实验轮次语义缓存绕过。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.chat.graph.builder import (
    _route_after_guardrail,
    _route_after_load,
    build_chat_graph,
)


def test_blocked_routes_short_circuit():
    """短路/拦截轮次直达回复生成，正常轮次走原线性段。"""
    assert _route_after_load({"blocked": True}) == "response_generate"
    assert _route_after_load({}) == "preprocess_message"
    assert _route_after_guardrail({"blocked": True}) == "response_generate"
    assert _route_after_guardrail({"blocked": False}) == "intent_classify"


def test_graph_compiles_with_conditional_edges():
    """加条件边后图仍可编译（结构完整性）。"""
    assert build_chat_graph() is not None


async def test_experiment_turn_bypasses_semantic_cache(monkeypatch):
    """A/B 实验轮次绕过语义缓存（查/写都不做），防变体互相污染。"""
    from app.chat.graph.nodes import rag_answer as node_mod
    from app.kb.answerer import RagAnswer
    from app.kb.retriever import RetrievalTrace

    calls = {"lookup": 0, "store": 0}

    class _SpyCache:
        async def lookup(self, *args, **kwargs):
            calls["lookup"] += 1
            return None

        async def store(self, *args, **kwargs):
            calls["store"] += 1

    monkeypatch.setattr(node_mod, "get_semantic_cache", lambda: _SpyCache())

    trace = RetrievalTrace(query="q", backend="fake")
    answer = RagAnswer(reply="回答", source="faq", citations=[], trace=trace)
    monkeypatch.setattr(
        node_mod,
        "rag_answerer",
        SimpleNamespace(answer=AsyncMock(return_value=(answer, trace))),
    )
    state = {
        "tenant_id": "t1",
        "session_id": "s1",
        "normalized_text": "退货政策",
        "intent_result": {"pred_label": "FAQ.GENERAL"},
        "experiment": {"exp1": "variant_b"},
    }
    config = {"configurable": {"db_session": None}}

    result = await node_mod.rag_answer(state, config)
    assert result["reply"] == "回答"
    assert calls == {"lookup": 0, "store": 0}  # 实验轮次完全绕过

    # 对照：无实验轮次正常查缓存并写入
    state["experiment"] = None
    await node_mod.rag_answer(state, config)
    assert calls == {"lookup": 1, "store": 1}
