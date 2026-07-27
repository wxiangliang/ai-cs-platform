"""Stage 22 只读诊断 agent 测试（fake LLM + fake ToolProvider；审计走真实 PG）。

覆盖：结构性终止条件全集 / 白名单红线 / 数字事实校验 /
触发启发式 / 默认关闭零回归。
"""

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.chat.agents.diagnose import (
    READONLY_TOOLS,
    _facts_grounded,
    needs_diagnosis,
    run_diagnose,
)
from app.chat.tools.base import ToolResult
from app.core.config import settings
from app.db.session import AsyncSessionLocal, dispose_engine


@pytest.fixture
async def db():
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()
    await dispose_engine()


class _FakeProvider:
    """记录调用序列的假工具方，按 tool_id 返回预置数据。"""

    def __init__(self, data_by_tool=None, fail_tools=()):
        self.calls: list[tuple[str, dict]] = []
        self._data = data_by_tool or {}
        self._fail = set(fail_tools)

    async def invoke(self, tool_id, params, *, tenant_id):
        self.calls.append((tool_id, dict(params)))
        if tool_id in self._fail:
            return ToolResult(ok=False, error_code="UPSTREAM_UNAVAILABLE")
        return ToolResult(ok=True, data=self._data.get(tool_id, {"note": "ok"}))


def _enable(monkeypatch, provider, llm_replies: list[str]):
    """开启 agent + fake LLM（按序返回决策/综合回复）+ fake 工具方。"""
    monkeypatch.setattr(settings, "DIAGNOSE_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake")
    mock = AsyncMock(side_effect=llm_replies)
    monkeypatch.setattr("app.chat.agents.diagnose.chat_completion", mock)
    monkeypatch.setattr("app.chat.agents.diagnose.get_tool_provider", lambda: provider)
    return mock


def _call(tool_id, params=None):
    return json.dumps({"action": "call", "tool_id": tool_id, "params": params or {}})


_ANSWER = json.dumps({"action": "answer"})


# ---------------- 触发与开关 ----------------


def test_needs_diagnosis_heuristics():
    assert needs_diagnosis("我的订单为什么还没到")
    assert needs_diagnosis("物流三天没动了怎么回事")
    assert not needs_diagnosis("查一下订单 A123 到哪了")  # 纯查询不触发
    assert not needs_diagnosis("")


async def test_disabled_returns_none(monkeypatch, db):
    monkeypatch.setattr(settings, "DIAGNOSE_AGENT_ENABLED", False)
    result = await run_diagnose(
        db, "t1", "s1", user_text="为什么还没到", observations={}, slots={}
    )
    assert result is None  # 默认关闭零回归


# ---------------- 决策循环 ----------------


async def test_call_then_answer_flow(monkeypatch, db):
    """决策 call→answer 两步：工具被调、审计落库、解释生成且数字有据。"""
    provider = _FakeProvider(
        {"query_logistics_track": {"latest": "卡在广州中转仓 2 天", "days_stuck": 2}}
    )
    _enable(
        monkeypatch,
        provider,
        [
            _call("query_logistics_track", {"order_id": "A123"}),
            _ANSWER,
            "包裹在广州中转仓停留了 2 天，可能是分拣延迟，这部分需要人工进一步核实。",
        ],
    )
    sid = f"s22-{uuid.uuid4().hex[:8]}"
    outcome = await run_diagnose(
        db, "t-s22", sid,
        user_text="订单 A123 为什么还没到",
        observations={"order_id": "A123", "status": "已发货"},
        slots={"order_id": "A123"},
    )
    assert outcome is not None
    assert "2 天" in outcome.explanation
    assert provider.calls == [("query_logistics_track", {"order_id": "A123"})]
    assert outcome.steps == [
        {"tool_id": "query_logistics_track", "ok": True, "error_code": None}
    ]
    # 审计落库（chat_tool_call 与静态链同表）
    from sqlalchemy import select

    from app.models.chat_tool_call import ChatToolCall

    rows = (
        (await db.execute(select(ChatToolCall).where(ChatToolCall.session_id == sid)))
        .scalars().all()
    )
    assert len(rows) == 1 and rows[0].tool_id == "query_logistics_track"


async def test_non_whitelist_tool_terminates(monkeypatch, db):
    """非白名单（写工具）→ 决策终止，不调用任何工具，仍产出综合。"""
    provider = _FakeProvider()
    _enable(monkeypatch, provider, [_call("cancel_order", {"order_id": "A1"}), "订单显示已发货。"])
    outcome = await run_diagnose(
        db, "t1", "s1", user_text="为什么还没到",
        observations={"status": "已发货"}, slots={},
    )
    assert provider.calls == []  # 写工具绝不被调用（红线）
    assert outcome is not None and outcome.steps == []


async def test_unparseable_decision_terminates(monkeypatch, db):
    _enable(monkeypatch, _FakeProvider(), ["我觉得应该再查一下", "订单显示已发货。"])
    outcome = await run_diagnose(
        db, "t1", "s1", user_text="为什么", observations={"status": "已发货"}, slots={}
    )
    assert outcome is not None and outcome.steps == []  # 解析失败不循环，直接综合


async def test_repeat_call_terminates(monkeypatch, db):
    """重复调用同一工具同参数 → 终止（防打转），工具只被调一次。"""
    provider = _FakeProvider({"query_shipping_policy": {"policy": "偏远地区 5 天"}})
    same = _call("query_shipping_policy")
    _enable(monkeypatch, provider, [same, same, "配送政策为偏远地区 5 天内送达。"])
    outcome = await run_diagnose(
        db, "t1", "s1", user_text="为什么这么慢", observations={}, slots={}
    )
    assert len(provider.calls) == 1
    assert outcome is not None and "5 天" in outcome.explanation


async def test_max_steps_cap(monkeypatch, db):
    """决策永远 call 不同参数 → 恰好 DIAGNOSE_MAX_STEPS 次工具调用。"""
    monkeypatch.setattr(settings, "DIAGNOSE_MAX_STEPS", 3)
    provider = _FakeProvider()
    # 3 次决策消费前 3 条；循环到步数上限终止后，综合消费第 4 条
    replies = [_call("query_order", {"order_id": f"A{i}"}) for i in range(3)] + ["数据显示一切正常。"]
    _enable(monkeypatch, provider, replies)
    outcome = await run_diagnose(
        db, "t1", "s1", user_text="怎么回事", observations={}, slots={}
    )
    assert len(provider.calls) == 3  # 步数硬上限
    assert outcome is not None


async def test_consecutive_failures_terminate(monkeypatch, db):
    provider = _FakeProvider(fail_tools={"query_order"})
    # 2 次失败决策后终止，综合消费第 3 条
    replies = [_call("query_order", {"order_id": "A1"}), _call("query_order", {"order_id": "A2"}),
               "现有数据无法确认，需要人工核实。"]
    _enable(monkeypatch, provider, replies)
    outcome = await run_diagnose(db, "t1", "s1", user_text="为什么", observations={}, slots={})
    assert len(provider.calls) == 2  # 连续 2 次失败即停
    assert outcome is not None


# ---------------- 事实校验与护栏 ----------------


def test_facts_grounded():
    obs = {"days": 2, "fee": "12.50 元"}
    assert _facts_grounded("停留了 2 天，运费 12.50 元", obs)
    assert not _facts_grounded("大约 3 天后送达", obs)  # 3 不在观察中


async def test_ungrounded_number_drops_explanation(monkeypatch, db):
    """解释编造了观察中不存在的数字 → 整段丢弃降级。"""
    _enable(monkeypatch, _FakeProvider(), [_ANSWER, "预计 3 天后送达。"])
    outcome = await run_diagnose(
        db, "t1", "s1", user_text="为什么还没到",
        observations={"status": "已发货"}, slots={},
    )
    assert outcome is None


async def test_guardrail_violation_drops_explanation(monkeypatch, db):
    _enable(monkeypatch, _FakeProvider(), [_ANSWER, "订单显示已发货。"])
    from app.chat.guardrail.engine import guardrail_engine

    monkeypatch.setattr(guardrail_engine, "check_output", lambda text: "OUTPUT_LEAK")
    outcome = await run_diagnose(
        db, "t1", "s1", user_text="为什么", observations={"status": "已发货"}, slots={}
    )
    assert outcome is None


# ---------------- 白名单红线 ----------------


def test_whitelist_contains_no_write_tools():
    """白名单永不收录写操作（create_/cancel_/update_ 前缀）。"""
    for tool_id in READONLY_TOOLS:
        assert not tool_id.startswith(("create_", "cancel_", "update_")), tool_id
        assert tool_id.startswith("query_"), tool_id
