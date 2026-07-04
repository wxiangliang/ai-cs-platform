"""Langfuse 链路追踪（Stage 12）。

与 Prometheus 指标互补：指标管聚合与告警，Langfuse 管单次调用级链路明细
（图节点 span 树 + LLM prompt/completion/token 用量）。

红线（与 LLM/Milvus/Redis 同一韧性原则）：
- 未配置 Key / SDK 导入失败 / 初始化异常 → 一律返回 None，主链路零感知；
- 上报由 SDK 后台批量异步完成，不增加请求延迟；应用关停时 flush 缓冲。
"""

from functools import lru_cache
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def langfuse_enabled() -> bool:
    """是否启用 Langfuse（开关开启且双 Key 齐全）。"""
    return bool(
        settings.LANGFUSE_ENABLED
        and settings.LANGFUSE_PUBLIC_KEY
        and settings.LANGFUSE_SECRET_KEY
    )


@lru_cache(maxsize=1)
def _init_client() -> Any | None:
    """初始化全局 Langfuse client（进程内一次；失败缓存 None 不反复重试）。"""
    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )
        logger.info("langfuse tracing enabled: host=%s", settings.LANGFUSE_HOST)
        return client
    except Exception:  # noqa: BLE001 - 观测工具故障不影响主链路
        logger.exception("langfuse init failed, tracing disabled")
        return None


def get_langfuse_handler() -> Any | None:
    """取 LangChain CallbackHandler（未启用/初始化失败返回 None）。

    handler 轻量无状态，按调用创建；trace 归属由 OTel 上下文自动关联。
    """
    if not langfuse_enabled():
        return None
    if _init_client() is None:
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception:  # noqa: BLE001
        logger.exception("langfuse handler create failed")
        return None


def build_trace_metadata(
    *, session_id: str, user_id: str, tenant_id: str, channel: str, trace_id: str
) -> dict[str, Any]:
    """组装 trace 关联字段（langfuse_* 为 SDK 识别的保留键）。

    tags/metadata 不放槽位值等敏感内容；业务 trace_id 进 metadata 供反查。
    """
    return {
        "langfuse_session_id": session_id,
        "langfuse_user_id": user_id,
        "langfuse_tags": [f"tenant:{tenant_id}", f"channel:{channel}"],
        "trace_id": trace_id,
    }


def shutdown_langfuse() -> None:
    """应用关停时 flush 上报缓冲（未启用时为空操作）。"""
    if not langfuse_enabled():
        return
    client = _init_client()
    if client is None:
        return
    try:
        client.shutdown()
    except Exception:  # noqa: BLE001
        logger.exception("langfuse shutdown flush failed")
