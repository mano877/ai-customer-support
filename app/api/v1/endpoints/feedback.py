"""Feedback endpoints: submission and summary metrics.

Routers stay thin: they parse/validate input and delegate to the service layer.
Customers submit feedback for their own conversations; the aggregate summary is
staff-only analytics (support agents and admins).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies.auth import CurrentUser, require_roles
from app.models.user import User, UserRole
from app.schemas.feedback import FeedbackCreate, FeedbackResponse, FeedbackSummary
from app.services.feedback import FeedbackService

router = APIRouter(prefix="/feedback", tags=["feedback"])

AgentUser = Annotated[User, Depends(require_roles(UserRole.SUPPORT_AGENT))]


@router.post("", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
def submit_feedback(
    payload: FeedbackCreate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> FeedbackResponse:
    """Rate one of the authenticated user's conversations (1–5 stars).

    The conversation must belong to the caller; resubmitting updates the
    existing feedback for that conversation.
    """
    return FeedbackService(db).submit_feedback(user, payload)


@router.get("/summary", response_model=FeedbackSummary)
def feedback_summary(
    agent: AgentUser,
    db: Annotated[Session, Depends(get_db)],
) -> FeedbackSummary:
    """Return aggregate feedback metrics across all customers (staff-only)."""
    return FeedbackService(db).get_summary()
