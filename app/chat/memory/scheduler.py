"""记忆写入调度器：主事务提交后异步执行，回滚时丢弃。"""

import asyncio
from dataclasses import dataclass

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.chat.llm.budget import set_current_tenant
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_PENDING_KEY = "_memory_write_pending"
_HOOKED_KEY = "_memory_write_hooked"
_pending_tasks: set[asyncio.Task[None]] = set()


@dataclass(frozen=True, slots=True)
class MemoryWriteRequest:
    """一次已完成聊天轮次的记忆写入参数。"""

    tenant_id: str
    user_id: str
    session_id: str
    user_text: str
    reply: str
    intent: str | None


def enqueue_memory_after_commit(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    user_text: str,
    reply: str,
    intent: str | None,
) -> None:
    """登记记忆写入；仅主事务提交成功后创建后台任务。

    待执行参数挂在请求级 Session.info 上。事务回滚会清空参数，避免聊天消息未落库时
    长期事实却由独立事务写入；提交后再执行也保证摘要能读取到本轮消息。
    """
    if settings.MEMORY_PROVIDER.lower() == "off":
        return

    sync_session = session.sync_session
    pending: list[MemoryWriteRequest] = sync_session.info.setdefault(_PENDING_KEY, [])
    pending.append(
        MemoryWriteRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            user_text=user_text,
            reply=reply,
            intent=intent,
        )
    )
    if sync_session.info.get(_HOOKED_KEY):
        return

    sync_session.info[_HOOKED_KEY] = True
    event.listen(sync_session, "after_commit", _drain_after_commit)
    event.listen(sync_session, "after_rollback", _discard_after_rollback)


def _drain_after_commit(sync_session: Session) -> None:
    """提交回调：取出本事务登记项并调度，重复 commit 不会重复执行。"""
    pending = list(sync_session.info.get(_PENDING_KEY) or [])
    sync_session.info[_PENDING_KEY] = []
    for request in pending:
        _schedule(request)


def _discard_after_rollback(sync_session: Session) -> None:
    """回滚回调：清空本事务登记项，不产生未提交轮次的记忆。"""
    sync_session.info[_PENDING_KEY] = []


def _schedule(request: MemoryWriteRequest) -> None:
    """在当前事件循环创建任务并持有强引用。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("memory write skipped: no running event loop")
        return

    task = loop.create_task(
        _remember_safe(request), name=f"memory:{request.session_id}"
    )
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


async def _remember_safe(request: MemoryWriteRequest) -> None:
    """执行 Provider 写入；失败只记日志，不影响已提交的聊天轮次。"""
    try:
        from app.chat.memory.factory import get_memory_provider

        # after_commit 创建新 Task 时显式恢复预算租户归属，避免依赖调用栈上下文。
        set_current_tenant(request.tenant_id)
        await get_memory_provider().remember(
            request.tenant_id,
            request.user_id,
            request.session_id,
            request.user_text,
            request.reply,
            request.intent,
        )
    except Exception:  # noqa: BLE001 - 记忆是 best-effort 增强层
        logger.exception("memory remember failed: session=%s", request.session_id)


async def wait_pending_memory_tasks(timeout: float = 5.0) -> None:
    """关停时等待记忆任务；超时后取消，避免继续访问已关闭的资源池。"""
    pending = [task for task in _pending_tasks if not task.done()]
    if not pending:
        return

    logger.info("等待 %d 个在途记忆任务收敛（<=%.0fs）...", len(pending), timeout)
    _, unfinished = await asyncio.wait(pending, timeout=timeout)
    if not unfinished:
        return

    logger.warning("取消 %d 个超时记忆任务", len(unfinished))
    for task in unfinished:
        task.cancel()
    await asyncio.gather(*unfinished, return_exceptions=True)
