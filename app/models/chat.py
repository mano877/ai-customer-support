"""Chat conversation and message models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import now_utc
from app.db.base import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class ChatStatus(enum.StrEnum):
    """Lifecycle states of a chat conversation."""

    ACTIVE = "active"  # customer + AI bot
    ESCALATED = "escalated"  # handoff requested / human agent handling
    RESOLVED = "resolved"  # closed


class ChatMessageSender(enum.StrEnum):
    """Origin of a chat message."""

    CUSTOMER = "customer"
    BOT = "bot"
    AGENT = "agent"
    SYSTEM = "system"


class ChatConversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A customer support chat session with its lifecycle state.

    ``user_id`` is the customer who owns the conversation; ``assigned_agent_id``
    is populated once a support agent claims an escalated conversation.
    Feedback (``rating`` / ``feedback_comment``) is captured once after the
    conversation is resolved.
    """

    __tablename__ = "chat_conversations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    subject: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[ChatStatus] = mapped_column(
        Enum(
            ChatStatus,
            name="chat_status",
            native_enum=False,
            length=32,
            values_callable=lambda members: [member.value for member in members],
        ),
        default=ChatStatus.ACTIVE,
        server_default="active",
        nullable=False,
    )
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handoff_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rating: Mapped[int | None] = mapped_column(Integer)
    feedback_comment: Mapped[str | None] = mapped_column(String(1000))

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChatMessage.position",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ChatConversation id={self.id} status={self.status.value!r}>"


class ChatMessage(UUIDPrimaryKeyMixin, Base):
    """A single message in a conversation (immutable, append-only).

    ``sender_type`` distinguishes customer, AI bot, human agent, and system
    notices. ``position`` is a per-conversation monotonic counter assigned by
    the service — it is the source of truth for chat history ordering, so the
    order never depends on wall-clock precision. The unique constraint on
    (conversation_id, position) makes duplicate positions impossible.
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "position",
            name="uq_chat_messages_conversation_position",
        ),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # Per-conversation insertion order (1-based, assigned by the service).
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    sender_type: Mapped[ChatMessageSender] = mapped_column(
        Enum(
            ChatMessageSender,
            name="chat_message_sender",
            native_enum=False,
            length=32,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
    )
    # The user who authored the message (customer or agent); NULL for bot/system.
    sender_user_id: Mapped[uuid.UUID | None] = mapped_column(
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

    conversation: Mapped[ChatConversation] = relationship(back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ChatMessage id={self.id} sender={self.sender_type.value!r} "
            f"conversation={self.conversation_id}>"
        )
