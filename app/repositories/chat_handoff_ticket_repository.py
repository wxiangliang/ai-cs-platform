"""chat_handoff_ticket 数据访问层。"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_handoff_ticket import ChatHandoffTicket
from app.repositories.base_repository import BaseRepository

OPEN_STATUSES = ("PENDING", "ASSIGNED")


class ChatHandoffTicketRepository(BaseRepository[ChatHandoffTicket]):
    """转人工工单 Repository。"""

    def __init__(self) -> None:
        super().__init__(ChatHandoffTicket)

    async def get_open_by_session(
        self, session: AsyncSession, tenant_id: str, session_id: str
    ) -> ChatHandoffTicket | None:
        """取会话当前未关闭工单（幂等建单依据）。"""
        stmt = select(ChatHandoffTicket).where(
            ChatHandoffTicket.tenant_id == tenant_id,
            ChatHandoffTicket.session_id == session_id,
            ChatHandoffTicket.status.in_(OPEN_STATUSES),
        )
        return await self._first(session, stmt)

    async def get_owned(
        self, session: AsyncSession, tenant_id: str, ticket_id: str
    ) -> ChatHandoffTicket | None:
        """按 id 查询并校验租户归属。"""
        ticket = await self.get_by_id(session, ticket_id)
        if ticket is not None and ticket.tenant_id == tenant_id:
            return ticket
        return None

    async def list_page(
        self,
        session: AsyncSession,
        tenant_id: str,
        status: str | None = None,
        limit: int = 20,
        before_id: str | None = None,
    ) -> list[ChatHandoffTicket]:
        """工单队列分页（created_at 倒序，PENDING 优先由调用方按 status 过滤）。"""
        stmt = select(ChatHandoffTicket).where(ChatHandoffTicket.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(ChatHandoffTicket.status == status)
        if before_id:
            anchor = await self.get_by_id(session, before_id)
            if anchor is not None and anchor.tenant_id == tenant_id:
                stmt = stmt.where(ChatHandoffTicket.created_at < anchor.created_at)
        stmt = stmt.order_by(ChatHandoffTicket.created_at.desc()).limit(limit)
        return await self._all(session, stmt)

    async def count_pending_before(
        self, session: AsyncSession, tenant_id: str, ticket_id: str
    ) -> int:
        """该工单在 PENDING 队列中的位置（含自身，按 created_at 先后；不存在返回 0）。"""
        ticket = await self.get_owned(session, tenant_id, ticket_id)
        if ticket is None:
            return 0
        stmt = (
            select(func.count())
            .select_from(ChatHandoffTicket)
            .where(
                ChatHandoffTicket.tenant_id == tenant_id,
                ChatHandoffTicket.status == "PENDING",
                ChatHandoffTicket.created_at <= ticket.created_at,
            )
        )
        return int((await session.execute(stmt)).scalar_one())

    async def claim(
        self, session: AsyncSession, tenant_id: str, ticket_id: str, assignee: str
    ) -> bool:
        """认领工单：仅 PENDING 可认领，条件更新防并发抢单（返回是否成功）。"""
        result = await session.execute(
            update(ChatHandoffTicket)
            .where(
                ChatHandoffTicket.tenant_id == tenant_id,
                ChatHandoffTicket.id == ticket_id,
                ChatHandoffTicket.status == "PENDING",
            )
            .values(status="ASSIGNED", assignee=assignee)
            .execution_options(synchronize_session=False)
        )
        # Core UPDATE 绕过 ORM：显式 expire 身份映射内的实例，
        # 后续访问必然回源重载（expire_on_commit=False 下同步策略行为不稳定）
        cached = await session.get(ChatHandoffTicket, ticket_id)
        if cached is not None:
            session.expire(cached)
        return int(getattr(result, "rowcount", 0) or 0) == 1


# 模块级单例
chat_handoff_ticket_repository = ChatHandoffTicketRepository()
