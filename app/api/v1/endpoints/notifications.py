"""Notification endpoints: list and mark-as-read.

Routers stay thin: they parse/validate input and delegate to the service layer.
Notifications are always scoped to the authenticated user — nobody can read or
mark another user's notifications.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies.auth import CurrentUser
from app.models.notification import NotificationType
from app.schemas.notification import NotificationListResponse, NotificationResponse
from app.services.notification import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    is_read: bool | None = Query(default=None),
    type_: NotificationType | None = Query(default=None, alias="type"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> NotificationListResponse:
    """List the authenticated user's notifications, newest first.

    Supports filtering by read state (``is_read``) and notification type
    (``type``), plus pagination.
    """
    return NotificationService(db).list_notifications(
        user,
        is_read=is_read,
        type_=type_,
        limit=limit,
        offset=offset,
    )


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> NotificationResponse:
    """Mark one of the user's notifications as read (idempotent)."""
    return NotificationService(db).mark_read(user, notification_id)
