"""Stage 21 智能澄清测试（fake LLM，不依赖真实模型）。

覆盖：触发条件收窄 / 输出治理与护栏 / 无 Key 零回归降级 /
两个接入出口（response_generate 与 rag_answer 拒答回落）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.chat.skills.llm_clarifier import _business_candidates, generate_clarify_question
from app.core.config import settings

_TOPK_BUSINESS = [
    {"label": "AFTERSALE.REFUND", "score": 0.31},
    {"label": "AFTERSALE.RETURN", "score": 0.27},
    {"label": "META.UNKNOWN", "score": 0.1},
]
_MEMORY = {"recent_turns": [["user", "我买的 AP-300 有问题"], ["assistant", "抱歉，请问需要什么帮助"]]}


def _fake_llm(monkeypatch, reply):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake")
    mock = AsyncMock(return_value=reply)
    monkeypatch.setattr("app.chat.skills.llm_clarifier.chat_completion", mock)
    return mock


# ---------------- 触发条件 ----------------


def test_business_candidates_filter(monkeypatch):
    """过滤 META/CHITCHAT 与低分候选，最多取 2 个。"""
    monkeypatch.setattr(settings, "CLARIFY_MIN_CANDIDATE_SCORE", 0.15)
    picked = _business_candidates(
        [
            {"label": "META.UNKNOWN", "score": 0.9},
            {"label": "CHITCHAT.GENERAL", "score": 0.8},
            {"label": "AFTERSALE.REFUND", "score": 0.3},
            {"label": "ORDER.CANCEL", "score": 0.2},
            {"label": "AFTERSALE.RETURN", "score": 0.18},
            {"label": "PRODUCT.ASK_PRICE", "score": 0.05},  # 低于下限
        ]
    )
    assert [c["label"] for c in picked] == ["AFTERSALE.REFUND", "ORDER.CANCEL"]


async def test_no_business_candidates_returns_none(monkeypatch):
    mock = _fake_llm(monkeypatch, "问句")
    assert await generate_clarify_question("嗯", [{"label": "META.UNKNOWN", "score": 0.9}], None) is None
    mock.assert_not_awaited()  # 没有业务候选连 LLM 都不调


async def test_disabled_without_key(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    assert await generate_clarify_question("退的钱", _TOPK_BUSINESS, _MEMORY) is None


# ---------------- 生成与输出治理 ----------------


async def test_generates_question_with_context(monkeypatch):
    mock = _fake_llm(monkeypatch, "您是想申请退款，还是想退货寄回 AP-300 呢？\n多余行")
    q = await generate_clarify_question("退的那个怎么弄", _TOPK_BUSINESS, _MEMORY)
    assert q == "您是想申请退款，还是想退货寄回 AP-300 呢？"  # 只取首行
    prompt = mock.await_args.args[1]
    # prompt 含候选描述（catalog 投影，非意图代码手抄）与近期对话
    assert "要求退款" in prompt and "要求退货" in prompt
    assert "AP-300 有问题" in prompt
    # 用户消息经防注入包裹（wrap_user_input 的边界标记）
    assert "退的那个怎么弄" in prompt


async def test_output_governance_rejects_bad_output(monkeypatch):
    # 超长输出 → 回退模板
    _fake_llm(monkeypatch, "长" * 100)
    assert await generate_clarify_question("退的钱", _TOPK_BUSINESS, _MEMORY) is None
    # 空输出 → 回退模板
    _fake_llm(monkeypatch, "   ")
    assert await generate_clarify_question("退的钱", _TOPK_BUSINESS, _MEMORY) is None


async def test_output_guardrail_violation_falls_back(monkeypatch):
    _fake_llm(monkeypatch, "正常问句？")
    from app.chat.guardrail.engine import guardrail_engine

    monkeypatch.setattr(guardrail_engine, "check_output", lambda text: "OUTPUT_LEAK")
    assert await generate_clarify_question("退的钱", _TOPK_BUSINESS, _MEMORY) is None


# ---------------- 接入出口 ----------------


async def test_response_generate_clarify_path(monkeypatch):
    from app.chat.graph.nodes.response_generate import response_generate

    _fake_llm(monkeypatch, "您是想申请退款，还是想退货呢？")
    state = {
        "status": "FALLBACK",
        "intent_result": {"pred_label": "META.UNKNOWN", "top_k": _TOPK_BUSINESS},
        "normalized_text": "退的那个怎么弄",
        "memory": _MEMORY,
    }
    result = await response_generate(state)
    assert result["reply"] == "您是想申请退款，还是想退货呢？"
    assert result["graph_trace"] == ["response_generate:clarify"]


async def test_response_generate_falls_back_to_template_without_key(monkeypatch):
    from app.chat.graph.nodes.response_generate import response_generate

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    state = {
        "status": "FALLBACK",
        "intent_result": {"pred_label": "META.UNKNOWN", "top_k": _TOPK_BUSINESS},
        "normalized_text": "退的那个怎么弄",
    }
    result = await response_generate(state)
    # 零回归：无 Key 时与 Stage 20 基线一致走固定模板
    assert result["graph_trace"] == ["response_generate"]
    assert result["reply"]  # 模板话术非空


async def test_rag_answer_clarify_on_refusal(monkeypatch):
    from app.chat.graph.nodes import rag_answer as node_mod
    from app.kb.retriever import RetrievalTrace

    _fake_llm(monkeypatch, "您是想申请退款，还是想退货呢？")
    trace = RetrievalTrace(query="q", backend="fake")
    trace.refused = True
    monkeypatch.setattr(
        node_mod, "rag_answerer",
        SimpleNamespace(answer=AsyncMock(return_value=(None, trace))),
    )
    state = {
        "tenant_id": "t1",
        "session_id": "s1",
        "normalized_text": "退的那个怎么弄",
        "intent_result": {"pred_label": "META.UNKNOWN", "top_k": _TOPK_BUSINESS},
        "memory": _MEMORY,
        "status": "FALLBACK",
    }
    result = await node_mod.rag_answer(state, {"configurable": {"db_session": None}})
    assert result["reply"] == "您是想申请退款，还是想退货呢？"
    assert result["graph_trace"] == ["rag_answer:clarify"]
    assert result["answer_source"] == "refused"  # 指标口径不变
    assert result["retrieval"]["clarify"] is True
