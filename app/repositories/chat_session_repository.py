"""chat_session 数据访问层。"""

from typing import Any

from sqlalchemy import cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
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

    async def list_by_tenant(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ChatSession]:
        """租户维度会话列表（观测面，Stage 29）：可按用户/状态过滤，更新时间倒序。"""
        stmt = select(ChatSession).where(ChatSession.tenant_id == tenant_id)
        if user_id:
            stmt = stmt.where(ChatSession.user_id == user_id)
        if status:
            stmt = stmt.where(ChatSession.status == status)
        stmt = stmt.order_by(ChatSession.updated_at.desc()).limit(limit).offset(offset)
        return await self._all(session, stmt)

    async def merge_metadata(
        self,
        session: AsyncSession,
        tenant_id: str,
        session_id: str,
        patch: dict[str, Any],
        *,
        expect_summary_covered: int | None = None,
    ) -> bool:
        """按顶层键合并 metadata_json（JSONB `||`），不整 dict 覆盖。

        并发安全（Stage 20 摘要写覆盖修复）：
        - 只更新 patch 中出现的键，并发事务写入的其他键（如 locale）不受影响；
        - expect_summary_covered 非 None 时做 CAS：行内 memory_summary_covered
          与预期不符则放弃更新（返回 False）——两个并发摘要任务只有先到者生效，
          后者下一轮按新游标重新增量，不会互相覆盖摘要与游标。
        """
        stmt = (
            update(ChatSession)
            .where(ChatSession.id == session_id, ChatSession.tenant_id == tenant_id)
            .values(
                metadata_json=func.coalesce(ChatSession.metadata_json, cast({}, JSONB)).op(
                    "||"
                )(cast(patch, JSONB))
            )
        )
        if expect_summary_covered is not None:
            # 键不存在时 ->> 为 NULL，coalesce 成 '0' 与初始游标对齐
            stmt = stmt.where(
                func.coalesce(
                    ChatSession.metadata_json["memory_summary_covered"].astext, "0"
                )
                == str(expect_summary_covered)
            )
        result = await session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0) == 1

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
