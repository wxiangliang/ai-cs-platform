"""product_item 数据访问层。"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_item import ProductItem
from app.repositories.base_repository import BaseRepository


class ProductRepository(BaseRepository[ProductItem]):
    """商品表 Repository。"""

    def __init__(self) -> None:
        super().__init__(ProductItem)

    async def search_by_name(
        self,
        session: AsyncSession,
        tenant_id: str,
        keywords: list[str],
        limit: int = 5,
    ) -> list[tuple[ProductItem, int]]:
        """按名称关键词检索生效商品：任一关键词 ILIKE 命中。

        返回 (商品, 命中词数) 按分数降序——分数留给调用方做唯一命中消歧
        （「凉风空调X1」应唯一命中 X1，而不是把 X2 也当候选）。
        """
        if not keywords:
            return []
        conditions = [ProductItem.name.ilike(f"%{kw}%") for kw in keywords]
        stmt = (
            select(ProductItem)
            .where(
                ProductItem.tenant_id == tenant_id,
                ProductItem.status == "active",
                or_(*conditions),
            )
            .limit(limit * 3)
        )
        items = await self._all(session, stmt)
        scored = [
            (item, sum(1 for kw in keywords if kw.lower() in item.name.lower()))
            for item in items
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    async def get_by_code(
        self, session: AsyncSession, tenant_id: str, product_code: str
    ) -> ProductItem | None:
        """按业务商品编码查询。"""
        stmt = select(ProductItem).where(
            ProductItem.tenant_id == tenant_id,
            ProductItem.product_code == product_code,
            ProductItem.status == "active",
        )
        return await self._first(session, stmt)


# 模块级单例
product_repository = ProductRepository()
