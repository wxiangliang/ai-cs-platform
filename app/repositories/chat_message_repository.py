"""chat_message 数据访问层。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_message import ChatMessage
from app.repositories.base_repository import BaseRepository


class ChatMessageRepository(BaseRepository[ChatMessage]):
    """消息表 Repository：提供消息的创建、查询。"""

    def __init__(self) -> None:
        super().__init__(ChatMessage)

    async def list_by_session_id(
        self,
        session: AsyncSession,
        tenant_id: str,
        session_id: str,
        limit: int = 100,
    ) -> list[ChatMessage]:
        """按会话 ID 拉取消息历史，按创建时间正序（对话顺序）。"""
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.tenant_id == tenant_id,
                ChatMessage.session_id == session_id,
            )
            .order_by(ChatMessage.created_at.asc())
            .limit(limit)
        )
        return await self._all(session, stmt)

    async def list_history_page(
        self,
        session: AsyncSession,
        tenant_id: str,
        session_id: str,
        limit: int = 20,
        before_id: str | None = None,
    ) -> list[ChatMessage]:
        """历史消息分页：created_at 倒序，游标为消息 ID（取该消息之前的更早消息）。"""
        stmt = select(ChatMessage).where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.session_id == session_id,
        )
        if before_id:
            anchor = await self.get_by_id(session, before_id)
            if anchor is not None and anchor.session_id == session_id:
                stmt = stmt.where(ChatMessage.created_at < anchor.created_at)
        stmt = stmt.order_by(ChatMessage.created_at.desc()).limit(limit)
        return await self._all(session, stmt)


# 模块级单例
chat_message_repository = ChatMessageRepository()
