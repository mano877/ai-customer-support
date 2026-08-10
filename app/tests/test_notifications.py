"""Tests for the Notifications module (list, filters, mark-read, creation hook)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.notification import Notification, NotificationType
from app.models.user import User
from app.services.notification import (
    NoopNotificationChannel,
    NotificationService,
    build_notification_channel,
)

API = "/api/v1/notifications"
CUSTOMER_EMAIL = "notify-customer@example.com"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _register_verify(client, db_session, email=CUSTOMER_EMAIL) -> tuple[dict, uuid.UUID]:
    """Register + verify a customer and return (tokens, user_id)."""
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass123!", "full_name": "Notify User"},
    )
    assert response.status_code == 201, response.text
    otp = response.json()["dev_otp"]
    tokens = client.post("/api/v1/auth/verify-otp", json={"email": email, "otp": otp})
    assert tokens.status_code == 200, tokens.text
    user = db_session.scalars(select(User).where(User.email == email)).one()
    return tokens.json(), user.id


def _auth_headers(tokens) -> dict:
    """Build auth headers from a token dict."""
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _create_notification(db_session, user_id, **overrides) -> dict:
    """Create a notification through the service hook (the real integration path)."""
    notification = NotificationService(db_session).create_notification(
        user_id,
        type_=overrides.pop("type_", NotificationType.SYSTEM),
        title=overrides.pop("title", "Package shipped"),
        message=overrides.pop("message", "Your order is on its way."),
        **overrides,
    )
    return notification.model_dump(mode="json")


def _mark_read(db_session, user_id, notification_id) -> None:
    """Mark a notification read through the service (idempotent path)."""
    user = db_session.get(User, user_id)
    NotificationService(db_session).mark_read(user, uuid.UUID(notification_id))


def _fresh(db_session, model, obj_id):
    """Fetch an object, bypassing a possibly-stale identity map."""
    obj = db_session.get(model, obj_id)
    db_session.refresh(obj)
    return obj


class _RecordingChannel:
    """Fake NotificationChannel that records deliveries for assertions."""

    def __init__(self) -> None:
        self.delivered: list[Notification] = []

    def deliver(self, notification: Notification) -> None:
        self.delivered.append(notification)


# --------------------------------------------------------------------------- #
# Creation hook (service level — used by other modules)
# --------------------------------------------------------------------------- #
class TestCreateNotification:
    def test_creates_unread_notification(self, client, db_session):
        _tokens, user_id = _register_verify(client, db_session)

        body = _create_notification(db_session, user_id)
        assert body["user_id"] == str(user_id)
        assert body["type"] == "system"
        assert body["title"] == "Package shipped"
        assert body["message"] == "Your order is on its way."
        assert body["is_read"] is False
        assert body["read_at"] is None
        assert body["related_entity_type"] is None
        assert body["related_entity_id"] is None
        assert body["created_at"] is not None

    def test_creates_with_related_entity(self, client, db_session):
        _tokens, user_id = _register_verify(client, db_session)
        related_id = uuid.uuid4()

        body = _create_notification(
            db_session,
            user_id,
            type_=NotificationType.ORDER_SHIPPED,
            title="Order shipped",
            message="Your order #1 has shipped.",
            related_entity_type="order",
            related_entity_id=related_id,
        )
        assert body["type"] == "order_shipped"
        assert body["related_entity_type"] == "order"
        assert body["related_entity_id"] == str(related_id)

    def test_unknown_user_404(self, db_session):
        with pytest.raises(NotFoundError):
            NotificationService(db_session).create_notification(
                uuid.uuid4(), type_=NotificationType.SYSTEM, title="t", message="m"
            )

    def test_blank_title_400(self, client, db_session):
        _tokens, user_id = _register_verify(client, db_session)
        with pytest.raises(BadRequestError):
            NotificationService(db_session).create_notification(
                user_id, type_=NotificationType.SYSTEM, title="   ", message="m"
            )

    def test_blank_message_400(self, client, db_session):
        _tokens, user_id = _register_verify(client, db_session)
        with pytest.raises(BadRequestError):
            NotificationService(db_session).create_notification(
                user_id, type_=NotificationType.SYSTEM, title="t", message="  "
            )

    def test_title_too_long_400(self, client, db_session):
        _tokens, user_id = _register_verify(client, db_session)
        with pytest.raises(BadRequestError):
            NotificationService(db_session).create_notification(
                user_id, type_=NotificationType.SYSTEM, title="x" * 256, message="m"
            )

    def test_message_too_long_400(self, client, db_session):
        _tokens, user_id = _register_verify(client, db_session)
        with pytest.raises(BadRequestError):
            NotificationService(db_session).create_notification(
                user_id, type_=NotificationType.SYSTEM, title="t", message="x" * 4001
            )

    def test_related_entity_must_be_pair_400(self, client, db_session):
        _tokens, user_id = _register_verify(client, db_session)
        with pytest.raises(BadRequestError):
            NotificationService(db_session).create_notification(
                user_id,
                type_=NotificationType.SYSTEM,
                title="t",
                message="m",
                related_entity_type="order",
            )
        with pytest.raises(BadRequestError):
            NotificationService(db_session).create_notification(
                user_id,
                type_=NotificationType.SYSTEM,
                title="t",
                message="m",
                related_entity_id=uuid.uuid4(),
            )

    def test_blank_related_entity_type_400(self, client, db_session):
        _tokens, user_id = _register_verify(client, db_session)
        with pytest.raises(BadRequestError):
            NotificationService(db_session).create_notification(
                user_id,
                type_=NotificationType.SYSTEM,
                title="t",
                message="m",
                related_entity_type="  ",
                related_entity_id=uuid.uuid4(),
            )

    def test_related_entity_type_too_long_400(self, client, db_session):
        _tokens, user_id = _register_verify(client, db_session)
        with pytest.raises(BadRequestError):
            NotificationService(db_session).create_notification(
                user_id,
                type_=NotificationType.SYSTEM,
                title="t",
                message="m",
                related_entity_type="x" * 65,
                related_entity_id=uuid.uuid4(),
            )

    def test_channel_receives_delivery(self, client, db_session):
        """The channel hook fires after persistence (extensibility contract)."""
        _tokens, user_id = _register_verify(client, db_session)
        channel = _RecordingChannel()
        service = NotificationService(db_session, channel=channel)

        notification = service.create_notification(
            user_id,
            type_=NotificationType.AI_HANDOFF,
            title="Handoff",
            message="A human agent is on the way.",
        )
        assert len(channel.delivered) == 1
        assert channel.delivered[0].id == notification.id

    def test_default_channel_is_noop(self):
        assert isinstance(build_notification_channel("noop"), NoopNotificationChannel)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError):
            build_notification_channel("twilio-sms")


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #
class TestListNotifications:
    def test_requires_auth(self, client):
        assert client.get(API).status_code == 401

    def test_empty_list(self, client, db_session):
        tokens, _user_id = _register_verify(client, db_session)
        body = client.get(API, headers=_auth_headers(tokens)).json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_lists_only_own_notifications(self, client, db_session):
        mine, my_id = _register_verify(client, db_session)
        other, other_id = _register_verify(client, db_session, email="other@example.com")
        _create_notification(db_session, other_id)
        _create_notification(db_session, other_id)

        body = client.get(API, headers=_auth_headers(mine)).json()
        assert body["total"] == 0

        _create_notification(db_session, my_id)
        body = client.get(API, headers=_auth_headers(mine)).json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Package shipped"

    def test_list_newest_first(self, client, db_session):
        tokens, user_id = _register_verify(client, db_session)
        created = [
            _create_notification(db_session, user_id, title=f"notice-{index}")
            for index in range(3)
        ]
        # Stagger created_at (SQLite now() has 1s precision) for determinism.
        for index, notification_json in enumerate(created):
            record = _fresh(
                db_session, Notification, uuid.UUID(notification_json["id"])
            )
            record.created_at = datetime(2026, 8, index + 1, 12, 0, tzinfo=UTC)
        db_session.commit()

        body = client.get(API, headers=_auth_headers(tokens)).json()
        ids = [item["id"] for item in body["items"]]
        assert ids == [created[2]["id"], created[1]["id"], created[0]["id"]]

    def test_list_pagination(self, client, db_session):
        tokens, user_id = _register_verify(client, db_session)
        for index in range(5):
            _create_notification(db_session, user_id, title=f"notice-{index}")

        page1 = client.get(
            API, params={"limit": 2}, headers=_auth_headers(tokens)
        ).json()
        assert page1["total"] == 5
        assert len(page1["items"]) == 2

        page2 = client.get(
            API, params={"limit": 2, "offset": 2}, headers=_auth_headers(tokens)
        ).json()
        assert len(page2["items"]) == 2
        first_ids = {item["id"] for item in page1["items"]}
        assert all(item["id"] not in first_ids for item in page2["items"])

    def test_list_limit_bounds_422(self, client, db_session):
        tokens, _user_id = _register_verify(client, db_session)
        assert (
            client.get(
                API, params={"limit": 0}, headers=_auth_headers(tokens)
            ).status_code
            == 422
        )

    def test_filter_by_is_read(self, client, db_session):
        tokens, user_id = _register_verify(client, db_session)
        unread = _create_notification(db_session, user_id, title="unread one")
        _create_notification(db_session, user_id, title="read me")
        _mark_read(db_session, user_id, unread["id"])

        body = client.get(
            API, params={"is_read": "true"}, headers=_auth_headers(tokens)
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "unread one"
        assert body["items"][0]["is_read"] is True

        body = client.get(
            API, params={"is_read": "false"}, headers=_auth_headers(tokens)
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "read me"
        assert body["items"][0]["is_read"] is False

    def test_filter_by_type(self, client, db_session):
        tokens, user_id = _register_verify(client, db_session)
        _create_notification(
            db_session, user_id, type_=NotificationType.SYSTEM, title="system notice"
        )
        shipped = _create_notification(
            db_session,
            user_id,
            type_=NotificationType.ORDER_SHIPPED,
            title="order notice",
        )

        body = client.get(
            API, params={"type": "order_shipped"}, headers=_auth_headers(tokens)
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == shipped["id"]

        body = client.get(
            API, params={"type": "system"}, headers=_auth_headers(tokens)
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "system notice"

    def test_filter_combines_is_read_and_type(self, client, db_session):
        tokens, user_id = _register_verify(client, db_session)
        unread_shipped = _create_notification(
            db_session,
            user_id,
            type_=NotificationType.ORDER_SHIPPED,
            title="shipped unread",
        )
        read_system = _create_notification(
            db_session, user_id, type_=NotificationType.SYSTEM, title="system read"
        )
        _mark_read(db_session, user_id, read_system["id"])

        body = client.get(
            API,
            params={"is_read": "false", "type": "order_shipped"},
            headers=_auth_headers(tokens),
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == unread_shipped["id"]

    def test_invalid_type_filter_422(self, client, db_session):
        tokens, _user_id = _register_verify(client, db_session)
        response = client.get(
            API, params={"type": "bogus"}, headers=_auth_headers(tokens)
        )
        assert response.status_code == 422

    def test_invalid_is_read_filter_422(self, client, db_session):
        tokens, _user_id = _register_verify(client, db_session)
        response = client.get(
            API, params={"is_read": "maybe"}, headers=_auth_headers(tokens)
        )
        assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Mark as read
# --------------------------------------------------------------------------- #
class TestMarkRead:
    def test_requires_auth(self, client):
        assert client.patch(f"{API}/{uuid.uuid4()}/read").status_code == 401

    def test_marks_unread_notification(self, client, db_session):
        tokens, user_id = _register_verify(client, db_session)
        created = _create_notification(db_session, user_id)
        assert created["is_read"] is False

        response = client.patch(
            f"{API}/{created['id']}/read", headers=_auth_headers(tokens)
        )
        assert response.status_code == 200
        body = response.json()
        assert body["is_read"] is True
        assert body["read_at"] is not None
        # Unchanged fields survive.
        assert body["title"] == created["title"]
        assert body["user_id"] == str(user_id)

    def test_mark_read_is_idempotent(self, client, db_session):
        tokens, user_id = _register_verify(client, db_session)
        created = _create_notification(db_session, user_id)

        first = client.patch(
            f"{API}/{created['id']}/read", headers=_auth_headers(tokens)
        ).json()
        second = client.patch(
            f"{API}/{created['id']}/read", headers=_auth_headers(tokens)
        ).json()
        assert second["is_read"] is True
        # read_at is set once and never reset by repeated PATCHes.
        assert second["read_at"] == first["read_at"]

    def test_foreign_notification_404(self, client, db_session):
        tokens, _my_id = _register_verify(client, db_session)
        _other, other_id = _register_verify(
            client, db_session, email="stranger@example.com"
        )
        created = _create_notification(db_session, other_id)

        response = client.patch(
            f"{API}/{created['id']}/read", headers=_auth_headers(tokens)
        )
        assert response.status_code == 404

    def test_missing_notification_404(self, client, db_session):
        tokens, _user_id = _register_verify(client, db_session)
        response = client.patch(
            f"{API}/{uuid.uuid4()}/read", headers=_auth_headers(tokens)
        )
        assert response.status_code == 404

    def test_invalid_id_422(self, client, db_session):
        tokens, _user_id = _register_verify(client, db_session)
        response = client.patch(f"{API}/not-a-uuid/read", headers=_auth_headers(tokens))
        assert response.status_code == 422

    def test_read_notification_removed_from_unread_filter(self, client, db_session):
        tokens, user_id = _register_verify(client, db_session)
        created = _create_notification(db_session, user_id)

        client.patch(f"{API}/{created['id']}/read", headers=_auth_headers(tokens))

        unread = client.get(
            API, params={"is_read": "false"}, headers=_auth_headers(tokens)
        ).json()
        assert unread["total"] == 0
        read = client.get(
            API, params={"is_read": "true"}, headers=_auth_headers(tokens)
        ).json()
        assert read["total"] == 1
        assert read["items"][0]["id"] == created["id"]
