"""Notification-related schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.notification import NotificationType


class NotificationResponse(BaseModel):
    """Public representation of a notification."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    type: NotificationType
    title: str
    message: str
    is_read: bool
    related_entity_type: str | None = None
    related_entity_id: uuid.UUID | None = None
    created_at: datetime
    read_at: datetime | None = None


class NotificationListResponse(BaseModel):
    """Paginated notification listing."""

    items: list[NotificationResponse]
    total: int
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
