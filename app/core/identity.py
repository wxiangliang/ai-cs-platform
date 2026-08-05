"""身份保证等级（IAL，Stage 35）。

等级经 contextvar 随请求注入（tenant budget 先例）：
- IAL0 未核验 / IAL1 渠道身份（API Key 鉴权通过）/ IAL2 OTP 级（暂无渠道）
  / IAL3 高风险二次验证（预留）；
- 开发模式默认 `IDENTITY_DEFAULT_LEVEL=2`（沙盒互信=零回归）；生产按需
  下调，要求 IAL2 的工具（改地址）未达标即结构性拒绝转人工。
执法点：ActionExecutor（写）与 tool_invoke（读）在 provider 调用前校验。
"""

from contextvars import ContextVar

from app.core.config import settings

_current_ial: ContextVar[int | None] = ContextVar("current_ial", default=None)


def set_identity_level(*, authenticated: bool) -> None:
    """请求入口设置本轮身份等级（chat_service 调用）。

    default 是渠道基线（开发沙盒 2=互信；生产应下调到 0/1），
    鉴权通过是**加成**取 max——开启鉴权绝不把等级降到比基线还低。
    """
    level = settings.IDENTITY_DEFAULT_LEVEL
    if authenticated:
        level = max(level, settings.IDENTITY_LEVEL_AUTHENTICATED)
    _current_ial.set(level)


def current_identity_level() -> int:
    """当前请求身份等级；未设置按默认级（后台任务/测试直调场景）。"""
    value = _current_ial.get()
    return settings.IDENTITY_DEFAULT_LEVEL if value is None else value


def identity_sufficient(tool_id: str) -> bool:
    """当前等级是否满足工具最低要求（目录声明为准）。"""
    from app.chat.tools.catalog import required_ial

    return current_identity_level() >= required_ial(tool_id)
