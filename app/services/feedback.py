"""Feedback service: submission, ownership checks, and summary aggregation.

The summary is computed here in a single place so the future AI quality /
analytics dashboard can consume ``get_summary`` directly — or drill into a
conversation's rating via ``FeedbackRepository.list_for_conversation`` —
without changing the API architecture. No AI analysis is performed yet.
"""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.feedback import Feedback
from app.models.user import User
from app.repositories.chat import ChatConversationRepository
from app.repositories.feedback import FeedbackRepository
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackResponse,
    FeedbackSummary,
)

# A rating of 4 or 5 counts as positive; 1 or 2 as negative; 3 is neutral.
POSITIVE_MIN_RATING = 4
NEGATIVE_MAX_RATING = 2
RATING_SCALE = (1, 2, 3, 4, 5)


class FeedbackService:
    """Business logic for the feedback module."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.feedback = FeedbackRepository(db)
        self.conversations = ChatConversationRepository(db)

    # ------------------------------------------------------------------ #
    # Submission
    # ------------------------------------------------------------------ #
    def submit_feedback(self, user: User, payload: FeedbackCreate) -> FeedbackResponse:
        """Record (or update) the user's feedback for one of their conversations.

        The conversation must belong to ``user`` — a foreign or unknown
        conversation is indistinguishable from a missing one (404). Feedback
        can be captured at any point in the conversation lifecycle (no status
        gate); resubmission updates the existing record so analytics never
        double-count a conversation.
        """
        conversation = self.conversations.get_for_user(payload.conversation_id, user.id)
        if conversation is None:
            raise NotFoundError("Conversation not found")

        existing = self.feedback.get_for_customer_and_conversation(
            user.id, conversation.id
        )
        if existing is not None:
            existing.rating = payload.rating
            existing.comment = payload.comment
            self.db.commit()
            self.db.refresh(existing)
            return FeedbackResponse.model_validate(existing)

        feedback = Feedback(
            customer_id=user.id,
            conversation_id=conversation.id,
            rating=payload.rating,
            comment=payload.comment,
        )
        self.feedback.add(feedback)
        try:
            self.db.commit()
        except IntegrityError:
            # A concurrent submission for the same (customer, conversation)
            # slipped in — the unique constraint wins, so update that record
            # instead of 500ing.
            self.db.rollback()
            existing = self.feedback.get_for_customer_and_conversation(
                user.id, conversation.id
            )
            if existing is None:  # pragma: no cover - defensive
                raise
            existing.rating = payload.rating
            existing.comment = payload.comment
            self.db.commit()
            self.db.refresh(existing)
            return FeedbackResponse.model_validate(existing)
        self.db.refresh(feedback)
        return FeedbackResponse.model_validate(feedback)

    # ------------------------------------------------------------------ #
    # Summary (analytics)
    # ------------------------------------------------------------------ #
    def feedback_for_conversation(
        self, conversation_id: uuid.UUID
    ) -> list[FeedbackResponse]:
        """Return the feedback records attached to a conversation.

        Dashboard hook: the future AI quality / analytics module correlates a
        conversation's rating with its transcript through this method (the
        caller is expected to have read access already, e.g. an agent viewing
        a conversation they handle).
        """
        records = self.feedback.list_for_conversation(conversation_id)
        return [FeedbackResponse.model_validate(record) for record in records]

    def get_summary(self) -> FeedbackSummary:
        """Aggregate feedback metrics across all customers.

        ``rating_distribution`` always contains every rating 1–5 (zero-filled)
        so dashboards don't need to fill gaps. Positive = ratings ≥ 4,
        negative = ratings ≤ 2, neutral (3) is excluded from both percentages.
        """
        rows = self.feedback.ratings_distribution()
        counts = dict.fromkeys(RATING_SCALE, 0)
        total = 0
        weighted = 0
        positive = 0
        negative = 0
        for rating, count in rows:
            counts[rating] = count
            total += count
            weighted += rating * count
            if rating >= POSITIVE_MIN_RATING:
                positive += count
            elif rating <= NEGATIVE_MAX_RATING:
                negative += count

        if total == 0:
            return FeedbackSummary(
                total_feedback=0,
                average_rating=0.0,
                rating_distribution=counts,
                positive_percentage=0.0,
                negative_percentage=0.0,
            )
        return FeedbackSummary(
            total_feedback=total,
            average_rating=round(weighted / total, 2),
            rating_distribution=counts,
            positive_percentage=round(positive / total * 100, 2),
            negative_percentage=round(negative / total * 100, 2),
        )
