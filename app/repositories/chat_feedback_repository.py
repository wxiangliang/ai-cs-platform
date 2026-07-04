"""chat_feedback 数据访问层。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_feedback import ChatFeedback
from app.repositories.base_repository import BaseRepository


class ChatFeedbackRepository(BaseRepository[ChatFeedback]):
    """用户反馈 Repository。"""

    def __init__(self) -> None:
        super().__init__(ChatFeedback)

    async def get_by_message(
        self, session: AsyncSession, tenant_id: str, message_id: str
    ) -> ChatFeedback | None:
        """按消息取已有反馈（幂等：重复评价更新而非报错）。"""
        stmt = select(ChatFeedback).where(
            ChatFeedback.tenant_id == tenant_id,
            ChatFeedback.message_id == message_id,
        )
        return await self._first(session, stmt)


# 模块级单例
chat_feedback_repository = ChatFeedbackRepository()
