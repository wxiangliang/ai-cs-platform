"""chat_tool_call 数据访问层（append-only 审计）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_tool_call import ChatToolCall
from app.repositories.base_repository import BaseRepository


class ChatToolCallRepository(BaseRepository[ChatToolCall]):
    """工具调用审计表 Repository。"""

    def __init__(self) -> None:
        super().__init__(ChatToolCall)

    async def list_by_session_id(
        self,
        session: AsyncSession,
        tenant_id: str,
        session_id: str,
        limit: int = 100,
    ) -> list[ChatToolCall]:
        """按会话拉取工具调用审计（观测面，Stage 29），时间正序。"""
        stmt = (
            select(ChatToolCall)
            .where(
                ChatToolCall.tenant_id == tenant_id,
                ChatToolCall.session_id == session_id,
            )
            .order_by(ChatToolCall.created_at.asc())
            .limit(limit)
        )
        return await self._all(session, stmt)


# 模块级单例
chat_tool_call_repository = ChatToolCallRepository()
