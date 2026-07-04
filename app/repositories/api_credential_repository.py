"""api_credential 数据访问层。"""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_credential import ApiCredential
from app.repositories.base_repository import BaseRepository


class ApiCredentialRepository(BaseRepository[ApiCredential]):
    """API 凭证 Repository。"""

    def __init__(self) -> None:
        super().__init__(ApiCredential)

    async def get_active_by_key_id(
        self, session: AsyncSession, key_id: str
    ) -> ApiCredential | None:
        """按公开键标识查询生效凭证（未停用且未过期，Stage 13 加 expires_at）。"""
        stmt = select(ApiCredential).where(
            ApiCredential.key_id == key_id,
            ApiCredential.status == "active",
            or_(ApiCredential.expires_at.is_(None), ApiCredential.expires_at > func.now()),
        )
        return await self._first(session, stmt)

    async def list_by_tenant(
        self, session: AsyncSession, tenant_id: str | None = None
    ) -> list[ApiCredential]:
        """列出凭证（CLI 用；tenant 为空列全部）。"""
        stmt = select(ApiCredential).order_by(ApiCredential.created_at.desc())
        if tenant_id:
            stmt = stmt.where(ApiCredential.tenant_id == tenant_id)
        return await self._all(session, stmt)


# 模块级单例
api_credential_repository = ApiCredentialRepository()
