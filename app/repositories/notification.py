"""Notification repository."""

import uuid

from sqlalchemy import func, select, update

from app.core.security import now_utc
from app.models.notification import Notification, NotificationType
from app.repositories.base import BaseRepository


def _filter_notifications(
    stmt,
    *,
    is_read: bool | None = None,
    type_: NotificationType | None = None,
):
    """Apply the shared is_read / type filters to a notification query."""
    if is_read is not None:
        stmt = stmt.where(Notification.is_read.is_(is_read))
    if type_ is not None:
        stmt = stmt.where(Notification.type == type_.value)
    return stmt


class NotificationRepository(BaseRepository[Notification]):
    """Data access for notifications, always scoped to the owning user.

    Every read resolves through ``user_id`` so foreign notifications are
    indistinguishable from missing ones — customers (and every other role) can
    only ever see their own notifications.
    """

    model = Notification

    def get_for_user(
        self, notification_id: uuid.UUID, user_id: uuid.UUID
    ) -> Notification | None:
        """Fetch a notification by id, only if it belongs to ``user_id``."""
        stmt = select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
        return self.session.scalars(stmt).first()

    def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
        is_read: bool | None = None,
        type_: NotificationType | None = None,
    ) -> list[Notification]:
        """Return a page of the user's notifications, newest first, with
        optional is_read / type filters."""
        stmt = _filter_notifications(
            select(Notification).where(Notification.user_id == user_id),
            is_read=is_read,
            type_=type_,
        )
        stmt = (
            stmt.order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def count_for_user(
        self,
        user_id: uuid.UUID,
        *,
        is_read: bool | None = None,
        type_: NotificationType | None = None,
    ) -> int:
        """Count the user's notifications for pagination metadata."""
        stmt = _filter_notifications(
            select(func.count(Notification.id)).where(Notification.user_id == user_id),
            is_read=is_read,
            type_=type_,
        )
        return self.session.scalars(stmt).one()

    def mark_read(self, user_id: uuid.UUID, notification_id: uuid.UUID) -> int:
        """Atomically mark one of the user's notifications as read.

        Returns the number of rows updated: 1 on success, 0 when the
        notification does not exist, is not owned by ``user_id``, or was
        already marked read (the guarded ``is_read = false`` condition makes
        concurrent mark-read requests safe).
        """
        result = self.session.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=now_utc())
        )
        return result.rowcount or 0
