"""Customer profile and address repositories."""

import uuid

from sqlalchemy import func, select, update

from app.models.customer import Address, CustomerProfile
from app.repositories.base import BaseRepository


class CustomerProfileRepository(BaseRepository[CustomerProfile]):
    """Data access for customer profiles."""

    model = CustomerProfile

    def get_by_user_id(self, user_id: uuid.UUID) -> CustomerProfile | None:
        stmt = select(CustomerProfile).where(CustomerProfile.user_id == user_id)
        return self.session.scalars(stmt).first()


class AddressRepository(BaseRepository[Address]):
    """Data access for addresses, always scoped to the owning user."""

    model = Address

    def get_for_user(self, address_id: uuid.UUID, user_id: uuid.UUID) -> Address | None:
        stmt = select(Address).where(Address.id == address_id, Address.user_id == user_id)
        return self.session.scalars(stmt).first()

    def list_for_user(self, user_id: uuid.UUID) -> list[Address]:
        """Return the user's addresses, default first, then newest first."""
        stmt = (
            select(Address)
            .where(Address.user_id == user_id)
            .order_by(Address.is_default.desc(), Address.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())

    def count_for_user(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count(Address.id)).where(Address.user_id == user_id)
        return self.session.scalars(stmt).one()

    def clear_default_for_user(
        self, user_id: uuid.UUID, *, except_id: uuid.UUID | None = None
    ) -> None:
        """Unset the default flag for a user's addresses (one may be excluded)."""
        stmt = update(Address).where(
            Address.user_id == user_id,
            Address.is_default.is_(True),
        )
        if except_id is not None:
            stmt = stmt.where(Address.id != except_id)
        self.session.execute(stmt.values(is_default=False))
