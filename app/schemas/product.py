"""Product-related schemas."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductResponse(BaseModel):
    """Public representation of a catalog product."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: str
    description: str | None = None
    category: str
    brand: str | None = None
    price: Decimal
    currency: str
    stock_quantity: int
    is_featured: bool
    rating: Decimal
    review_count: int
    created_at: datetime
    updated_at: datetime


class ProductListResponse(BaseModel):
    """Paginated product listing."""

    items: list[ProductResponse]
    total: int
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
