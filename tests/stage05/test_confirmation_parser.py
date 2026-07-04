"""ConfirmationResponseParser 单元测试（fake LLM）。"""

from unittest.mock import AsyncMock

import pytest

from app.chat.confirmation.parser import confirmation_parser
from app.core.config import settings

TASK = {
    "intent": "AFTERSALE.REFUND",
    "required_slots": ["order_id"],
    "collected_slots": {"order_id": "A1"},
}


@pytest.fixture
def _llm_on(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake-key")


async def test_parse_modify_with_slot_whitelist(monkeypatch, _llm_on):
    monkeypatch.setattr(
        "app.chat.confirmation.parser.chat_completion",
        AsyncMock(return_value='{"verdict": "MODIFY", "slot_updates": {"order_id": "B222", "evil": "x"}}'),
    )
    outcome = await confirmation_parser.parse("订单号错了，是 B222", TASK)
    assert outcome is not None and outcome.verdict == "MODIFY"
    # 白名单过滤：只接受任务声明过的槽位
    assert outcome.slot_updates == {"order_id": "B222"}


async def test_parse_confirm(monkeypatch, _llm_on):
    monkeypatch.setattr(
        "app.chat.confirmation.parser.chat_completion",
        AsyncMock(return_value='{"verdict": "CONFIRM", "slot_updates": {}}'),
    )
    outcome = await confirmation_parser.parse("行吧就这样弄吧", TASK)
    assert outcome is not None and outcome.verdict == "CONFIRM"


async def test_parse_invalid_output(monkeypatch, _llm_on):
    monkeypatch.setattr(
        "app.chat.confirmation.parser.chat_completion",
        AsyncMock(return_value="我觉得他同意了"),
    )
    assert await confirmation_parser.parse("嗯", TASK) is None


async def test_parse_without_llm(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    assert await confirmation_parser.parse("行吧", TASK) is None
