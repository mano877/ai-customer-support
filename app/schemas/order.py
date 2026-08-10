"""Order-related schemas."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.order import OrderStatus


class OrderItemCreate(BaseModel):
    """A requested line item when placing an order."""

    product_id: uuid.UUID
    quantity: int = Field(ge=1, le=99)


class OrderCreate(BaseModel):
    """Payload for POST /orders.

    ``address_id`` is optional: when omitted, the user's default address is
    used. At least one item is required.
    """

    items: list[OrderItemCreate] = Field(min_length=1, max_length=50)
    address_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=500)


class OrderItemResponse(BaseModel):
    """A line item as stored on an order (snapshot at purchase time)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID | None = None
    sku: str
    name: str
    unit_price: Decimal
    quantity: int
    line_total: Decimal


class OrderResponse(BaseModel):
    """Full representation of an order."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    status: OrderStatus
    items_subtotal: Decimal
    shipping_cost: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    currency: str
    shipping_address: dict[str, str] | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemResponse]


class OrderListResponse(BaseModel):
    """Paginated order listing."""

    items: list[OrderResponse]
    total: int
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class TrackingEventResponse(BaseModel):
    """A single tracking event."""

    status: str
    at: datetime
    description: str


class OrderTrackingResponse(BaseModel):
    """Tracking view of an order."""

    order_id: uuid.UUID
    order_number: str
    status: OrderStatus
    tracking_number: str | None = None
    carrier: str | None = None
    estimated_delivery: date | None = None
    events: list[TrackingEventResponse]
