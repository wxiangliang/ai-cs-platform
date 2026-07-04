"""商品管理 API（管理面，本地商品库 LocalProductProvider 的数据维护入口）。

对接真实商品系统后本接口仅用于联调数据。
临时保护同 kb：配置 KB_ADMIN_TOKEN 后需带 X-KB-Admin-Token 头。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_admin, resolve_tenant_id
from app.core.responses import success_response
from app.db.session import get_db_session
from app.repositories.product_repository import product_repository

router = APIRouter(prefix="/api/product", tags=["product"])


class ProductUpsertRequest(BaseModel):
    """创建/更新商品请求。"""

    tenant_id: str | None = Field(default=None, max_length=64, description="鉴权开启后忽略")
    name: str = Field(..., min_length=1, max_length=256)
    product_code: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=64)
    price_cents: int | None = Field(default=None, ge=0, description="价格（分）")
    stock: int | None = Field(default=None, ge=0)
    attrs: dict | None = None
    description: str | None = None


@router.post("/items")
async def upsert_product(
    body: ProductUpsertRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """创建/更新商品（有 product_code 且已存在则更新）。"""
    tenant_id = resolve_tenant_id(auth, body.tenant_id)
    item = None
    if body.product_code:
        item = await product_repository.get_by_code(db, tenant_id, body.product_code)
    if item is not None:
        await product_repository.update(
            db, item, name=body.name, category=body.category,
            price_cents=body.price_cents, stock=body.stock,
            attrs_json=body.attrs, description=body.description, status="active",
        )
    else:
        item = await product_repository.create(
            db, tenant_id=tenant_id, product_code=body.product_code,
            name=body.name, category=body.category,
            price_cents=body.price_cents, stock=body.stock,
            attrs_json=body.attrs, description=body.description,
        )
    return success_response(data={"id": item.id, "name": item.name})
