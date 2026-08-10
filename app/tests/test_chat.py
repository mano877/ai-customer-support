"""Tests for the Chat module (conversations, messages, handoff, agent queue, feedback)."""

import uuid

from app.core.security import hash_password
from app.models.user import User, UserRole
from app.schemas.auth import LoginRequest
from app.services.auth import AuthService

API = "/api/v1/chat"
CUSTOMER_EMAIL = "chat-customer@example.com"
AGENT_EMAIL = "chat-agent@example.com"
AGENT_PASSWORD = "AgentPass123!"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _register_verify(client, email=CUSTOMER_EMAIL) -> dict:
    """Register + verify a customer and return its token dict."""
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass123!", "full_name": "Chat User"},
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


def _create_conversation(client, headers, **overrides) -> dict:
    payload = {"subject": "Help needed", **overrides}
    response = client.post(f"{API}/conversations", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _escalate(client, headers, conversation_id) -> dict:
    response = client.post(
        f"{API}/conversations/{conversation_id}/escalate", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Creating conversations
# --------------------------------------------------------------------------- #
class TestCreateConversation:
    def test_requires_auth(self, client):
        assert client.post(f"{API}/conversations", json={}).status_code == 401

    def test_create_with_initial_message_generates_bot_reply(self, client):
        tokens = _register_verify(client)
        body = _create_conversation(
            client, _auth_headers(tokens), initial_message="Where is my order?"
        )
        assert body["subject"] == "Help needed"
        assert body["status"] == "active"
        senders = [message["sender_type"] for message in body["messages"]]
        assert senders == ["customer", "bot"]
        assert body["messages"][0]["content"] == "Where is my order?"
        assert body["messages"][0]["sender_user_id"] is not None
        assert body["messages"][1]["sender_user_id"] is None
        assert body["messages"][1]["content"]

    def test_create_without_initial_message(self, client):
        tokens = _register_verify(client)
        body = _create_conversation(client, _auth_headers(tokens))
        assert body["status"] == "active"
        assert body["messages"] == []

    def test_create_subject_optional(self, client):
        tokens = _register_verify(client)
        body = _create_conversation(client, _auth_headers(tokens), subject="Order help")
        assert body["subject"] == "Order help"

        body = client.post(
            f"{API}/conversations", json={}, headers=_auth_headers(tokens)
        ).json()
        assert body["subject"] is None

    def test_create_blank_subject_rejected(self, client):
        tokens = _register_verify(client)
        response = client.post(
            f"{API}/conversations",
            json={"subject": "   "},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 422

    def test_create_blank_initial_message_rejected(self, client):
        tokens = _register_verify(client)
        response = client.post(
            f"{API}/conversations",
            json={"initial_message": "   "},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 422

    def test_create_oversized_initial_message_rejected(self, client):
        tokens = _register_verify(client)
        response = client.post(
            f"{API}/conversations",
            json={"initial_message": "x" * 4001},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Listing & reading conversations
# --------------------------------------------------------------------------- #
class TestListConversations:
    def test_requires_auth(self, client):
        assert client.get(f"{API}/conversations").status_code == 401

    def test_lists_only_own_conversations(self, client):
        mine = _register_verify(client, email="owner@example.com")
        _register_verify(client, email="other@example.com")
        other_headers = _auth_headers(
            _register_verify(client, email="stranger@example.com")
        )
        _create_conversation(client, other_headers)

        body = client.get(f"{API}/conversations", headers=_auth_headers(mine)).json()
        assert body["total"] == 0

        _create_conversation(client, _auth_headers(mine), subject="mine-one")
        body = client.get(f"{API}/conversations", headers=_auth_headers(mine)).json()
        assert body["total"] == 1
        assert body["items"][0]["subject"] == "mine-one"

    def test_lists_newest_first(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        for index in range(3):
            _create_conversation(client, headers, subject=f"topic-{index}")

        body = client.get(f"{API}/conversations", headers=headers).json()
        assert body["total"] == 3
        times = [item["created_at"] for item in body["items"]]
        assert times == sorted(times, reverse=True)
        assert {item["subject"] for item in body["items"]} == {
            "topic-0",
            "topic-1",
            "topic-2",
        }

    def test_list_pagination(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        for index in range(5):
            _create_conversation(client, headers, subject=f"topic-{index}")

        page = client.get(f"{API}/conversations", params={"limit": 2}, headers=headers).json()
        assert page["total"] == 5
        assert len(page["items"]) == 2
        assert page["limit"] == 2

        second = client.get(
            f"{API}/conversations", params={"limit": 2, "offset": 2}, headers=headers
        ).json()
        assert len(second["items"]) == 2
        first_ids = {item["id"] for item in page["items"]}
        assert all(item["id"] not in first_ids for item in second["items"])

    def test_list_limit_bounds(self, client):
        tokens = _register_verify(client)
        assert (
            client.get(
                f"{API}/conversations", params={"limit": 0}, headers=_auth_headers(tokens)
            ).status_code
            == 422
        )


class TestGetConversation:
    def test_requires_auth(self, client):
        assert client.get(f"{API}/conversations/{uuid.uuid4()}").status_code == 401

    def test_returns_full_history_in_order(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(client, headers, initial_message="First hello")
        conversation_id = conversation["id"]
        client.post(
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": "Second hello"},
            headers=headers,
        )

        body = client.get(f"{API}/conversations/{conversation_id}", headers=headers).json()
        senders = [message["sender_type"] for message in body["messages"]]
        assert senders == ["customer", "bot", "customer", "bot"]
        assert [message["content"] for message in body["messages"]][::2] == [
            "First hello",
            "Second hello",
        ]

    def test_foreign_conversation_404(self, client):
        tokens = _register_verify(client, email="owner@example.com")
        _register_verify(client, email="other@example.com")
        other = _register_verify(client, email="stranger@example.com")
        conversation = _create_conversation(client, _auth_headers(other))

        response = client.get(
            f"{API}/conversations/{conversation['id']}", headers=_auth_headers(tokens)
        )
        assert response.status_code == 404

    def test_invalid_id_422(self, client):
        tokens = _register_verify(client)
        response = client.get(
            f"{API}/conversations/not-a-uuid", headers=_auth_headers(tokens)
        )
        assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Sending messages
# --------------------------------------------------------------------------- #
class TestSendMessage:
    def test_requires_auth(self, client):
        assert (
            client.post(
                f"{API}/conversations/{uuid.uuid4()}/messages", json={"content": "hi"}
            ).status_code
            == 401
        )

    def test_send_message_returns_customer_and_bot_messages(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]

        response = client.post(
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": "I need help with my order"},
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["customer_message"]["sender_type"] == "customer"
        assert body["customer_message"]["content"] == "I need help with my order"
        assert body["bot_message"]["sender_type"] == "bot"
        assert body["bot_message"]["content"]

        history = client.get(
            f"{API}/conversations/{conversation_id}", headers=headers
        ).json()
        assert [m["sender_type"] for m in history["messages"]] == ["customer", "bot"]

    def test_send_message_blank_content_rejected(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]
        response = client.post(
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": "   "},
            headers=headers,
        )
        assert response.status_code == 422

    def test_send_message_oversized_content_rejected(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]
        response = client.post(
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": "x" * 4001},
            headers=headers,
        )
        assert response.status_code == 422

    def test_send_message_foreign_conversation_404(self, client):
        tokens = _register_verify(client, email="owner@example.com")
        other = _register_verify(client, email="stranger@example.com")
        conversation_id = _create_conversation(client, _auth_headers(other))["id"]

        response = client.post(
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": "hi"},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 404

    def test_send_message_on_resolved_conversation_conflict(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]
        client.post(f"{API}/conversations/{conversation_id}/resolve", headers=headers)

        response = client.post(
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": "hello again"},
            headers=headers,
        )
        assert response.status_code == 409

    def test_send_message_after_escalation_stores_without_bot_reply(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]
        _escalate(client, headers, conversation_id)

        response = client.post(
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": "Can anyone help me now?"},
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["customer_message"]["content"] == "Can anyone help me now?"
        assert body["bot_message"] is None

        history = client.get(
            f"{API}/conversations/{conversation_id}", headers=headers
        ).json()
        assert history["messages"][-1]["sender_type"] == "customer"


# --------------------------------------------------------------------------- #
# Human handoff (escalation)
# --------------------------------------------------------------------------- #
class TestEscalate:
    def test_requires_auth(self, client):
        assert (
            client.post(f"{API}/conversations/{uuid.uuid4()}/escalate").status_code == 401
        )

    def test_escalate_marks_conversation_and_adds_system_message(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]

        body = _escalate(client, headers, conversation_id)
        assert body["status"] == "escalated"
        assert body["handoff_requested_at"] is not None

        history = client.get(
            f"{API}/conversations/{conversation_id}", headers=headers
        ).json()
        assert history["status"] == "escalated"
        system_messages = [
            m for m in history["messages"] if m["sender_type"] == "system"
        ]
        assert len(system_messages) == 1
        assert "human agent" in system_messages[0]["content"].lower()

    def test_escalate_twice_conflict(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]
        _escalate(client, headers, conversation_id)

        response = client.post(
            f"{API}/conversations/{conversation_id}/escalate", headers=headers
        )
        assert response.status_code == 409

    def test_escalate_resolved_conversation_conflict(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]
        client.post(f"{API}/conversations/{conversation_id}/resolve", headers=headers)

        response = client.post(
            f"{API}/conversations/{conversation_id}/escalate", headers=headers
        )
        assert response.status_code == 409

    def test_escalate_foreign_conversation_404(self, client):
        tokens = _register_verify(client, email="owner@example.com")
        other = _register_verify(client, email="stranger@example.com")
        conversation_id = _create_conversation(client, _auth_headers(other))["id"]

        response = client.post(
            f"{API}/conversations/{conversation_id}/escalate",
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Resolving (customer side)
# --------------------------------------------------------------------------- #
class TestResolve:
    def test_requires_auth(self, client):
        assert (
            client.post(f"{API}/conversations/{uuid.uuid4()}/resolve").status_code == 401
        )

    def test_customer_resolves_own_conversation(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]

        response = client.post(
            f"{API}/conversations/{conversation_id}/resolve", headers=headers
        )
        assert response.status_code == 200
        assert response.json()["status"] == "resolved"
        assert response.json()["resolved_at"] is not None

        history = client.get(
            f"{API}/conversations/{conversation_id}", headers=headers
        ).json()
        assert history["messages"][-1]["sender_type"] == "system"

    def test_resolve_escalated_conversation(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]
        _escalate(client, headers, conversation_id)

        response = client.post(
            f"{API}/conversations/{conversation_id}/resolve", headers=headers
        )
        assert response.status_code == 200
        assert response.json()["status"] == "resolved"

    def test_resolve_twice_conflict(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]
        client.post(f"{API}/conversations/{conversation_id}/resolve", headers=headers)

        response = client.post(
            f"{API}/conversations/{conversation_id}/resolve", headers=headers
        )
        assert response.status_code == 409

    def test_resolve_foreign_conversation_404(self, client):
        tokens = _register_verify(client, email="owner@example.com")
        other = _register_verify(client, email="stranger@example.com")
        conversation_id = _create_conversation(client, _auth_headers(other))["id"]

        response = client.post(
            f"{API}/conversations/{conversation_id}/resolve",
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Feedback
# --------------------------------------------------------------------------- #
class TestFeedback:
    def test_requires_auth(self, client):
        assert (
            client.post(
                f"{API}/conversations/{uuid.uuid4()}/feedback", json={"rating": 5}
            ).status_code
            == 401
        )

    def test_feedback_on_resolved_conversation(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]
        client.post(f"{API}/conversations/{conversation_id}/resolve", headers=headers)

        response = client.post(
            f"{API}/conversations/{conversation_id}/feedback",
            json={"rating": 5, "comment": "Great help!"},
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["rating"] == 5
        assert body["feedback_comment"] == "Great help!"

    def test_feedback_on_active_conversation_rejected(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]

        response = client.post(
            f"{API}/conversations/{conversation_id}/feedback",
            json={"rating": 5},
            headers=headers,
        )
        assert response.status_code == 400

    def test_feedback_duplicate_conflict(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]
        client.post(f"{API}/conversations/{conversation_id}/resolve", headers=headers)

        first = client.post(
            f"{API}/conversations/{conversation_id}/feedback",
            json={"rating": 4},
            headers=headers,
        )
        assert first.status_code == 200

        second = client.post(
            f"{API}/conversations/{conversation_id}/feedback",
            json={"rating": 5},
            headers=headers,
        )
        assert second.status_code == 409

    def test_feedback_rating_out_of_range_rejected(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]
        client.post(f"{API}/conversations/{conversation_id}/resolve", headers=headers)

        for rating in (0, 6):
            response = client.post(
                f"{API}/conversations/{conversation_id}/feedback",
                json={"rating": rating},
                headers=headers,
            )
            assert response.status_code == 422

    def test_feedback_foreign_conversation_404(self, client):
        tokens = _register_verify(client, email="owner@example.com")
        other = _register_verify(client, email="stranger@example.com")
        conversation_id = _create_conversation(client, _auth_headers(other))["id"]

        response = client.post(
            f"{API}/conversations/{conversation_id}/feedback",
            json={"rating": 5},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Support agent queue
# --------------------------------------------------------------------------- #
class TestAgentQueue:
    def _agent_setup(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        active = _create_conversation(client, customer_headers, subject="active chat")
        escalated = _create_conversation(
            client, customer_headers, subject="escalated chat"
        )
        _escalate(client, customer_headers, escalated["id"])
        agent_tokens, agent = _agent_tokens(client, db_session)
        return escalated["id"], active["id"], _auth_headers(agent_tokens), agent

    def test_customer_forbidden(self, client):
        tokens = _register_verify(client)
        response = client.get(f"{API}/agent/conversations", headers=_auth_headers(tokens))
        assert response.status_code == 403

    def test_requires_auth(self, client):
        assert client.get(f"{API}/agent/conversations").status_code == 401

    def test_lists_escalated_conversations_by_default(self, client, db_session):
        escalated_id, _active_id, agent_headers, _agent = self._agent_setup(
            client, db_session
        )

        body = client.get(f"{API}/agent/conversations", headers=agent_headers).json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == escalated_id
        assert body["items"][0]["status"] == "escalated"

    def test_list_by_status_filter(self, client, db_session):
        escalated_id, _active_id, agent_headers, _agent = self._agent_setup(
            client, db_session
        )

        active = client.get(
            f"{API}/agent/conversations",
            params={"status": "active"},
            headers=agent_headers,
        ).json()
        assert active["total"] == 1
        assert active["items"][0]["id"] != escalated_id

        resolved = client.get(
            f"{API}/agent/conversations",
            params={"status": "resolved"},
            headers=agent_headers,
        ).json()
        assert resolved["total"] == 0

    def test_invalid_status_rejected(self, client, db_session):
        _escalated, _active, agent_headers, _agent = self._agent_setup(client, db_session)
        response = client.get(
            f"{API}/agent/conversations",
            params={"status": "bogus"},
            headers=agent_headers,
        )
        assert response.status_code == 422

    def test_agent_conversation_invalid_id_422(self, client, db_session):
        _escalated, _active, agent_headers, _agent = self._agent_setup(client, db_session)
        response = client.get(
            f"{API}/agent/conversations/not-a-uuid", headers=agent_headers
        )
        assert response.status_code == 422

    def test_agent_view_missing_conversation_404(self, client, db_session):
        _escalated, _active, agent_headers, _agent = self._agent_setup(client, db_session)
        response = client.get(
            f"{API}/agent/conversations/{uuid.uuid4()}", headers=agent_headers
        )
        assert response.status_code == 404

    def test_agent_can_view_any_conversation(self, client, db_session):
        escalated_id, active_id, agent_headers, _agent = self._agent_setup(
            client, db_session
        )

        for conversation_id in (escalated_id, active_id):
            response = client.get(
                f"{API}/agent/conversations/{conversation_id}", headers=agent_headers
            )
            assert response.status_code == 200
            assert response.json()["id"] == conversation_id

    def test_admin_can_access_agent_queue(self, client, db_session):
        _register_verify(client)
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )
        response = client.get(
            f"{API}/agent/conversations", headers=_auth_headers(admin_tokens)
        )
        assert response.status_code == 200


class TestClaim:
    def _escalated_conversation(self, client, db_session):
        escalated_id, _active, _headers, agent = self._setup(client, db_session)
        return escalated_id, agent

    @staticmethod
    def _setup(client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        escalated = _create_conversation(client, customer_headers, subject="help me")
        _escalate(client, customer_headers, escalated["id"])
        agent_tokens, agent = _agent_tokens(client, db_session)
        return escalated["id"], _auth_headers(agent_tokens), agent

    def test_claim_assigns_agent(self, client, db_session):
        escalated_id, agent_headers, agent = self._setup(client, db_session)

        response = client.post(
            f"{API}/agent/conversations/{escalated_id}/claim", headers=agent_headers
        )
        assert response.status_code == 200
        body = response.json()
        assert body["assigned_agent_id"] == str(agent.id)
        assert body["assigned_at"] is not None
        assert body["status"] == "escalated"

    def test_claim_twice_conflict(self, client, db_session):
        escalated_id, agent_headers, _agent = self._setup(client, db_session)
        client.post(f"{API}/agent/conversations/{escalated_id}/claim", headers=agent_headers)

        second_tokens, _second = _agent_tokens(
            client, db_session, email="second@example.com"
        )
        response = client.post(
            f"{API}/agent/conversations/{escalated_id}/claim",
            headers=_auth_headers(second_tokens),
        )
        assert response.status_code == 409

    def test_claim_active_conversation_conflict(self, client, db_session):
        customer = _register_verify(client)
        active_id = _create_conversation(
            client, _auth_headers(customer), subject="not escalated"
        )["id"]
        agent_tokens, _agent = _agent_tokens(client, db_session)

        response = client.post(
            f"{API}/agent/conversations/{active_id}/claim",
            headers=_auth_headers(agent_tokens),
        )
        assert response.status_code == 409

    def test_claim_missing_conversation_404(self, client, db_session):
        agent_tokens, _agent = _agent_tokens(client, db_session)
        response = client.post(
            f"{API}/agent/conversations/{uuid.uuid4()}/claim",
            headers=_auth_headers(agent_tokens),
        )
        assert response.status_code == 404

    def test_customer_cannot_claim(self, client, db_session):
        escalated_id, _headers, _agent = self._setup(client, db_session)
        customer = _register_verify(client, email="other-customer@example.com")
        response = client.post(
            f"{API}/agent/conversations/{escalated_id}/claim",
            headers=_auth_headers(customer),
        )
        assert response.status_code == 403


class TestAgentMessages:
    def _claimed_setup(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        escalated = _create_conversation(client, customer_headers, subject="help")
        _escalate(client, customer_headers, escalated["id"])
        agent_tokens, agent = _agent_tokens(client, db_session)
        agent_headers = _auth_headers(agent_tokens)
        client.post(
            f"{API}/agent/conversations/{escalated['id']}/claim", headers=agent_headers
        )
        return escalated["id"], agent_headers, agent, customer_headers

    def test_agent_reply_requires_claim(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        escalated = _create_conversation(client, customer_headers, subject="help")
        _escalate(client, customer_headers, escalated["id"])
        agent_tokens, _agent = _agent_tokens(client, db_session)

        response = client.post(
            f"{API}/agent/conversations/{escalated['id']}/messages",
            json={"content": "I'll help you"},
            headers=_auth_headers(agent_tokens),
        )
        assert response.status_code == 403

    def test_other_agent_cannot_reply(self, client, db_session):
        escalated_id, agent_headers, _agent, _customer_headers = self._claimed_setup(
            client, db_session
        )
        other_tokens, _other = _agent_tokens(
            client, db_session, email="other-agent@example.com"
        )
        response = client.post(
            f"{API}/agent/conversations/{escalated_id}/messages",
            json={"content": "sneaky"},
            headers=_auth_headers(other_tokens),
        )
        assert response.status_code == 403

    def test_agent_reply_is_stored_and_visible_to_customer(self, client, db_session):
        escalated_id, agent_headers, agent, customer_headers = self._claimed_setup(
            client, db_session
        )
        response = client.post(
            f"{API}/agent/conversations/{escalated_id}/messages",
            json={"content": "I'll help you with your order."},
            headers=agent_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["sender_type"] == "agent"
        assert body["sender_user_id"] == str(agent.id)

        history = client.get(
            f"{API}/conversations/{escalated_id}", headers=customer_headers
        ).json()
        assert history["messages"][-1]["sender_type"] == "agent"
        assert history["messages"][-1]["content"] == "I'll help you with your order."

    def test_agent_reply_blank_content_rejected(self, client, db_session):
        escalated_id, agent_headers, _agent, _customer = self._claimed_setup(
            client, db_session
        )
        response = client.post(
            f"{API}/agent/conversations/{escalated_id}/messages",
            json={"content": ""},
            headers=agent_headers,
        )
        assert response.status_code == 422

    def test_agent_reply_on_resolved_conversation_conflict(self, client, db_session):
        escalated_id, agent_headers, _agent, _customer = self._claimed_setup(
            client, db_session
        )
        client.post(
            f"{API}/agent/conversations/{escalated_id}/resolve", headers=agent_headers
        )
        response = client.post(
            f"{API}/agent/conversations/{escalated_id}/messages",
            json={"content": "too late"},
            headers=agent_headers,
        )
        assert response.status_code == 409


class TestAgentResolve:
    def test_agent_resolves_claimed_conversation(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        escalated = _create_conversation(client, customer_headers, subject="help")
        _escalate(client, customer_headers, escalated["id"])
        agent_tokens, _agent = _agent_tokens(client, db_session)
        agent_headers = _auth_headers(agent_tokens)
        client.post(
            f"{API}/agent/conversations/{escalated['id']}/claim", headers=agent_headers
        )

        response = client.post(
            f"{API}/agent/conversations/{escalated['id']}/resolve", headers=agent_headers
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "resolved"
        assert body["resolved_at"] is not None

        history = client.get(
            f"{API}/conversations/{escalated['id']}", headers=customer_headers
        ).json()
        assert history["messages"][-1]["sender_type"] == "system"

    def test_unclaimed_resolve_forbidden(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        escalated = _create_conversation(client, customer_headers, subject="help")
        _escalate(client, customer_headers, escalated["id"])
        agent_tokens, _agent = _agent_tokens(client, db_session)

        response = client.post(
            f"{API}/agent/conversations/{escalated['id']}/resolve",
            headers=_auth_headers(agent_tokens),
        )
        assert response.status_code == 403

    def test_agent_resolve_twice_conflict(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        escalated = _create_conversation(client, customer_headers, subject="help")
        _escalate(client, customer_headers, escalated["id"])
        agent_tokens, _agent = _agent_tokens(client, db_session)
        agent_headers = _auth_headers(agent_tokens)
        client.post(
            f"{API}/agent/conversations/{escalated['id']}/claim", headers=agent_headers
        )
        client.post(
            f"{API}/agent/conversations/{escalated['id']}/resolve", headers=agent_headers
        )

        response = client.post(
            f"{API}/agent/conversations/{escalated['id']}/resolve", headers=agent_headers
        )
        assert response.status_code == 409


# --------------------------------------------------------------------------- #
# Message ordering across the full lifecycle
# --------------------------------------------------------------------------- #
class TestMessageOrdering:
    def test_full_lifecycle_history_order_is_preserved(self, client, db_session):
        """Message history keeps insertion order across every state change.

        Guards the per-conversation ``position`` invariant: customer and bot
        messages, escalation/claim/resolution system notices, and agent replies
        must all appear in the exact order they were written.
        """
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        conversation_id = _create_conversation(
            client, customer_headers, initial_message="hello"
        )["id"]
        client.post(
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": "follow up"},
            headers=customer_headers,
        )
        _escalate(client, customer_headers, conversation_id)
        agent_tokens, _agent = _agent_tokens(client, db_session)
        agent_headers = _auth_headers(agent_tokens)
        client.post(
            f"{API}/agent/conversations/{conversation_id}/claim", headers=agent_headers
        )
        client.post(
            f"{API}/agent/conversations/{conversation_id}/messages",
            json={"content": "agent reply"},
            headers=agent_headers,
        )
        client.post(
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": "thanks"},
            headers=customer_headers,
        )
        client.post(
            f"{API}/agent/conversations/{conversation_id}/resolve", headers=agent_headers
        )

        body = client.get(
            f"{API}/conversations/{conversation_id}", headers=customer_headers
        ).json()
        senders = [message["sender_type"] for message in body["messages"]]
        assert senders == [
            "customer",
            "bot",
            "customer",
            "bot",
            "system",
            "system",
            "agent",
            "customer",
            "system",
        ]
        contents = [message["content"] for message in body["messages"]]
        assert contents[4] == "A human agent has been requested for this conversation."
        assert contents[5] == "A support agent has joined the conversation."
        assert contents[6] == "agent reply"
        assert contents[7] == "thanks"
        assert contents[8] == "Conversation resolved by a support agent."


# --------------------------------------------------------------------------- #
# AI provider modularity
# --------------------------------------------------------------------------- #
class TestReplyProviderSwap:
    def test_custom_provider_is_used_for_bot_replies(self, client):
        """Any ChatReplyProvider implementation can be wired in via the service.

        The AI Service module constructs the Groq-backed provider and passes it
        the same way; the endpoint path uses the stub through the configured
        CHAT_REPLY_BACKEND.
        """
        from sqlalchemy import select

        from app.db.session import SessionLocal
        from app.schemas.chat import ChatMessageCreate
        from app.services.chat import ChatReply, ChatService

        class FixedReplyProvider:
            def generate_reply(
                self, db, *, user, conversation, customer_message, history
            ) -> ChatReply:
                return ChatReply(message=f"Echo: {customer_message.content}")

        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = uuid.UUID(_create_conversation(client, headers)["id"])

        # Drive the service directly with the custom provider (as the AI module
        # will later), and assert its reply is what lands in the database.
        session = SessionLocal()
        try:
            user = session.scalars(
                select(User).where(User.email == CUSTOMER_EMAIL)
            ).one()
            service = ChatService(session, provider=FixedReplyProvider())
            result = service.send_message(
                user, conversation_id, ChatMessageCreate(content="ping")
            )
            assert result.bot_message.content == "Echo: ping"
        finally:
            session.close()

        history = client.get(
            f"{API}/conversations/{conversation_id}", headers=headers
        ).json()
        assert history["messages"][-1]["content"] == "Echo: ping"
