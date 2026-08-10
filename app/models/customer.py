"""Customer profile and address models."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class CustomerProfile(TimestampMixin, Base):
    """One-to-one profile extending the User record with customer-specific data."""

    __tablename__ = "customer_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(32))
    marketing_opt_in: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    preferred_language: Mapped[str] = mapped_column(
        String(16), default="en", server_default="en", nullable=False
    )
    avatar_url: Mapped[str | None] = mapped_column(String(512))

    user: Mapped[User] = relationship(back_populates="customer_profile")


class Address(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A shipping/billing address owned by a user."""

    __tablename__ = "addresses"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    label: Mapped[str] = mapped_column(
        String(32), default="Home", server_default="Home", nullable=False
    )
    recipient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    street: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str | None] = mapped_column(String(128))
    postal_code: Mapped[str] = mapped_column(String(32), nullable=False)
    country: Mapped[str] = mapped_column(
        String(2), default="US", server_default="US", nullable=False
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="addresses")
