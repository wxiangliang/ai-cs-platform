"""customer_journey 数据访问层（Stage 38）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer_journey import CustomerJourney
from app.repositories.base_repository import BaseRepository


class CustomerJourneyRepository(BaseRepository[CustomerJourney]):
    """客户旅程 Repository。"""

    def __init__(self) -> None:
        super().__init__(CustomerJourney)

    async def get_by_user(
        self, session: AsyncSession, tenant_id: str, user_id: str
    ) -> CustomerJourney | None:
        stmt = select(CustomerJourney).where(
            CustomerJourney.tenant_id == tenant_id,
            CustomerJourney.user_id == user_id,
        )
        return await self._first(session, stmt)


# 模块级单例
customer_journey_repository = CustomerJourneyRepository()
