"""Product service: public catalog reads."""

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.repositories.product import ProductRepository
from app.schemas.product import ProductListResponse, ProductResponse


class ProductService:
    """Business logic for the product catalog."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = ProductRepository(db)

    def list_products(
        self, *, limit: int = 20, offset: int = 0, category: str | None = None
    ) -> ProductListResponse:
        items = self.products.list_public(limit=limit, offset=offset, category=category)
        total = self.products.count_public(category=category)
        return ProductListResponse(
            items=[ProductResponse.model_validate(product) for product in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_product(self, product_id: uuid.UUID) -> ProductResponse:
        product = self.products.get_public(product_id)
        if product is None:
            raise NotFoundError("Product not found")
        return ProductResponse.model_validate(product)

    def search_products(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        category: str | None = None,
    ) -> ProductListResponse:
        items = self.products.search(query, limit=limit, offset=offset, category=category)
        total = self.products.count_search(query, category=category)
        return ProductListResponse(
            items=[ProductResponse.model_validate(product) for product in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_recommendations(self, *, limit: int = 10) -> list[ProductResponse]:
        products = self.products.recommendations(limit=limit)
        return [ProductResponse.model_validate(product) for product in products]
