"""Tests for the Feedback module (submission + summary)."""

import uuid

from sqlalchemy import select

from app.core.security import hash_password
from app.models.feedback import Feedback
from app.models.user import User, UserRole
from app.schemas.auth import LoginRequest
from app.services.auth import AuthService
from app.services.feedback import FeedbackService

API = "/api/v1/feedback"
CHAT_API = "/api/v1/chat"
CUSTOMER_EMAIL = "feedback-customer@example.com"
AGENT_EMAIL = "feedback-agent@example.com"
AGENT_PASSWORD = "AgentPass123!"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _register_verify(client, email=CUSTOMER_EMAIL) -> dict:
    """Register + verify a customer and return its token dict."""
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass123!", "full_name": "Feedback User"},
    )
    assert response.status_code == 201, response.text
    otp = response.json()["dev_otp"]
    tokens = client.post("/api/v1/auth/verify-otp", json={"email": email, "otp": otp})
    assert tokens.status_code == 200, tokens.text
    return tokens.json()


def _auth_headers(tokens) -> dict:
    """Build auth headers from a token dict or a TokenResponse object."""
    access = tokens["access_token"] if isinstance(tokens, dict) else tokens.access_token
    return {"Authorization": f"Bearer {access}"}


def _agent_tokens(client, db_session, *, email=AGENT_EMAIL, role=UserRole.SUPPORT_AGENT):
    """Create a verified support agent (or admin) and return (tokens, user)."""
    user = User(
        email=email,
        hashed_password=hash_password(AGENT_PASSWORD),
        full_name="Support Agent",
        role=role,
        is_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    tokens = AuthService(db_session).login(
        LoginRequest(email=email, password=AGENT_PASSWORD)
    )
    return tokens, user


def _create_conversation(client, headers) -> dict:
    """Create a chat conversation via the chat API."""
    response = client.post(
        f"{CHAT_API}/conversations",
        json={"subject": "chat", "initial_message": "I need help"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _submit_feedback(client, headers, conversation_id, **overrides) -> dict:
    """POST /feedback asserting success, returning the response body."""
    payload = {"conversation_id": conversation_id, "rating": 5, **overrides}
    response = client.post(API, json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _get_summary(client, headers) -> dict:
    response = client.get(f"{API}/summary", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _feedback_count(db_session) -> int:
    return len(db_session.scalars(select(Feedback)).all())


# --------------------------------------------------------------------------- #
# Submission
# --------------------------------------------------------------------------- #
class TestSubmitFeedback:
    def test_requires_auth(self, client):
        assert (
            client.post(API, json={"conversation_id": str(uuid.uuid4()), "rating": 5}).status_code
            == 401
        )

    def test_submits_for_own_conversation(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)

        body = _submit_feedback(
            client,
            headers,
            conversation["id"],
            rating=4,
            comment="  Great support!  ",
        )
        assert body["conversation_id"] == conversation["id"]
        assert body["customer_id"] is not None
        assert body["rating"] == 4
        assert body["comment"] == "Great support!"  # stripped
        assert body["created_at"] is not None

    def test_rating_boundaries_accepted(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)

        assert _submit_feedback(client, headers, conversation["id"], rating=1)["rating"] == 1
        # Resubmission on the same conversation updates, so use a new one.
        second = _create_conversation(client, headers)
        assert _submit_feedback(client, headers, second["id"], rating=5)["rating"] == 5

    def test_rating_zero_422(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)

        response = client.post(
            API,
            json={"conversation_id": conversation["id"], "rating": 0},
            headers=headers,
        )
        assert response.status_code == 422

    def test_rating_six_422(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)

        response = client.post(
            API,
            json={"conversation_id": conversation["id"], "rating": 6},
            headers=headers,
        )
        assert response.status_code == 422

    def test_rating_not_integer_422(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)

        response = client.post(
            API,
            json={"conversation_id": conversation["id"], "rating": "five"},
            headers=headers,
        )
        assert response.status_code == 422

    def test_comment_optional(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)

        body = _submit_feedback(client, headers, conversation["id"], rating=3)
        assert body["comment"] is None

    def test_blank_comment_422(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)

        response = client.post(
            API,
            json={"conversation_id": conversation["id"], "rating": 5, "comment": "   "},
            headers=headers,
        )
        assert response.status_code == 422

    def test_comment_too_long_422(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)

        response = client.post(
            API,
            json={"conversation_id": conversation["id"], "rating": 5, "comment": "x" * 1001},
            headers=headers,
        )
        assert response.status_code == 422

    def test_foreign_conversation_404(self, client):
        tokens = _register_verify(client, email="owner@example.com")
        other = _register_verify(client, email="stranger@example.com")
        conversation = _create_conversation(client, _auth_headers(other))

        response = client.post(
            API,
            json={"conversation_id": conversation["id"], "rating": 5},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 404

    def test_unknown_conversation_404(self, client):
        tokens = _register_verify(client)
        response = client.post(
            API,
            json={"conversation_id": str(uuid.uuid4()), "rating": 5},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 404

    def test_invalid_conversation_id_422(self, client):
        tokens = _register_verify(client)
        response = client.post(
            API, json={"conversation_id": "not-a-uuid", "rating": 5},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 422

    def test_resubmission_updates_single_record(self, client, db_session):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)

        first = _submit_feedback(
            client, headers, conversation["id"], rating=2, comment="not great"
        )
        second = _submit_feedback(
            client, headers, conversation["id"], rating=5, comment="changed my mind"
        )
        assert second["id"] == first["id"]  # same record, not a duplicate
        assert second["rating"] == 5
        assert second["comment"] == "changed my mind"

        # Exactly one row exists for the (customer, conversation) pair.
        assert _feedback_count(db_session) == 1
        record = db_session.scalars(select(Feedback)).one()
        assert record.rating == 5
        assert record.comment == "changed my mind"

    def test_resubmission_keeps_created_at(self, client, db_session):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)

        first = _submit_feedback(client, headers, conversation["id"], rating=4)
        second = _submit_feedback(client, headers, conversation["id"], rating=5)
        assert second["created_at"] == first["created_at"]

    def test_no_endpoint_to_read_individual_feedback(self, client):
        """Customers cannot access another customer's feedback: there is no
        GET /feedback/{id} — individual feedback is never exposed."""
        tokens = _register_verify(client)
        response = client.get(f"{API}/{uuid.uuid4()}", headers=_auth_headers(tokens))
        assert response.status_code == 404

    def test_deleting_conversation_cascades_feedback(self, client, db_session):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)
        _submit_feedback(client, headers, conversation["id"], rating=5)
        assert _feedback_count(db_session) == 1

        from app.models.chat import ChatConversation

        record = db_session.get(ChatConversation, uuid.UUID(conversation["id"]))
        db_session.delete(record)
        db_session.commit()
        assert _feedback_count(db_session) == 0


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
class TestSummary:
    def test_requires_auth(self, client):
        assert client.get(f"{API}/summary").status_code == 401

    def test_customer_forbidden_403(self, client):
        tokens = _register_verify(client)
        assert client.get(f"{API}/summary", headers=_auth_headers(tokens)).status_code == 403

    def test_agent_can_read_200(self, client, db_session):
        agent_tokens, _agent = _agent_tokens(client, db_session)
        response = client.get(f"{API}/summary", headers=_auth_headers(agent_tokens))
        assert response.status_code == 200

    def test_admin_can_read_200(self, client, db_session):
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )
        response = client.get(f"{API}/summary", headers=_auth_headers(admin_tokens))
        assert response.status_code == 200

    def test_empty_summary_zeros(self, client, db_session):
        agent_tokens, _agent = _agent_tokens(client, db_session)
        body = _get_summary(client, _auth_headers(agent_tokens))
        assert body["total_feedback"] == 0
        assert body["average_rating"] == 0.0
        assert body["rating_distribution"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
        assert body["positive_percentage"] == 0.0
        assert body["negative_percentage"] == 0.0

    def test_summary_math(self, client, db_session):
        """Aggregates across customers; ratings [5,5,4,3,1,1]."""
        alice = _register_verify(client, email="alice@example.com")
        bob = _register_verify(client, email="bob@example.com")
        alice_headers = _auth_headers(alice)
        bob_headers = _auth_headers(bob)

        for rating in (5, 5, 4):
            conversation = _create_conversation(client, alice_headers)
            _submit_feedback(client, alice_headers, conversation["id"], rating=rating)
        for rating in (3, 1, 1):
            conversation = _create_conversation(client, bob_headers)
            _submit_feedback(client, bob_headers, conversation["id"], rating=rating)

        agent_tokens, _agent = _agent_tokens(client, db_session)
        body = _get_summary(client, _auth_headers(agent_tokens))
        assert body["total_feedback"] == 6
        assert body["average_rating"] == 3.17  # 19 / 6
        assert body["rating_distribution"] == {
            "1": 2,
            "2": 0,
            "3": 1,
            "4": 1,
            "5": 2,
        }
        assert body["positive_percentage"] == 50.0  # 3 of 6 (5,5,4)
        assert body["negative_percentage"] == 33.33  # 2 of 6 (1,1)

    def test_summary_excludes_unrated_conversations(self, client, db_session):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        _create_conversation(client, headers)  # no feedback submitted
        _create_conversation(client, headers)  # no feedback submitted

        agent_tokens, _agent = _agent_tokens(client, db_session)
        body = _get_summary(client, _auth_headers(agent_tokens))
        assert body["total_feedback"] == 0
        assert body["average_rating"] == 0.0


# --------------------------------------------------------------------------- #
# Dashboard hook
# --------------------------------------------------------------------------- #
class TestDashboardHook:
    def test_feedback_for_conversation_lists_records(self, client, db_session):
        """The AI quality dashboard drills into a conversation's feedback
        through the service layer."""
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)
        _submit_feedback(client, headers, conversation["id"], rating=5, comment="nice")

        user = db_session.scalars(
            select(User).where(User.email == CUSTOMER_EMAIL)
        ).one()
        records = FeedbackService(db_session).feedback_for_conversation(
            uuid.UUID(conversation["id"])
        )
        assert len(records) == 1
        assert records[0].rating == 5
        assert records[0].comment == "nice"
        assert records[0].customer_id == user.id

    def test_feedback_for_conversation_empty(self, db_session, client):
        conversation = _create_conversation(
            client, _auth_headers(_register_verify(client))
        )
        records = FeedbackService(db_session).feedback_for_conversation(
            uuid.UUID(conversation["id"])
        )
        assert records == []


# --------------------------------------------------------------------------- #
# Service-level update path
# --------------------------------------------------------------------------- #
class TestServiceUpdatePath:
    def test_existing_record_is_updated_not_duplicated(self, client, db_session):
        """Direct DB record + service submission → updates, never duplicates."""
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers)

        user = db_session.scalars(
            select(User).where(User.email == CUSTOMER_EMAIL)
        ).one()
        db_session.add(
            Feedback(
                customer_id=user.id,
                conversation_id=uuid.UUID(conversation["id"]),
                rating=1,
                comment="original",
            )
        )
        db_session.commit()

        body = _submit_feedback(
            client, headers, conversation["id"], rating=4, comment="updated"
        )
        assert body["rating"] == 4
        assert body["comment"] == "updated"
        assert _feedback_count(db_session) == 1
