"""chat_task 数据访问层。"""

from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_task import ChatTask
from app.repositories.base_repository import BaseRepository


class ChatTaskRepository(BaseRepository[ChatTask]):
    """任务表 Repository。"""

    def __init__(self) -> None:
        super().__init__(ChatTask)

    async def get_owned(
        self, session: AsyncSession, tenant_id: str, task_id: str
    ) -> ChatTask | None:
        """按 id 查询并校验租户归属。"""
        task = await self.get_by_id(session, task_id)
        if task is not None and task.tenant_id == tenant_id:
            return task
        return None

    async def abort_open_by_session(
        self, session: AsyncSession, tenant_id: str, session_id: str
    ) -> int:
        """终结会话内所有未完成任务行（Stage 15 修复僵尸行）。

        人工 resolve / 定时关单会清空 dialog_state 的 active_task 引用，
        使 load_session_state 的 TTL 自愈再也够不到这些行——必须在清引用的
        同时把中间态（COLLECTING/CONFIRMING/EXECUTING）标 ABORTED。
        """
        result = await session.execute(
            update(ChatTask)
            .where(
                ChatTask.tenant_id == tenant_id,
                ChatTask.session_id == session_id,
                ChatTask.status.in_(("COLLECTING", "CONFIRMING", "EXECUTING", "SUSPENDED")),
            )
            .values(status="ABORTED")
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def claim_for_execution(
        self, session: AsyncSession, tenant_id: str, task_id: str
    ) -> bool:
        """原子拿执行权（Stage 13 防重放整改）。

        条件更新：仅中间态（CONFIRMING/COLLECTING）可置 EXECUTING，
        受影响行数=1 才算拿到执行权——并发两条「确认」只有一个能赢，
        替代此前「读 status 检查 + 无条件 UPDATE」的非原子两步。
        version 同步 +1，与 ORM 乐观锁口径一致。
        """
        result = await session.execute(
            update(ChatTask)
            .where(
                ChatTask.tenant_id == tenant_id,
                ChatTask.id == task_id,
                ChatTask.status.in_(("CONFIRMING", "COLLECTING")),
            )
            .values(
                status="EXECUTING",
                confirmed_at=datetime.now(timezone.utc),
                version=ChatTask.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        # 显式 expire 身份映射内的实例（口径同 chat_handoff_ticket_repository.claim）
        cached = await session.get(ChatTask, task_id)
        if cached is not None:
            session.expire(cached)
        return int(getattr(result, "rowcount", 0) or 0) == 1


# 模块级单例
chat_task_repository = ChatTaskRepository()
