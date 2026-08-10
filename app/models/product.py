"""Product model."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A catalog product (public read-only; seeded/managed out-of-band)."""

    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    brand: Mapped[str | None] = mapped_column(String(128))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), default="USD", server_default="USD", nullable=False
    )
    stock_quantity: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("1"), nullable=False
    )
    is_featured: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    # Aggregated review metrics (updated by the Feedback module).
    rating: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=Decimal("0.00"), server_default=text("0"), nullable=False
    )
    review_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Product id={self.id} sku={self.sku!r} name={self.name!r}>"
