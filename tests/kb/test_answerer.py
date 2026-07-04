"""RagAnswerer 回答策略单元测试（monkeypatch 检索层，不依赖 Milvus/PG）。"""

from unittest.mock import AsyncMock

import pytest

from app.kb.answerer import rag_answerer
from app.kb.retriever import FaqAnswer, kb_retriever
from app.kb.types import Hit


@pytest.fixture
def _no_llm(monkeypatch):
    """确保不走 LLM 生成路径（未配置 API Key）。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")


async def test_faq_hit_returns_standard_answer(monkeypatch, _no_llm):
    monkeypatch.setattr(
        kb_retriever,
        "search_faq",
        AsyncMock(return_value=FaqAnswer(faq_id="f1", question="退货运费谁出", answer="标准答案", score=0.95)),
    )
    result, trace = await rag_answerer.answer(None, "t1", "退货运费谁承担")
    assert result is not None
    assert result.source == "faq"
    assert result.reply == "标准答案"
    assert trace.refused is False


async def test_low_score_refuses(monkeypatch, _no_llm):
    monkeypatch.setattr(kb_retriever, "search_faq", AsyncMock(return_value=None))
    low_hits = [Hit(id="c1", score=0.1, source_backend="milvus", title="政策", content="内容")]
    monkeypatch.setattr(kb_retriever, "search_chunks", AsyncMock(return_value=low_hits))
    result, trace = await rag_answerer.answer(None, "t1", "你们CEO是谁")
    # 低于拒答阈值：回答为 None（调用方走澄清/转人工），绝不编造；轨迹仍返回供落库
    assert result is None
    assert trace.refused is True


async def test_good_hit_extractive_fallback_without_llm(monkeypatch, _no_llm):
    monkeypatch.setattr(kb_retriever, "search_faq", AsyncMock(return_value=None))
    hits = [
        Hit(id="c1", score=0.85, source_backend="milvus", title="退换货政策", content="签收后7天内可无理由退货。"),
    ]
    monkeypatch.setattr(kb_retriever, "search_chunks", AsyncMock(return_value=hits))
    result, _trace = await rag_answerer.answer(None, "t1", "退货政策是什么")
    assert result is not None
    assert result.source == "rag_extract"
    # 摘录式回答必须引用原文与来源，不改写
    assert "签收后7天内可无理由退货" in result.reply
    assert "《退换货政策》" in result.reply
    assert result.citations == ["《退换货政策》"]
