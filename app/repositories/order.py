"""Order repository."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.order import Order
from app.repositories.base import BaseRepository


class OrderRepository(BaseRepository[Order]):
    """Data access for orders, always scoped to the owning user."""

    model = Order

    def get_for_user(self, order_id: uuid.UUID, user_id: uuid.UUID) -> Order | None:
        """Fetch an order by id, only if it belongs to ``user_id``."""
        stmt = (
            select(Order)
            .where(Order.id == order_id, Order.user_id == user_id)
            .options(selectinload(Order.items))
        )
        return self.session.scalars(stmt).first()

    def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Order]:
        """Return a page of the user's orders, newest first."""
        stmt = (
            select(Order)
            .where(Order.user_id == user_id)
            .options(selectinload(Order.items))
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def count_for_user(self, user_id: uuid.UUID) -> int:
        """Count the user's orders for pagination metadata."""
        stmt = select(func.count(Order.id)).where(Order.user_id == user_id)
        return self.session.scalars(stmt).one()
