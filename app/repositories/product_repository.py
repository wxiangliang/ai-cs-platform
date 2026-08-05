"""product_item 数据访问层。"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_item import ProductItem
from app.repositories.base_repository import BaseRepository


class ProductRepository(BaseRepository[ProductItem]):
    """商品表 Repository。"""

    def __init__(self) -> None:
        super().__init__(ProductItem)

    async def list_by_tenant(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ProductItem]:
        """管理列表（Stage 29 批 3）：全状态，可按名称/编码关键词过滤，更新时间倒序。"""
        stmt = select(ProductItem).where(ProductItem.tenant_id == tenant_id)
        if keyword:
            stmt = stmt.where(
                or_(
                    ProductItem.name.ilike(f"%{keyword}%"),
                    ProductItem.product_code.ilike(f"%{keyword}%"),
                )
            )
        stmt = stmt.order_by(ProductItem.updated_at.desc()).limit(limit).offset(offset)
        return await self._all(session, stmt)

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

    async def search_by_constraints(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        category_keyword: str,
        budget_max_cents: int | None = None,
        limit: int = 4,
    ) -> list[ProductItem]:
        """选品硬约束过滤（Stage 32 红线：不满足硬约束的商品不进候选）。

        SQL 层保证：上架 + 有货 + 品类/名称匹配 + 预算内（给了预算时无价格
        的商品也排除——无法证明满足硬约束就不展示），价格升序（无商业权重）。
        """
        stmt = select(ProductItem).where(
            ProductItem.tenant_id == tenant_id,
            ProductItem.status == "active",
            ProductItem.stock > 0,
            or_(
                ProductItem.category.ilike(f"%{category_keyword}%"),
                ProductItem.name.ilike(f"%{category_keyword}%"),
            ),
        )
        if budget_max_cents is not None:
            stmt = stmt.where(
                ProductItem.price_cents.is_not(None),
                ProductItem.price_cents <= budget_max_cents,
            )
        stmt = stmt.order_by(ProductItem.price_cents.asc().nulls_last()).limit(limit)
        return await self._all(session, stmt)

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
