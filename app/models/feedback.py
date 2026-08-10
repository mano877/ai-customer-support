"""Feedback model: customer ratings on chat conversations."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class Feedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A customer's rating of one of their chat conversations.

    ``conversation_id`` references the existing ``chat_conversations`` table —
    no duplicate conversation data is stored. The rating is validated by
    Pydantic (1–5) and enforced at the database level by a CHECK constraint.
    One feedback record per (customer, conversation): resubmitting feedback
    updates the existing record so analytics never double-count a conversation.
    """

    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint(
            "rating BETWEEN 1 AND 5",
            name="ck_feedback_rating_range",
        ),
        UniqueConstraint(
            "customer_id",
            "conversation_id",
            name="uq_feedback_customer_conversation",
        ),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(1000))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Feedback id={self.id} rating={self.rating} "
            f"conversation={self.conversation_id}>"
        )
