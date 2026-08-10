"""Support ticket and ticket comment models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import now_utc
from app.db.base import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class TicketStatus(enum.StrEnum):
    """Lifecycle states of a support ticket."""

    OPEN = "open"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    WAITING_FOR_CUSTOMER = "waiting_for_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(enum.StrEnum):
    """Priority of a support ticket."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Ticket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A customer support ticket.

    ``user_id`` is the customer who filed the ticket; ``assigned_agent_id`` is
    the support agent currently handling it (NULL until claimed). ``category``
    is a free-text grouping (e.g. "billing", "shipping"). ``conversation_id``
    optionally links the ticket to the chat conversation that led to it, so the
    Chat → Ticket handoff can preserve full context.
    """

    __tablename__ = "tickets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    # The chat conversation that caused this ticket (Chat → Ticket handoff).
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("chat_conversations.id", ondelete="SET NULL"),
        index=True,
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        Enum(
            TicketStatus,
            name="ticket_status",
            native_enum=False,
            length=32,
            values_callable=lambda members: [member.value for member in members],
        ),
        default=TicketStatus.OPEN,
        server_default="open",
        nullable=False,
    )
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(
            TicketPriority,
            name="ticket_priority",
            native_enum=False,
            length=32,
            values_callable=lambda members: [member.value for member in members],
        ),
        default=TicketPriority.MEDIUM,
        server_default="medium",
        nullable=False,
    )
    category: Mapped[str | None] = mapped_column(String(64))
    resolution_notes: Mapped[str | None] = mapped_column(Text)

    comments: Mapped[list[TicketComment]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: [
            TicketComment.created_at.asc(),
            TicketComment.id.asc(),
        ],
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Ticket id={self.id} status={self.status.value!r} "
            f"priority={self.priority.value!r}>"
        )


class TicketComment(UUIDPrimaryKeyMixin, Base):
    """A single comment on a ticket (customer, agent, or admin authored).

    ``author_id`` references the user who wrote it; it is kept when the user is
    deleted (SET NULL) so ticket history survives account removal.
    """

    __tablename__ = "ticket_comments"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=now_utc,
        nullable=False,
    )

    ticket: Mapped[Ticket] = relationship(back_populates="comments")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TicketComment id={self.id} ticket={self.ticket_id}>"
