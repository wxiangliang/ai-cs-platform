"""基础结构化日志模块。

根据配置的 LOG_LEVEL 初始化全局日志，统一日志格式，
并预留 trace_id 扩展入口，方便后续做全链路追踪。
"""

import logging
from contextvars import ContextVar

from app.core.config import settings

# trace_id 上下文变量：在请求入口处写入，日志里自动携带。
# 第一阶段先预留入口，后续可在中间件中为每个请求生成并 set。
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")


def set_trace_id(trace_id: str) -> None:
    """写入当前上下文的 trace_id（预留给后续中间件调用）。"""
    _trace_id_ctx.set(trace_id)


def get_trace_id() -> str:
    """读取当前上下文的 trace_id，未设置时返回占位符 '-'。"""
    return _trace_id_ctx.get()


class _TraceIdFilter(logging.Filter):
    """日志过滤器：把当前上下文的 trace_id 注入到每条日志记录。

    这样日志格式里就能使用 %(trace_id)s 字段。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True


# 日志格式：时间 | 级别 | trace_id | logger 名称 | 消息
_LOG_FORMAT = "%(asctime)s | %(levelname)s | trace=%(trace_id)s | %(name)s | %(message)s"


def setup_logging() -> None:
    """初始化全局日志配置。

    在应用启动时调用一次，根据 LOG_LEVEL 设置根 logger 级别，
    并为 handler 挂上 trace_id 过滤器。
    """
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.addFilter(_TraceIdFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # 避免重复初始化时叠加多个 handler（如 --reload 热加载场景）
    root_logger.handlers.clear()
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """获取带命名空间的 logger，业务模块统一通过此函数获取。"""
    return logging.getLogger(name)
