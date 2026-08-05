"""Stage 29 批 1：观测查询测试（仓储查询 + 路由序列化，活库）。"""

import uuid

import pytest

from app.api.routes.observe import (
    _decision_item,
    _message_item,
    _session_item,
    _tool_call_item,
)
from app.db.session import AsyncSessionLocal, dispose_engine
from app.repositories.chat_decision_log_repository import chat_decision_log_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.repositories.chat_tool_call_repository import chat_tool_call_repository

_TENANT = f"t-observe-{uuid.uuid4().hex[:8]}"


# 每个测试独立事件循环 → 引擎连接池不能跨测试复用（同 stage07 模式）
@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    await dispose_engine()


async def test_list_by_tenant_filters_and_pagination():
    async with AsyncSessionLocal() as db:
        for i in range(3):
            await chat_session_repository.create(
                db,
                tenant_id=_TENANT,
                user_id=f"u{i % 2}",
                channel="web",
                status="active" if i < 2 else "closed",
            )
        await db.commit()

        all_rows = await chat_session_repository.list_by_tenant(db, _TENANT)
        assert len(all_rows) == 3
        # 用户过滤
        u0 = await chat_session_repository.list_by_tenant(db, _TENANT, user_id="u0")
        assert {s.user_id for s in u0} == {"u0"}
        # 状态过滤
        closed = await chat_session_repository.list_by_tenant(db, _TENANT, status="closed")
        assert len(closed) == 1
        # 分页
        page1 = await chat_session_repository.list_by_tenant(db, _TENANT, limit=2)
        page2 = await chat_session_repository.list_by_tenant(db, _TENANT, limit=2, offset=2)
        assert len(page1) == 2 and len(page2) == 1
        assert {s.id for s in page1} | {s.id for s in page2} == {s.id for s in all_rows}
        # 序列化字段完整
        item = _session_item(all_rows[0])
        assert set(item) == {"session_id", "user_id", "channel", "status", "created_at", "updated_at"}


async def test_tool_call_list_and_serialization():
    session_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        await chat_tool_call_repository.create(
            db,
            tenant_id=_TENANT,
            session_id=session_id,
            tool_id="query_order",
            request_json={"order_id": "A1"},
            response_json={"status": "SHIPPED"},
            ok=True,
            error_code=None,
            latency_ms=12.5,
        )
        await db.commit()
        rows = await chat_tool_call_repository.list_by_session_id(db, _TENANT, session_id)
        assert len(rows) == 1
        item = _tool_call_item(rows[0])
        assert item["tool_id"] == "query_order"
        assert item["ok"] is True
        assert item["response"] == {"status": "SHIPPED"}
        # 跨租户隔离
        other = await chat_tool_call_repository.list_by_session_id(db, "t-other", session_id)
        assert other == []


async def test_decision_serialization_exposes_analysis_fields():
    """决策序列化必须透出分析关键 JSON（Stage 26/27 证据都在里面）。"""
    session_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        await chat_decision_log_repository.create(
            db,
            tenant_id=_TENANT,
            session_id=session_id,
            original_text="订单号是12345678",
            normalized_text="订单号是12345678",
            intent_result_json={
                "pred_label": "META.SLOT_ONLY",
                "decision_source": "RULE_PENDING_SLOT",
                "pending_fill": {"slot": "order_id", "evidence": "explicit_slot_name"},
            },
            status="NEEDS_CONFIRM",
            decision_source="RULE_PENDING_SLOT",
            graph_trace_json={"trace": ["intent_classify"], "meta_shadow": {"actual": "CONTINUE_CURRENT"}},
        )
        await db.commit()
        rows = await chat_decision_log_repository.list_by_session_id(db, _TENANT, session_id)
        item = _decision_item(rows[0])
        assert item["intent_result"]["pending_fill"]["slot"] == "order_id"
        assert item["graph_trace"]["meta_shadow"]["actual"] == "CONTINUE_CURRENT"
        assert item["decision_source"] == "RULE_PENDING_SLOT"
        for key in ("retrieval", "experiment", "latency", "error"):
            assert key in item


def test_message_item_fields():
    class _M:
        id = "m1"
        role = "assistant"
        content = "好的"
        intent = "AFTERSALE.REFUND"
        status = "NEEDS_SLOT"
        trace_id = "trace-1"

        class created_at:  # noqa: N801 - 模拟 datetime 接口
            @staticmethod
            def isoformat() -> str:
                return "2026-08-05T00:00:00"

    item = _message_item(_M())  # type: ignore[arg-type]
    assert item["trace_id"] == "trace-1"
    assert item["role"] == "assistant"
