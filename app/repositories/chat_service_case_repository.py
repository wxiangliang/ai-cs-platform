"""chat_service_case 数据访问层（Stage 34）。"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_service_case import ACTIVE_CASE_STATUSES, ChatServiceCase
from app.repositories.base_repository import BaseRepository


class ChatServiceCaseRepository(BaseRepository[ChatServiceCase]):
    """服务 Case Repository。"""

    def __init__(self) -> None:
        super().__init__(ChatServiceCase)

    async def get_active_by_user_type(
        self, session: AsyncSession, tenant_id: str, user_id: str, case_type: str
    ) -> ChatServiceCase | None:
        """取同客户同类型的活跃 Case（幂等合并依据）。"""
        stmt = select(ChatServiceCase).where(
            ChatServiceCase.tenant_id == tenant_id,
            ChatServiceCase.user_id == user_id,
            ChatServiceCase.case_type == case_type,
            ChatServiceCase.status.in_(ACTIVE_CASE_STATUSES),
        )
        return await self._first(session, stmt)

    async def get_owned(
        self, session: AsyncSession, tenant_id: str, case_id: str
    ) -> ChatServiceCase | None:
        stmt = select(ChatServiceCase).where(
            ChatServiceCase.tenant_id == tenant_id,
            ChatServiceCase.id == case_id,
        )
        return await self._first(session, stmt)

    async def list_by_tenant(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        status: str | None = None,
        case_type: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ChatServiceCase], int]:
        """Case 队列（过滤 + 分页），按更新时间倒序。"""
        conds = [ChatServiceCase.tenant_id == tenant_id]
        if status:
            conds.append(ChatServiceCase.status == status)
        if case_type:
            conds.append(ChatServiceCase.case_type == case_type)
        if user_id:
            conds.append(ChatServiceCase.user_id == user_id)
        total = (
            await session.execute(
                select(func.count()).select_from(ChatServiceCase).where(*conds)
            )
        ).scalar_one()
        stmt = (
            select(ChatServiceCase)
            .where(*conds)
            .order_by(ChatServiceCase.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return await self._all(session, stmt), int(total)

    async def list_sla_breached(
        self, session: AsyncSession, now: datetime, limit: int = 200
    ) -> list[ChatServiceCase]:
        """全租户超时活跃 Case（cron 扫描；ESCALATED 已升级不重复取）。"""
        stmt = (
            select(ChatServiceCase)
            .where(
                ChatServiceCase.status.in_(
                    tuple(s for s in ACTIVE_CASE_STATUSES if s != "ESCALATED")
                ),
                ChatServiceCase.sla_due_at.is_not(None),
                ChatServiceCase.sla_due_at < now,
            )
            .limit(limit)
        )
        return await self._all(session, stmt)


# 模块级单例
chat_service_case_repository = ChatServiceCaseRepository()
