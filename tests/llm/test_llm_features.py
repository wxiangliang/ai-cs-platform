"""Stage 04-01 LLM 增强点单元测试（fake LLM，不依赖真实网络）。"""

from unittest.mock import AsyncMock

import pytest

from app.chat.intent.hybrid_classifier import hybrid_intent_classifier
from app.chat.intent.types import DecisionSource, IntentLabel
from app.chat.skills.llm_responder import polish_reply
from app.chat.slots.llm_extractor import extract_missing_slots
from app.chat.state.types import TurnStatus
from app.core.config import settings


class _FakeSetFit:
    """低置信的假 SetFit 模型（触发 LLM 二判分支）。"""

    available = True

    def predict(self, text: str, top_k: int = 3):
        return "PRODUCT.ASK_PRICE", 0.2, [{"label": "PRODUCT.ASK_PRICE", "score": 0.2}]


@pytest.fixture
def _llm_on(monkeypatch):
    """打开 LLM 开关（fake key），并把 SetFit 换成低置信假模型。"""
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake-key")
    monkeypatch.setattr(
        "app.chat.intent.hybrid_classifier.setfit_intent_model", _FakeSetFit()
    )


async def test_second_opinion_adopts_valid_label(monkeypatch, _llm_on):
    monkeypatch.setattr(
        "app.chat.intent.hybrid_classifier.chat_completion",
        AsyncMock(return_value="LOGISTICS.TRACK"),
    )
    r = await hybrid_intent_classifier.classify("东西还没到我等急了")
    assert r.pred_label == "LOGISTICS.TRACK"
    assert r.decision_source == DecisionSource.LLM


async def test_second_opinion_rejects_invalid_label(monkeypatch, _llm_on):
    monkeypatch.setattr(
        "app.chat.intent.hybrid_classifier.chat_completion",
        AsyncMock(return_value="不知道是什么意图"),
    )
    r = await hybrid_intent_classifier.classify("嗯嗯那个啥")
    # 无效输出 → 维持低置信 UNKNOWN 兜底
    assert r.pred_label == IntentLabel.META_UNKNOWN
    assert r.decision_source == DecisionSource.SETFIT_LOW_CONF


async def test_second_opinion_disabled_without_key(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(
        "app.chat.intent.hybrid_classifier.setfit_intent_model", _FakeSetFit()
    )
    r = await hybrid_intent_classifier.classify("嗯嗯那个啥")
    assert r.decision_source == DecisionSource.SETFIT_LOW_CONF


async def test_llm_slot_extraction_whitelist(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake-key")
    monkeypatch.setattr(
        "app.chat.slots.llm_extractor.chat_completion",
        AsyncMock(return_value='{"order_id": "A888", "evil_key": "x", "phone": ""}'),
    )
    slots = await extract_missing_slots("订单 A888 退款", "AFTERSALE.REFUND", ["order_id", "phone"])
    # 只接受声明的槽位名，空值丢弃，未声明键过滤
    assert slots == {"order_id": "A888"}


async def test_llm_slot_extraction_bad_json(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake-key")
    monkeypatch.setattr(
        "app.chat.slots.llm_extractor.chat_completion",
        AsyncMock(return_value="抱歉我不会"),
    )
    assert await extract_missing_slots("x", "AFTERSALE.REFUND", ["order_id"]) == {}


async def test_polish_keeps_facts(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake-key")
    monkeypatch.setattr(
        "app.chat.skills.llm_responder.chat_completion",
        AsyncMock(return_value="您好～「凉风空调 X1」现在的价格是 1999.00 元哦，有其他想了解的随时说！"),
    )
    draft = "「凉风空调 X1」目前价格为 1999.00 元。"
    out = await polish_reply(draft, TurnStatus.DONE, "多少钱")
    assert "1999.00" in out and out != draft


async def test_polish_reverts_when_facts_dropped(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake-key")
    monkeypatch.setattr(
        "app.chat.skills.llm_responder.chat_completion",
        AsyncMock(return_value="这款空调大概两千元左右～"),  # 丢失精确价格
    )
    draft = "「凉风空调 X1」目前价格为 1999.00 元。"
    assert await polish_reply(draft, TurnStatus.DONE, "多少钱") == draft


async def test_polish_skips_confirm_gate(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake-key")
    called = AsyncMock(return_value="改写")
    monkeypatch.setattr("app.chat.skills.llm_responder.chat_completion", called)
    draft = "您要对订单「A1」申请退款，确认提交吗？"
    # 确认门轮次不润色（话术即协议）
    assert await polish_reply(draft, TurnStatus.NEEDS_CONFIRM, "退款") == draft
    called.assert_not_called()
