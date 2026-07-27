"""轮级 LLM 时间预算 + 后台记忆任务并发闸（容量修复）回归。"""

import asyncio
import time

from app.chat.llm import deadline
from app.chat.llm.factory import chat_completion
from app.core.config import settings


class _FakeModel:
    """假 ChatModel：可配置响应延迟。"""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls = 0

    async def ainvoke(self, messages, config=None):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)

        class _R:
            content = "回复"
            usage_metadata = None
            response_metadata: dict = {}

        return _R()


def _enable_llm(monkeypatch, model: _FakeModel) -> None:
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "fake")
    monkeypatch.setattr("app.chat.llm.factory.get_chat_model", lambda purpose: model)


async def test_budget_exhausted_degrades_without_calling(monkeypatch):
    """预算耗尽 → 直接返回 None，不发起模型调用。"""
    model = _FakeModel()
    _enable_llm(monkeypatch, model)
    deadline._deadline_var.set(time.monotonic() - 1)  # 已过期
    try:
        assert await chat_completion("s", "u", purpose="classify") is None
        assert model.calls == 0
    finally:
        deadline.clear_turn_budget()


async def test_remaining_budget_bounds_single_call(monkeypatch):
    """单次调用超过剩余预算 → 外层 wait_for 按剩余预算截断，降级返回 None。"""
    model = _FakeModel(delay=5.0)  # 模型响应远超剩余预算
    _enable_llm(monkeypatch, model)
    deadline._deadline_var.set(time.monotonic() + 1.5)  # 剩余 1.5s（高于最小有用预算）
    try:
        start = time.monotonic()
        assert await chat_completion("s", "u", purpose="classify") is None
        elapsed = time.monotonic() - start
        assert model.calls == 1  # 确实发起了调用，被 wait_for 截断
        assert elapsed < 3.0, f"未按剩余预算截断，等了 {elapsed:.1f}s"
    finally:
        deadline.clear_turn_budget()


async def test_budget_disabled_is_unlimited(monkeypatch):
    """TURN_LLM_BUDGET_SECONDS=0 → 不设 deadline，行为与修复前一致。"""
    model = _FakeModel()
    _enable_llm(monkeypatch, model)
    monkeypatch.setattr(settings, "TURN_LLM_BUDGET_SECONDS", 0.0)
    deadline.start_turn_budget()
    assert deadline.remaining_budget() is None
    assert not deadline.budget_exhausted()
    assert await chat_completion("s", "u", purpose="classify") == "回复"


async def test_start_and_clear_budget(monkeypatch):
    monkeypatch.setattr(settings, "TURN_LLM_BUDGET_SECONDS", 40.0)
    deadline.start_turn_budget()
    remaining = deadline.remaining_budget()
    assert remaining is not None and 39.0 < remaining <= 40.0
    deadline.clear_turn_budget()
    assert deadline.remaining_budget() is None


async def test_memory_task_semaphore_caps_concurrency(monkeypatch):
    """后台记忆任务并发不超过 MEMORY_TASK_CONCURRENCY，超出的排队执行。"""
    from app.chat.memory import scheduler
    from app.chat.memory.scheduler import MemoryWriteRequest, _remember_safe

    monkeypatch.setattr(settings, "MEMORY_TASK_CONCURRENCY", 2)
    # 强制重建并发闸（可能残留其他测试的 loop/配置）
    monkeypatch.setattr(scheduler, "_semaphore", None)
    monkeypatch.setattr(scheduler, "_semaphore_loop", None)

    running = 0
    peak = 0

    class _FakeProvider:
        async def remember(self, *args):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            running -= 1

    monkeypatch.setattr(
        "app.chat.memory.factory.get_memory_provider", lambda: _FakeProvider()
    )
    reqs = [
        MemoryWriteRequest("t", "u", f"s{i}", "问", "答", "FAQ.GENERAL") for i in range(6)
    ]
    await asyncio.gather(*(_remember_safe(r) for r in reqs))
    assert peak <= 2, f"并发峰值 {peak} 超过上限 2"
