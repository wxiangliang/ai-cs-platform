"""chat_session 数据访问层。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_session import ChatSession
from app.repositories.base_repository import BaseRepository


class ChatSessionRepository(BaseRepository[ChatSession]):
    """会话表 Repository：提供会话的创建、查询、更新。"""

    def __init__(self) -> None:
        super().__init__(ChatSession)

    async def list_by_user_id(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
        limit: int = 50,
    ) -> list[ChatSession]:
        """查询某租户下某用户的会话列表，按创建时间倒序。"""
        stmt = (
            select(ChatSession)
            .where(ChatSession.tenant_id == tenant_id, ChatSession.user_id == user_id)
            .order_by(ChatSession.created_at.desc())
            .limit(limit)
        )
        return await self._all(session, stmt)

    async def update_status(
        self,
        session: AsyncSession,
        tenant_id: str,
        session_id: str,
        status: str,
    ) -> None:
        """更新会话状态（active/closed/handoff），保持与对话状态机联动。"""
        instance = await self.get_by_id(session, session_id)
        if instance is not None and instance.tenant_id == tenant_id:
            instance.status = status
            await session.flush()


# 模块级单例，业务层统一复用
chat_session_repository = ChatSessionRepository()
