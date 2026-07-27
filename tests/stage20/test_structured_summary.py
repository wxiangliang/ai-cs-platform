"""Stage 20 结构化会话摘要测试（fake LLM；DB 走真实 PG）。

覆盖：结构化摘要存取链路 / 非 JSON 降级纯文本 / 存量旧格式兼容 /
数组与总长编译时上界 / 单一表示不变式（摘要覆盖区间与短期窗口互斥）。
"""

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.chat.memory.local_provider import (
    _normalize_summary,
    _render_summary,
    local_memory_provider,
)
from app.core.config import settings
from app.db.session import AsyncSessionLocal, dispose_engine
from app.repositories.chat_message_repository import chat_message_repository
from app.repositories.chat_session_repository import chat_session_repository

VALID_SUMMARY_JSON = json.dumps(
    {
        "request": "查询订单物流",
        "entities": ["ORD20260701123", "白色空气净化器 AP-300"],
        "progress": "已查到物流卡在中转仓",
        "pending": ["等用户提供收货地址"],
        "answered": ["运费险规则"],
    },
    ensure_ascii=False,
)


@pytest.fixture
async def seed():
    """返回造数工厂：带 N 条消息的会话；测试后释放引擎。"""

    async def _make(message_count: int = 6):
        tenant, user = "s20-t", f"s20-u-{uuid.uuid4().hex[:6]}"
        async with AsyncSessionLocal() as session:
            record = await chat_session_repository.create(
                session, tenant_id=tenant, user_id=user, channel="web"
            )
            sid = record.id
            for i in range(message_count):
                await chat_message_repository.create(
                    session,
                    tenant_id=tenant,
                    session_id=sid,
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"消息{i}",
                )
            await session.commit()
        return tenant, user, sid

    yield _make
    await dispose_engine()


async def _add_messages(tenant: str, sid: str, start: int, count: int) -> None:
    async with AsyncSessionLocal() as session:
        for i in range(start, start + count):
            await chat_message_repository.create(
                session,
                tenant_id=tenant,
                session_id=sid,
                role="user" if i % 2 == 0 else "assistant",
                content=f"消息{i}",
            )
        await session.commit()


async def _get_meta(sid: str) -> dict:
    async with AsyncSessionLocal() as session:
        record = await chat_session_repository.get_by_id(session, sid)
        return dict(record.metadata_json or {})


async def _set_meta(sid: str, meta: dict) -> None:
    async with AsyncSessionLocal() as session:
        record = await chat_session_repository.get_by_id(session, sid)
        record.metadata_json = meta
        await session.commit()


def _fake_llm(monkeypatch, reply: str) -> AsyncMock:
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake")
    mock = AsyncMock(return_value=reply)
    monkeypatch.setattr("app.chat.memory.local_provider.chat_completion", mock)
    return mock


async def test_structured_summary_written_and_rendered(monkeypatch, seed):
    """合法 JSON → 存 dict + summary_format=json；注入时渲染含具体单号。"""
    tenant, user, sid = await seed(6)
    monkeypatch.setattr(settings, "MEMORY_SUMMARY_THRESHOLD", 2)
    monkeypatch.setattr(settings, "MEMORY_SHORT_TERM_TURNS", 2)
    _fake_llm(monkeypatch, VALID_SUMMARY_JSON)

    await local_memory_provider._maybe_summarize(tenant, sid)

    meta = await _get_meta(sid)
    assert isinstance(meta["memory_summary"], dict)
    assert meta["summary_format"] == "json"
    assert meta["memory_summary_covered"] == 4
    context = await local_memory_provider.get_context(tenant, user, sid, "")
    # 下游拿到的仍是 str；具体单号原文在场
    assert isinstance(context.session_summary, str)
    assert "ORD20260701123" in context.session_summary
    assert "诉求：查询订单物流" in context.session_summary


async def test_non_json_degrades_to_text(monkeypatch, seed):
    """LLM 输出非 JSON → 降级纯文本存储，不抛错。"""
    tenant, user, sid = await seed(6)
    monkeypatch.setattr(settings, "MEMORY_SUMMARY_THRESHOLD", 2)
    monkeypatch.setattr(settings, "MEMORY_SHORT_TERM_TURNS", 2)
    _fake_llm(monkeypatch, "这轮用户主要咨询了物流进度。")

    await local_memory_provider._maybe_summarize(tenant, sid)

    meta = await _get_meta(sid)
    assert meta["memory_summary"] == "这轮用户主要咨询了物流进度。"
    assert meta["summary_format"] == "text"
    assert meta["memory_summary_covered"] == 4
    context = await local_memory_provider.get_context(tenant, user, sid, "")
    assert context.session_summary == "这轮用户主要咨询了物流进度。"


async def test_legacy_plain_text_summary_readable(seed):
    """存量纯文本摘要（Stage 10 旧格式）原样注入。"""
    tenant, user, sid = await seed(2)
    await _set_meta(sid, {"memory_summary": "旧版纯文本摘要", "memory_summary_covered": 2})
    context = await local_memory_provider.get_context(tenant, user, sid, "")
    assert context.session_summary == "旧版纯文本摘要"


async def test_list_fields_capped_keep_newest(monkeypatch, seed):
    """entities 超 5 条 → 截断丢最旧，保留最新 5 条。"""
    tenant, _, sid = await seed(6)
    monkeypatch.setattr(settings, "MEMORY_SUMMARY_THRESHOLD", 2)
    monkeypatch.setattr(settings, "MEMORY_SHORT_TERM_TURNS", 2)
    reply = json.dumps(
        {"request": "多订单咨询", "entities": [f"ORD{i}" for i in range(7)]},
        ensure_ascii=False,
    )
    _fake_llm(monkeypatch, reply)

    await local_memory_provider._maybe_summarize(tenant, sid)

    meta = await _get_meta(sid)
    assert meta["memory_summary"]["entities"] == ["ORD2", "ORD3", "ORD4", "ORD5", "ORD6"]


async def test_over_budget_rejected_keeps_old_summary(monkeypatch, seed):
    """裁剪后序列化仍超 500 字 → 拒写，旧摘要与 covered 游标保持不变。"""
    tenant, _, sid = await seed(6)
    monkeypatch.setattr(settings, "MEMORY_SUMMARY_THRESHOLD", 2)
    monkeypatch.setattr(settings, "MEMORY_SHORT_TERM_TURNS", 2)
    monkeypatch.setattr(settings, "MEMORY_SUMMARY_STEP", 2)  # 本测试关注超长拒写，不关步长
    old_meta = {
        "memory_summary": {"request": "旧诉求"},
        "summary_format": "json",
        "memory_summary_covered": 2,
    }
    await _set_meta(sid, old_meta)
    reply = json.dumps({"request": "长" * 600}, ensure_ascii=False)
    _fake_llm(monkeypatch, reply)

    await local_memory_provider._maybe_summarize(tenant, sid)

    meta = await _get_meta(sid)
    assert meta["memory_summary"] == {"request": "旧诉求"}
    assert meta["memory_summary_covered"] == 2


@pytest.mark.parametrize(("window", "threshold"), [(1, 3), (2, 2), (3, 4)])
async def test_single_representation_invariant(monkeypatch, seed, window, threshold):
    """单一表示不变式：任意窗口/阈值组合下，摘要覆盖区间与短期窗口无交集，
    且增量续写不重复纳入已覆盖消息。"""
    total = 8
    tenant, user, sid = await seed(total)
    monkeypatch.setattr(settings, "MEMORY_SUMMARY_THRESHOLD", threshold)
    monkeypatch.setattr(settings, "MEMORY_SHORT_TERM_TURNS", window)
    monkeypatch.setattr(settings, "MEMORY_SUMMARY_STEP", 2)  # 本测试关注不变式，逐轮滚动
    mock = _fake_llm(monkeypatch, VALID_SUMMARY_JSON)

    await local_memory_provider._maybe_summarize(tenant, sid)

    cut = total - window
    meta = await _get_meta(sid)
    assert meta["memory_summary_covered"] == cut
    prompt = mock.await_args.args[1]
    # 被摘要的消息 [0, cut) 全在增量 prompt 中；窗口消息 [cut, total) 一条都不在
    # （chunk 每行格式 "role: 内容\n"，wrap_user_input 保证末行后也有换行）
    for i in range(cut):
        assert f": 消息{i}\n" in prompt
    context = await local_memory_provider.get_context(tenant, user, sid, "")
    assert [c for _, c in context.recent_turns] == [f"消息{i}" for i in range(cut, total)]
    for _, content in context.recent_turns:
        assert f": {content}\n" not in prompt

    # 增量续写：追加 2 条后再摘要，只纳入 [cut, new_cut)，已覆盖区间不重复出现
    await _add_messages(tenant, sid, total, 2)
    await local_memory_provider._maybe_summarize(tenant, sid)
    new_cut = total + 2 - window
    meta = await _get_meta(sid)
    assert meta["memory_summary_covered"] == new_cut
    prompt2 = mock.await_args.args[1]
    increment = prompt2.split("对话增量：", 1)[1]
    for i in range(cut):
        assert f": 消息{i}\n" not in increment


def test_normalize_summary_units():
    """裁剪函数：丢未知字段/空值，数组限 5 条丢最旧。"""
    out = _normalize_summary(
        {
            "request": " 查订单 ",
            "unknown": "丢弃",
            "entities": ["", "A", "B", "C", "D", "E", "F"],
            "pending": [],
            "progress": 123,
        }
    )
    assert out == {"request": "查订单", "entities": ["B", "C", "D", "E", "F"]}


def test_render_summary_units():
    """渲染函数：dict → 紧凑中文；str 原样；空值安全。"""
    assert _render_summary("纯文本") == "纯文本"
    assert _render_summary(None) == ""
    assert _render_summary({}) == ""
    rendered = _render_summary(
        {"request": "查订单", "entities": ["ORD1", "ORD2"], "pending": ["等地址"]}
    )
    assert rendered == "诉求：查订单；涉及：ORD1、ORD2；待办：等地址"


# ---------------- 并发写覆盖修复（merge_metadata + CAS）----------------


async def test_summary_preserves_concurrent_locale_write(monkeypatch, seed):
    """摘要 LLM 等待期间请求事务写入 locale → 合并写不冲掉它（修复前整 dict 覆盖丢失）。"""
    tenant, user, sid = await seed(6)
    monkeypatch.setattr(settings, "MEMORY_SUMMARY_THRESHOLD", 2)
    monkeypatch.setattr(settings, "MEMORY_SHORT_TERM_TURNS", 2)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake")

    async def _llm_with_concurrent_write(system, user_msg, purpose="classify"):
        # 模拟 LLM 等待窗口内，另一事务更新会话 locale
        async with AsyncSessionLocal() as s:
            record = await chat_session_repository.get_by_id(s, sid)
            m = dict(record.metadata_json or {})
            m["locale"] = "en"
            record.metadata_json = m
            await s.commit()
        return VALID_SUMMARY_JSON

    monkeypatch.setattr(
        "app.chat.memory.local_provider.chat_completion", _llm_with_concurrent_write
    )
    await local_memory_provider._maybe_summarize(tenant, sid)

    meta = await _get_meta(sid)
    assert meta["locale"] == "en"  # 并发写入的键存活
    assert meta["summary_format"] == "json"  # 摘要也成功写入
    assert meta["memory_summary_covered"] == 4


async def test_summary_cas_first_writer_wins(monkeypatch, seed):
    """两个并发摘要任务：先完成者生效，后者 CAS 不中放弃（游标与摘要不被回退覆盖）。"""
    tenant, user, sid = await seed(6)
    monkeypatch.setattr(settings, "MEMORY_SUMMARY_THRESHOLD", 2)
    monkeypatch.setattr(settings, "MEMORY_SHORT_TERM_TURNS", 2)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake")

    winner_summary = {"request": "先到的摘要", "entities": ["ORD-WINNER"]}

    async def _llm_while_rival_commits(system, user_msg, purpose="classify"):
        # 模拟另一摘要任务在本任务 LLM 期间先完成写入（covered 0 → 4）
        async with AsyncSessionLocal() as s:
            ok = await chat_session_repository.merge_metadata(
                s, tenant, sid,
                {"memory_summary": winner_summary, "summary_format": "json",
                 "memory_summary_covered": 4},
                expect_summary_covered=0,
            )
            await s.commit()
            assert ok
        return VALID_SUMMARY_JSON  # 本任务（后到者）的产出

    monkeypatch.setattr(
        "app.chat.memory.local_provider.chat_completion", _llm_while_rival_commits
    )
    await local_memory_provider._maybe_summarize(tenant, sid)

    meta = await _get_meta(sid)
    # 先到者的摘要保留，后到者被 CAS 拒绝（修复前后到者会覆盖）
    assert meta["memory_summary"] == winner_summary
    assert meta["memory_summary_covered"] == 4


async def test_summary_step_gate(monkeypatch, seed):
    """滚动步长：增量不足步长不触发 LLM；达到步长才滚动（首次摘要不受限）。"""
    tenant, _, sid = await seed(8)
    monkeypatch.setattr(settings, "MEMORY_SUMMARY_THRESHOLD", 2)
    monkeypatch.setattr(settings, "MEMORY_SHORT_TERM_TURNS", 2)
    monkeypatch.setattr(settings, "MEMORY_SUMMARY_STEP", 4)
    mock = _fake_llm(monkeypatch, VALID_SUMMARY_JSON)

    # 首次摘要（covered=0）：不受步长限制，正常建立摘要 covered=6
    await local_memory_provider._maybe_summarize(tenant, sid)
    assert mock.await_count == 1
    assert (await _get_meta(sid))["memory_summary_covered"] == 6

    # 追加 2 条：增量 2 < 步长 4 → 不触发 LLM，游标不动（修复前每轮都调）
    await _add_messages(tenant, sid, 8, 2)
    await local_memory_provider._maybe_summarize(tenant, sid)
    assert mock.await_count == 1
    assert (await _get_meta(sid))["memory_summary_covered"] == 6

    # 再追加 2 条：增量 4 ≥ 步长 4 → 滚动一次，covered 推进到 10
    await _add_messages(tenant, sid, 10, 2)
    await local_memory_provider._maybe_summarize(tenant, sid)
    assert mock.await_count == 2
    assert (await _get_meta(sid))["memory_summary_covered"] == 10
