"""Product endpoints: public catalog reads."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.product import ProductListResponse, ProductResponse
from app.services.product import ProductService

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=ProductListResponse)
def list_products(
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None, max_length=128),
) -> ProductListResponse:
    """List active products, paginated and optionally filtered by category."""
    return ProductService(db).list_products(limit=limit, offset=offset, category=category)


# Static routes must be declared before /products/{product_id}.
@router.get("/search", response_model=ProductListResponse)
def search_products(
    db: Annotated[Session, Depends(get_db)],
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None, max_length=128),
) -> ProductListResponse:
    """Search active products by name, description, category, or brand."""
    return ProductService(db).search_products(q, limit=limit, offset=offset, category=category)


@router.get("/recommendations", response_model=list[ProductResponse])
def product_recommendations(
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=10, ge=1, le=50),
) -> list[ProductResponse]:
    """Return recommended products (featured first, then highest rated)."""
    return ProductService(db).get_recommendations(limit=limit)


@router.get("/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> ProductResponse:
    """Fetch a single active product by id."""
    return ProductService(db).get_product(product_id)
