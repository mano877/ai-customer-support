"""Notification model."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class NotificationType(enum.StrEnum):
    """Supported notification categories.

    ``AI_HANDOFF`` is raised when the Chat → human handoff happens (or the AI
    Service creates a ticket it could not resolve); ``SYSTEM`` covers generic
    account/service notices.
    """

    ORDER_SHIPPED = "order_shipped"
    ORDER_DELIVERED = "order_delivered"
    REFUND_COMPLETED = "refund_completed"
    TICKET_CREATED = "ticket_created"
    TICKET_UPDATED = "ticket_updated"
    TICKET_ASSIGNED = "ticket_assigned"
    PAYMENT_SUCCESSFUL = "payment_successful"
    AI_HANDOFF = "ai_handoff"
    SYSTEM = "system"


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An in-app notification addressed to a single user.

    ``related_entity_type`` / ``related_entity_id`` optionally point at the
    domain object that caused the notification (e.g. an order, a ticket, a chat
    conversation); both are set together or both are NULL. ``is_read`` +
    ``read_at`` track the read state (``read_at`` is set the first time the
    notification is marked read and never reset).
    """

    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    type: Mapped[NotificationType] = mapped_column(
        Enum(
            NotificationType,
            name="notification_type",
            native_enum=False,
            length=32,
            values_callable=lambda members: [member.value for member in members],
        ),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("0"),
        index=True,
        nullable=False,
    )
    related_entity_type: Mapped[str | None] = mapped_column(String(64))
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Notification id={self.id} type={self.type.value!r} "
            f"read={self.is_read}>"
        )
