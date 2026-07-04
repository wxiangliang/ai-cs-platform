"""chat_decision_log 数据访问层。

决策日志只写不改，提供创建与按会话查询。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_decision_log import ChatDecisionLog
from app.repositories.base_repository import BaseRepository


class ChatDecisionLogRepository(BaseRepository[ChatDecisionLog]):
    """决策日志表 Repository：提供创建与按会话查询。"""

    def __init__(self) -> None:
        super().__init__(ChatDecisionLog)

    async def list_by_session_id(
        self,
        session: AsyncSession,
        tenant_id: str,
        session_id: str,
        limit: int = 100,
    ) -> list[ChatDecisionLog]:
        """按会话 ID 拉取决策日志，按创建时间正序。"""
        stmt = (
            select(ChatDecisionLog)
            .where(
                ChatDecisionLog.tenant_id == tenant_id,
                ChatDecisionLog.session_id == session_id,
            )
            .order_by(ChatDecisionLog.created_at.asc())
            .limit(limit)
        )
        return await self._all(session, stmt)


# 模块级单例
chat_decision_log_repository = ChatDecisionLogRepository()
