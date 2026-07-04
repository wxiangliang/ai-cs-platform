"""ProductProvider 协议与本地实现。

协议化的意义：真实商品系统（HTTP/RPC）对接时只需新增一个 Provider 实现，
聊天链路（product_answer 节点）不感知数据来自本地表还是外部系统。
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

import jieba
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.product_repository import product_repository


@dataclass
class ProductInfo:
    """商品信息（后端无关的统一结构）。"""

    id: str
    name: str
    price_cents: int | None
    stock: int | None
    category: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    description: str | None = None

    @property
    def price_text(self) -> str:
        """价格展示文本（分转元）。"""
        if self.price_cents is None:
            return "以页面标价为准"
        return f"{self.price_cents / 100:.2f} 元"

    @property
    def stock_text(self) -> str:
        if self.stock is None:
            return "以页面显示为准"
        return f"现货 {self.stock} 件" if self.stock > 0 else "暂时缺货"


class ProductProvider(Protocol):
    """商品查询协议。tenant_id 必传。"""

    async def search(
        self, session: AsyncSession, tenant_id: str, query: str, limit: int = 5
    ) -> list[ProductInfo]:
        """按商品名称线索检索。"""
        ...


def _keywords(query: str, max_terms: int = 6) -> list[str]:
    """jieba 分词提取检索词（≥2 字符或含字母数字）。"""
    seen: list[str] = []
    for token in jieba.cut(query or ""):
        token = token.strip()
        if token and (len(token) >= 2 or token.isalnum()) and token not in seen:
            seen.append(token)
    return seen[:max_terms]


def _normalize(text: str) -> str:
    """归一化商品名比对口径：去空白、转小写。"""
    return "".join((text or "").split()).lower()


class LocalProductProvider:
    """本地商品表实现（默认；真实商品系统就绪后替换）。"""

    name = "local"

    async def search(
        self, session: AsyncSession, tenant_id: str, query: str, limit: int = 5
    ) -> list[ProductInfo]:
        # 精确编码优先（修复：product_id 槽位/用户直接给编码时，此前一律走名称
        # ILIKE 模糊匹配常查不出）——命中即唯一返回；未命中回落名称检索
        code = (query or "").strip()
        if code:
            item = await product_repository.get_by_code(session, tenant_id, code)
            if item is not None:
                return [
                    ProductInfo(
                        id=item.id, name=item.name, price_cents=item.price_cents,
                        stock=item.stock, category=item.category,
                        attrs=item.attrs_json or {}, description=item.description,
                    )
                ]
        scored = await product_repository.search_by_name(
            session, tenant_id, _keywords(query), limit=limit
        )
        # 唯一命中消歧：名称精确相等（归一化后）或最高分严格领先 → 只返回第一个，
        # 避免「凉风空调X1」这类明确指名被当作多候选反问用户。
        # 注意不做子串折叠：「凉风空调」是 X1/X2 的公共前缀，必须保持多候选让用户选
        if scored:
            q = _normalize(query)
            exact = bool(q) and q == _normalize(scored[0][0].name)
            strictly_best = len(scored) == 1 or scored[0][1] > scored[1][1]
            if exact or strictly_best:
                scored = scored[:1]
        return [
            ProductInfo(
                id=item.id,
                name=item.name,
                price_cents=item.price_cents,
                stock=item.stock,
                category=item.category,
                attrs=item.attrs_json or {},
                description=item.description,
            )
            for item, _score in scored
        ]


# 模块级单例（工厂预留：接真实系统时在此按配置切换实现）
product_provider: ProductProvider = LocalProductProvider()
