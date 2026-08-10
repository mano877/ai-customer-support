"""Feedback repository."""

import uuid

from sqlalchemy import func, select

from app.models.feedback import Feedback
from app.repositories.base import BaseRepository


class FeedbackRepository(BaseRepository[Feedback]):
    """Data access for customer feedback records."""

    model = Feedback

    def get_for_customer_and_conversation(
        self, customer_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Feedback | None:
        """Fetch the feedback record for a (customer, conversation) pair."""
        stmt = select(Feedback).where(
            Feedback.customer_id == customer_id,
            Feedback.conversation_id == conversation_id,
        )
        return self.session.scalars(stmt).first()

    def list_for_conversation(self, conversation_id: uuid.UUID) -> list[Feedback]:
        """Return the feedback records attached to a conversation.

        Used by the future AI quality/analytics dashboard to correlate a
        conversation's rating with its transcript.
        """
        stmt = (
            select(Feedback)
            .where(Feedback.conversation_id == conversation_id)
            .order_by(Feedback.created_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    def ratings_distribution(self) -> list[tuple[int, int]]:
        """Return (rating, count) pairs for every submitted rating."""
        stmt = (
            select(Feedback.rating, func.count(Feedback.id))
            .group_by(Feedback.rating)
            .order_by(Feedback.rating.asc())
        )
        return [(rating, count) for rating, count in self.session.execute(stmt).all()]
