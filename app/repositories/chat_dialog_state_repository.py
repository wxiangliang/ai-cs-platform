"""chat_dialog_state 数据访问层。

对话状态每个会话唯一，提供按 session 查询与 upsert（不存在则插入，存在则更新）。
upsert 在已存在记录上修改字段时，会触发模型的乐观锁版本校验。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_dialog_state import ChatDialogState
from app.repositories.base_repository import BaseRepository


class ChatDialogStateRepository(BaseRepository[ChatDialogState]):
    """对话状态表 Repository。"""

    def __init__(self) -> None:
        super().__init__(ChatDialogState)

    async def get_by_session_id(
        self,
        session: AsyncSession,
        tenant_id: str,
        session_id: str,
    ) -> ChatDialogState | None:
        """按 (tenant_id, session_id) 查询会话当前状态，不存在返回 None。"""
        stmt = select(ChatDialogState).where(
            ChatDialogState.tenant_id == tenant_id,
            ChatDialogState.session_id == session_id,
        )
        return await self._first(session, stmt)

    async def upsert_by_session_id(
        self,
        session: AsyncSession,
        tenant_id: str,
        session_id: str,
        **fields: Any,
    ) -> ChatDialogState:
        """按会话 ID upsert 状态。

        - 不存在：插入一条新状态记录；
        - 已存在：更新传入字段（flush 时触发乐观锁版本校验，
          并发冲突会抛 StaleDataError，交由上层处理重试或报错）。
        """
        instance = await self.get_by_session_id(session, tenant_id, session_id)
        if instance is None:
            instance = ChatDialogState(
                tenant_id=tenant_id,
                session_id=session_id,
                **fields,
            )
            session.add(instance)
        else:
            for key, value in fields.items():
                setattr(instance, key, value)
        await session.flush()
        return instance


# 模块级单例
chat_dialog_state_repository = ChatDialogStateRepository()
