"""Product repository."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select

from app.models.product import Product
from app.repositories.base import BaseRepository
from app.utils.like import escape_like


class ProductRepository(BaseRepository[Product]):
    """Data access for the public product catalog."""

    model = Product

    def list_public(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        category: str | None = None,
    ) -> list[Product]:
        """List active products, optionally filtered by category."""
        stmt = (
            select(Product)
            .where(Product.is_active.is_(True))
            .order_by(Product.name.asc(), Product.id.asc())
            .limit(limit)
            .offset(offset)
        )
        if category:
            stmt = stmt.where(func.lower(Product.category) == category.strip().lower())
        return list(self.session.scalars(stmt).all())

    def count_public(self, *, category: str | None = None) -> int:
        """Count active products, optionally filtered by category."""
        stmt = select(func.count(Product.id)).where(Product.is_active.is_(True))
        if category:
            stmt = stmt.where(func.lower(Product.category) == category.strip().lower())
        return self.session.scalars(stmt).one()

    def get_public(self, product_id: uuid.UUID) -> Product | None:
        """Fetch a single active product by id."""
        stmt = select(Product).where(Product.id == product_id, Product.is_active.is_(True))
        return self.session.scalars(stmt).first()

    def _search_filter(self, query: str, category: str | None = None):
        """Build the WHERE clause shared by search and count_search."""
        pattern = f"%{escape_like(query.strip())}%"
        terms = [
            Product.name.ilike(pattern, escape="\\"),
            Product.description.ilike(pattern, escape="\\"),
            Product.category.ilike(pattern, escape="\\"),
            Product.brand.ilike(pattern, escape="\\"),
        ]
        where = [Product.is_active.is_(True), or_(*terms)]
        if category:
            where.append(func.lower(Product.category) == category.strip().lower())
        return where

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        category: str | None = None,
    ) -> list[Product]:
        """Case-insensitive search over name, description, category, and brand."""
        stmt = (
            select(Product)
            .where(*self._search_filter(query, category))
            .order_by(Product.name.asc(), Product.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def count_search(self, query: str, *, category: str | None = None) -> int:
        """Count search matches for pagination metadata."""
        stmt = select(func.count(Product.id)).where(*self._search_filter(query, category))
        return self.session.scalars(stmt).one()

    def recommendations(self, *, limit: int = 10) -> Sequence[Product]:
        """Recommended active products: featured first, then highest rated."""
        stmt = (
            select(Product)
            .where(Product.is_active.is_(True))
            .order_by(
                Product.is_featured.desc(),
                Product.rating.desc(),
                Product.name.asc(),
            )
            .limit(limit)
        )
        return self.session.scalars(stmt).all()
