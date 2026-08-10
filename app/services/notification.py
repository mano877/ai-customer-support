"""Notification service: creation hook, listing, and read-state management.

Delivery is delegated to a ``NotificationChannel`` so email, SMS, WhatsApp, or
push providers can be plugged in later without touching the API or this
service. Until then ``NOTIFICATION_CHANNEL=noop`` records notifications in the
database only.

The service-level ``create_notification`` is the integration point for other
modules: the Orders, Tickets, Payments, and AI Service modules will call it
(e.g. ``NotificationService(db).create_notification(user_id, type_=...)``)
when events such as ``ORDER_SHIPPED`` or ``TICKET_ASSIGNED`` occur.
"""

import uuid
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.notification import Notification, NotificationType
from app.models.user import User
from app.repositories.notification import NotificationRepository
from app.repositories.user import UserRepository
from app.schemas.notification import (
    NotificationListResponse,
    NotificationResponse,
)

TITLE_MAX_LENGTH = 255
MESSAGE_MAX_LENGTH = 4000
ENTITY_TYPE_MAX_LENGTH = 64


class NotificationChannel(Protocol):
    """Contract for delivering a notification out-of-band.

    Future providers (email, SMS, WhatsApp, push) implement this protocol and
    are wired in via ``NOTIFICATION_CHANNEL`` — the API architecture and the
    notification records themselves stay unchanged.
    """

    def deliver(self, notification: Notification) -> None: ...


class NoopNotificationChannel:
    """Placeholder channel: the notification lives in the database only.

    Swapped out by implementing ``NotificationChannel`` and setting
    ``NOTIFICATION_CHANNEL`` to the new backend.
    """

    def deliver(self, notification: Notification) -> None:
        """No external delivery at this stage (infrastructure only)."""
        return None


def build_notification_channel(backend: str) -> NotificationChannel:
    """Return the configured delivery channel backend.

    ``NOTIFICATION_CHANNEL`` currently supports ``noop``. Future values will
    construct the email / SMS / WhatsApp / push providers here.
    """
    if backend == "noop":
        return NoopNotificationChannel()
    raise ValueError(f"Unsupported notification channel backend: {backend!r}")


class NotificationService:
    """Business logic for the notifications module."""

    def __init__(
        self,
        db: Session,
        channel: NotificationChannel | None = None,
    ) -> None:
        self.db = db
        self.notifications = NotificationRepository(db)
        self.users = UserRepository(db)
        self.channel = channel or build_notification_channel(
            get_settings().NOTIFICATION_CHANNEL
        )

    # ------------------------------------------------------------------ #
    # Creation (integration hook for other modules)
    # ------------------------------------------------------------------ #
    def create_notification(
        self,
        user_id: uuid.UUID,
        *,
        type_: NotificationType,
        title: str,
        message: str,
        related_entity_type: str | None = None,
        related_entity_id: uuid.UUID | None = None,
    ) -> NotificationResponse:
        """Persist a notification for ``user_id`` and hand it to the channel.

        This is the extension point used by Orders / Tickets / Payments / AI
        Service to raise notifications. Input is validated defensively since
        callers are internal modules rather than the API schema layer.
        """
        if self.users.get(user_id) is None:
            raise NotFoundError("User not found")

        title = title.strip()
        if not title:
            raise BadRequestError("title must not be blank")
        if len(title) > TITLE_MAX_LENGTH:
            raise BadRequestError(
                f"title must be at most {TITLE_MAX_LENGTH} characters"
            )

        message = message.strip()
        if not message:
            raise BadRequestError("message must not be blank")
        if len(message) > MESSAGE_MAX_LENGTH:
            raise BadRequestError(
                f"message must be at most {MESSAGE_MAX_LENGTH} characters"
            )

        if related_entity_type is not None:
            related_entity_type = related_entity_type.strip()
            if not related_entity_type:
                raise BadRequestError("related_entity_type must not be blank")
            if len(related_entity_type) > ENTITY_TYPE_MAX_LENGTH:
                raise BadRequestError(
                    "related_entity_type must be at most "
                    f"{ENTITY_TYPE_MAX_LENGTH} characters"
                )
        if (related_entity_type is None) != (related_entity_id is None):
            raise BadRequestError(
                "related_entity_type and related_entity_id must be set together"
            )

        notification = Notification(
            user_id=user_id,
            type=type_,
            title=title,
            message=message,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
        )
        self.notifications.add(notification)
        self.db.commit()
        self.db.refresh(notification)
        # Deliver only after the record is durable in the database. Future
        # providers must be idempotent / raise-aware: the record is already
        # committed, so a delivery failure must not make the caller retry and
        # create a duplicate notification.
        self.channel.deliver(notification)
        return NotificationResponse.model_validate(notification)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def list_notifications(
        self,
        user: User,
        *,
        is_read: bool | None = None,
        type_: NotificationType | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> NotificationListResponse:
        """Return a paginated list of the user's own notifications, newest
        first, with optional is_read / type filters."""
        items = self.notifications.list_for_user(
            user.id,
            is_read=is_read,
            type_=type_,
            limit=limit,
            offset=offset,
        )
        total = self.notifications.count_for_user(
            user.id, is_read=is_read, type_=type_
        )
        return NotificationListResponse(
            items=[NotificationResponse.model_validate(item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    # ------------------------------------------------------------------ #
    # Read state
    # ------------------------------------------------------------------ #
    def mark_read(
        self, user: User, notification_id: uuid.UUID
    ) -> NotificationResponse:
        """Mark one of the user's notifications as read (idempotent).

        Uses a guarded UPDATE (``is_read = false``) so a notification can only
        transition to read once — even under concurrent requests. Repeating the
        PATCH returns the current state with the original ``read_at``.
        """
        notification = self._get_owned_notification(user, notification_id)
        if not notification.is_read:
            updated = self.notifications.mark_read(user.id, notification.id)
            if updated != 1:
                # A concurrent request already marked it read — the guarded
                # UPDATE matched zero rows. Treat it as already read.
                self.db.rollback()
                notification = self._get_owned_notification(
                    user, notification_id
                )
            else:
                self.db.commit()
                self.db.refresh(notification)
        return NotificationResponse.model_validate(notification)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _get_owned_notification(
        self, user: User, notification_id: uuid.UUID
    ) -> Notification:
        """Fetch a notification owned by the user or raise 404."""
        notification = self.notifications.get_for_user(notification_id, user.id)
        if notification is None:
            raise NotFoundError("Notification not found")
        return notification
