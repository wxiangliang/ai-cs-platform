"""轮级 LLM 时间预算（容量修复）。

背景：一轮对话最坏可串行触发多次 LLM 调用（二判/槽位/确认门/润色/RAG 生成），
每次 timeout 30s × 重试，理论最坏分钟级——期间用户在等、DB 连接被占。
本模块提供轮内共享的时间预算：

- 入口（chat_service.process_message）`start_turn_budget()` 设定 deadline；
- 每次 LLM 调用前查 `remaining_budget()`：不足则直接降级
  （与无 Key 同路径——模板/摘录/规则，所有调用点已有降级分支）；
- 单次调用以剩余预算为外层超时（asyncio.wait_for），防重试放大尾延迟。

预算耗尽不是错误，是降级信号。`TURN_LLM_BUDGET_SECONDS=0` 关闭。
后台任务（记忆写入）继承请求上下文时必须 `clear_turn_budget()`——
它们在轮次结束后运行，残留的过期 deadline 会把记忆 LLM 全部饿死。
"""

import time
from contextvars import ContextVar

from app.core.config import settings

# 本轮 LLM deadline（time.monotonic 时刻）；None=无预算限制
_deadline_var: ContextVar[float | None] = ContextVar("turn_llm_deadline", default=None)

# 剩余预算低于该值（秒）不再发起新调用（发了也大概率超时白付）
_MIN_USEFUL_BUDGET = 1.0


def start_turn_budget() -> None:
    """轮次入口设定 LLM 时间预算；配置为 0 时不设限。"""
    budget = settings.TURN_LLM_BUDGET_SECONDS
    _deadline_var.set(time.monotonic() + budget if budget > 0 else None)


def clear_turn_budget() -> None:
    """清除预算（后台任务/脚本上下文使用，避免继承过期 deadline）。"""
    _deadline_var.set(None)


def remaining_budget() -> float | None:
    """剩余预算秒数；未设预算返回 None（不限制）。"""
    deadline = _deadline_var.get()
    return None if deadline is None else deadline - time.monotonic()


def budget_exhausted() -> bool:
    """剩余预算是否已不足以发起一次有意义的 LLM 调用。"""
    remaining = remaining_budget()
    return remaining is not None and remaining < _MIN_USEFUL_BUDGET
