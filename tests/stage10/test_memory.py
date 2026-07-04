"""记忆系统单元测试（fake LLM / factory 降级；DB 相关走真实 PG）。"""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.chat.memory.base import MemoryContext
from app.chat.memory.factory import get_memory_provider
from app.chat.memory.local_provider import local_memory_provider
from app.core.config import settings
from app.db.session import AsyncSessionLocal, dispose_engine
from app.repositories.chat_message_repository import chat_message_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.repositories.user_memory_repository import user_memory_repository


@pytest.fixture
async def _seeded_session():
    """造一个带消息的会话，测试后清理。"""
    tenant, user = "mem-t", f"mem-u-{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as session:
        record = await chat_session_repository.create(
            session, tenant_id=tenant, user_id=user, channel="web"
        )
        sid = record.id
        for i in range(4):
            await chat_message_repository.create(
                session, tenant_id=tenant, session_id=sid,
                role="user" if i % 2 == 0 else "assistant", content=f"消息{i}",
            )
        await session.commit()
    yield tenant, user, sid
    await dispose_engine()


async def test_local_get_context_short_term(_seeded_session):
    """无 LLM：短期窗口照常返回，摘要与长期为空。"""
    tenant, user, sid = _seeded_session
    context = await local_memory_provider.get_context(tenant, user, sid, "查询")
    assert len(context.recent_turns) == 4
    assert context.recent_turns[0] == ("user", "消息0")  # 正序
    assert context.session_summary == "" and context.long_term_facts == []


async def test_local_remember_noop_without_llm(monkeypatch, _seeded_session):
    tenant, user, sid = _seeded_session
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    # 不抛异常、不产生长期记忆
    await local_memory_provider.remember(tenant, user, sid, "我要退款", "好的", "AFTERSALE.REFUND")
    async with AsyncSessionLocal() as session:
        assert await user_memory_repository.list_recent(session, tenant, user) == []


async def test_local_fact_extraction_with_fake_llm(monkeypatch, _seeded_session):
    tenant, user, sid = _seeded_session
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake")
    monkeypatch.setattr(
        "app.chat.memory.local_provider.chat_completion",
        AsyncMock(return_value='["用户偏好白色家电"]'),
    )
    await local_memory_provider.remember(tenant, user, sid, "我喜欢白色的", "好的", "PRODUCT.RECOMMEND")
    # 重复调用不重复入库
    await local_memory_provider.remember(tenant, user, sid, "我喜欢白色的", "好的", "PRODUCT.RECOMMEND")
    async with AsyncSessionLocal() as session:
        memories = await user_memory_repository.list_recent(session, tenant, user)
    assert [m.content for m in memories] == ["用户偏好白色家电"]


async def test_summary_written_over_threshold(monkeypatch, _seeded_session):
    tenant, user, sid = _seeded_session
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake")
    monkeypatch.setattr(settings, "MEMORY_SUMMARY_THRESHOLD", 2)
    monkeypatch.setattr(settings, "MEMORY_SHORT_TERM_TURNS", 2)
    monkeypatch.setattr(
        "app.chat.memory.local_provider.chat_completion",
        AsyncMock(return_value="用户咨询过消息0相关问题"),
    )
    await local_memory_provider._maybe_summarize(tenant, sid)
    context = await local_memory_provider.get_context(tenant, user, sid, "")
    assert context.session_summary == "用户咨询过消息0相关问题"


def test_factory_mem0_degrades_without_key(monkeypatch):
    """MEMORY_PROVIDER=mem0 但无 Key → 降级 local 并告警。"""
    get_memory_provider.cache_clear()
    monkeypatch.setattr(settings, "MEMORY_PROVIDER", "mem0")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    provider = get_memory_provider()
    assert provider.name == "local"
    get_memory_provider.cache_clear()


def test_memory_context_roundtrip():
    ctx = MemoryContext(session_summary="s", recent_turns=[("user", "hi")], long_term_facts=["f"])
    assert MemoryContext.from_dict(ctx.to_dict()) == ctx
