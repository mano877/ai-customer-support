"""Order models."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, Enum, ForeignKey, Integer, Numeric, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class OrderStatus(enum.StrEnum):
    """Lifecycle states of an order."""

    PENDING = "pending"
    PAID = "paid"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURN_REQUESTED = "return_requested"
    RETURNED = "returned"


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A customer order with a snapshot of the shipping address and line items.

    Line items and the shipping address are snapshots taken at purchase time so
    order history stays accurate even if products or addresses change later.
    """

    __tablename__ = "orders"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    order_number: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(
            OrderStatus,
            name="order_status",
            native_enum=False,
            length=32,
            values_callable=lambda members: [member.value for member in members],
        ),
        default=OrderStatus.PENDING,
        server_default="pending",
        nullable=False,
    )
    items_subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    shipping_cost: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0.00"), server_default=text("0"), nullable=False
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0.00"), server_default=text("0"), nullable=False
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), default="USD", server_default="USD", nullable=False
    )
    shipping_address: Mapped[dict[str, str] | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)

    # Tracking details (carrier fields are set by future fulfillment flows).
    tracking_number: Mapped[str | None] = mapped_column(String(64))
    carrier: Mapped[str | None] = mapped_column(String(64))
    estimated_delivery: Mapped[date | None] = mapped_column(Date)
    tracking_events: Mapped[list[dict[str, str]] | None] = mapped_column(JSON)

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="OrderItem.id",
    )

    def append_tracking_event(self, status: str, description: str) -> None:
        """Record a tracking event (append-only history)."""
        events = list(self.tracking_events or [])
        events.append(
            {
                "status": status,
                "at": datetime.now(UTC).isoformat(),
                "description": description,
            }
        )
        self.tracking_events = events

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Order id={self.id} number={self.order_number!r} status={self.status.value!r}>"


class OrderItem(UUIDPrimaryKeyMixin, Base):
    """A single line of an order, with product details frozen at purchase time."""

    __tablename__ = "order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # Nullable so catalog changes never break order history. Indexed for the
    # stock-restore lookups performed when orders are cancelled.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        index=True,
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<OrderItem id={self.id} sku={self.sku!r} qty={self.quantity}>"
