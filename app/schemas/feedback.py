"""Feedback-related schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

COMMENT_MAX_LENGTH = 1000


class FeedbackCreate(BaseModel):
    """Payload for POST /feedback.

    ``conversation_id`` points at an existing chat conversation owned by the
    caller; the rating must be between 1 and 5.
    """

    conversation_id: uuid.UUID
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=COMMENT_MAX_LENGTH)

    @field_validator("comment")
    @classmethod
    def _comment_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("comment must not be blank")
        return value


class FeedbackResponse(BaseModel):
    """Public representation of a feedback record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    conversation_id: uuid.UUID
    rating: int
    comment: str | None = None
    created_at: datetime


class FeedbackSummary(BaseModel):
    """Aggregate feedback metrics for the analytics dashboard.

    ``rating_distribution`` always contains every rating 1–5 (zero-filled).
    Positive = ratings ≥ 4, negative = ratings ≤ 2; neutral (3) is excluded
    from both percentages, so they do not necessarily sum to 100.
    """

    total_feedback: int
    average_rating: float
    rating_distribution: dict[int, int]
    positive_percentage: float
    negative_percentage: float
