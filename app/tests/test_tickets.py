"""Tests for the Support Tickets module (create, list, detail, update, comments)."""

import uuid
from datetime import UTC, datetime

from app.core.security import hash_password
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User, UserRole
from app.schemas.auth import LoginRequest
from app.services.auth import AuthService

API = "/api/v1/tickets"
CHAT_API = "/api/v1/chat"
CUSTOMER_EMAIL = "ticket-customer@example.com"
AGENT_EMAIL = "ticket-agent@example.com"
AGENT_PASSWORD = "AgentPass123!"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _register_verify(client, email=CUSTOMER_EMAIL) -> dict:
    """Register + verify a customer and return its token dict."""
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass123!", "full_name": "Ticket User"},
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


def _create_ticket(client, headers, **overrides) -> dict:
    payload = {
        "subject": "Order not delivered",
        "description": "My order has not arrived yet.",
        **overrides,
    }
    response = client.post(API, json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _create_chat_conversation(client, headers) -> dict:
    """Create a chat conversation via the chat API (for handoff tests)."""
    response = client.post(
        f"{CHAT_API}/conversations",
        json={"subject": "pre-chat", "initial_message": "I need help"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _fresh(db_session, model, obj_id):
    """Fetch an object, bypassing a possibly-stale identity map."""
    obj = db_session.get(model, obj_id)
    db_session.refresh(obj)
    return obj


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #
class TestCreateTicket:
    def test_requires_auth(self, client):
        assert client.post(API, json={}).status_code == 401

    def test_create_ticket_defaults(self, client):
        tokens = _register_verify(client)
        body = _create_ticket(client, _auth_headers(tokens))
        assert body["status"] == "open"
        assert body["priority"] == "medium"
        assert body["subject"] == "Order not delivered"
        assert body["description"] == "My order has not arrived yet."
        assert body["assigned_agent_id"] is None
        assert body["conversation_id"] is None
        assert body["category"] is None
        assert body["resolution_notes"] is None
        assert body["created_at"] and body["updated_at"]

    def test_create_with_category_priority(self, client):
        tokens = _register_verify(client)
        body = _create_ticket(
            client,
            _auth_headers(tokens),
            category="shipping",
            priority="critical",
        )
        assert body["category"] == "shipping"
        assert body["priority"] == "critical"

    def test_create_links_to_owned_conversation(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_chat_conversation(client, headers)

        body = _create_ticket(
            client, headers, conversation_id=conversation["id"]
        )
        assert body["conversation_id"] == conversation["id"]

    def test_create_with_foreign_conversation_404(self, client):
        tokens = _register_verify(client, email="owner@example.com")
        other = _register_verify(client, email="stranger@example.com")
        conversation = _create_chat_conversation(client, _auth_headers(other))

        response = client.post(
            API,
            json={
                "subject": "s",
                "description": "d",
                "conversation_id": conversation["id"],
            },
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 404

    def test_create_with_unknown_conversation_404(self, client):
        tokens = _register_verify(client)
        response = client.post(
            API,
            json={
                "subject": "s",
                "description": "d",
                "conversation_id": str(uuid.uuid4()),
            },
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 404

    def test_create_blank_subject_422(self, client):
        tokens = _register_verify(client)
        response = client.post(
            API,
            json={"subject": "   ", "description": "d"},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 422

    def test_create_blank_description_422(self, client):
        tokens = _register_verify(client)
        response = client.post(
            API,
            json={"subject": "s", "description": "   "},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 422

    def test_create_blank_category_422(self, client):
        tokens = _register_verify(client)
        response = client.post(
            API,
            json={"subject": "s", "description": "d", "category": " "},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 422

    def test_create_missing_required_fields_422(self, client):
        tokens = _register_verify(client)
        assert (
            client.post(API, json={}, headers=_auth_headers(tokens)).status_code == 422
        )
        assert (
            client.post(
                API, json={"subject": "s"}, headers=_auth_headers(tokens)
            ).status_code
            == 422
        )

    def test_create_subject_too_long_422(self, client):
        tokens = _register_verify(client)
        response = client.post(
            API,
            json={"subject": "x" * 256, "description": "d"},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 422

    def test_create_description_too_long_422(self, client):
        tokens = _register_verify(client)
        response = client.post(
            API,
            json={"subject": "s", "description": "x" * 4001},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 422

    def test_create_category_too_long_422(self, client):
        tokens = _register_verify(client)
        response = client.post(
            API,
            json={"subject": "s", "description": "d", "category": "x" * 65},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 422

    def test_create_invalid_priority_422(self, client):
        tokens = _register_verify(client)
        response = client.post(
            API,
            json={"subject": "s", "description": "d", "priority": "urgent"},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #
class TestListTickets:
    def test_requires_auth(self, client):
        assert client.get(API).status_code == 401

    def test_customer_lists_only_own(self, client):
        mine = _register_verify(client)
        _register_verify(client, email="other@example.com")
        other = _register_verify(client, email="stranger@example.com")
        _create_ticket(client, _auth_headers(other), subject="theirs")

        body = client.get(API, headers=_auth_headers(mine)).json()
        assert body["total"] == 0

        _create_ticket(client, _auth_headers(mine), subject="mine")
        body = client.get(API, headers=_auth_headers(mine)).json()
        assert body["total"] == 1
        assert body["items"][0]["subject"] == "mine"

    def test_customer_empty_list(self, client):
        tokens = _register_verify(client)
        body = client.get(API, headers=_auth_headers(tokens)).json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_newest_first(self, client, db_session):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        created = [_create_ticket(client, headers) for _ in range(3)]
        # Stagger created_at (SQLite now() has 1s precision) for determinism.
        for index, ticket_json in enumerate(created):
            record = _fresh(db_session, Ticket, uuid.UUID(ticket_json["id"]))
            record.created_at = datetime(2026, 8, index + 1, 12, 0, tzinfo=UTC)
        db_session.commit()

        body = client.get(API, headers=headers).json()
        ids = [item["id"] for item in body["items"]]
        assert ids == [created[2]["id"], created[1]["id"], created[0]["id"]]

    def test_list_pagination(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        for index in range(5):
            _create_ticket(client, headers, subject=f"topic-{index}")

        page1 = client.get(API, params={"limit": 2}, headers=headers).json()
        assert page1["total"] == 5
        assert len(page1["items"]) == 2

        page2 = client.get(
            API, params={"limit": 2, "offset": 2}, headers=headers
        ).json()
        assert len(page2["items"]) == 2
        first_ids = {item["id"] for item in page1["items"]}
        assert all(item["id"] not in first_ids for item in page2["items"])

    def test_list_limit_bounds_422(self, client):
        tokens = _register_verify(client)
        assert (
            client.get(API, params={"limit": 0}, headers=_auth_headers(tokens)).status_code
            == 422
        )

    def test_filter_by_status(self, client, db_session):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        open_ticket = _create_ticket(client, headers, subject="still open")
        resolved = _create_ticket(client, headers, subject="done")
        record = _fresh(db_session, Ticket, uuid.UUID(resolved["id"]))
        record.status = TicketStatus.RESOLVED
        db_session.commit()

        body = client.get(
            API, params={"status": "resolved"}, headers=headers
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == resolved["id"]

        body = client.get(API, params={"status": "open"}, headers=headers).json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == open_ticket["id"]

    def test_filter_by_priority(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        _create_ticket(client, headers, subject="normal")
        critical = _create_ticket(client, headers, subject="urgent", priority="critical")

        body = client.get(API, params={"priority": "critical"}, headers=headers).json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == critical["id"]

    def test_filter_by_category(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        _create_ticket(client, headers, subject="no category")
        shipping = _create_ticket(client, headers, subject="shipping", category="shipping")

        body = client.get(API, params={"category": "shipping"}, headers=headers).json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == shipping["id"]

    def test_invalid_status_filter_422(self, client):
        tokens = _register_verify(client)
        response = client.get(API, params={"status": "bogus"}, headers=_auth_headers(tokens))
        assert response.status_code == 422

    def test_invalid_priority_filter_422(self, client):
        tokens = _register_verify(client)
        response = client.get(
            API, params={"priority": "bogus"}, headers=_auth_headers(tokens)
        )
        assert response.status_code == 422

    def test_agent_sees_full_queue(self, client, db_session):
        customer_a = _register_verify(client, email="alice@example.com")
        customer_b = _register_verify(client, email="bob@example.com")
        _create_ticket(client, _auth_headers(customer_a), subject="a-one")
        _create_ticket(client, _auth_headers(customer_a), subject="a-two")
        _create_ticket(client, _auth_headers(customer_b), subject="b-one")

        agent_tokens, _agent = _agent_tokens(client, db_session)
        body = client.get(API, headers=_auth_headers(agent_tokens)).json()
        assert body["total"] == 3
        subjects = {item["subject"] for item in body["items"]}
        assert subjects == {"a-one", "a-two", "b-one"}

    def test_admin_sees_full_queue(self, client, db_session):
        customer = _register_verify(client)
        _create_ticket(client, _auth_headers(customer))
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )
        body = client.get(API, headers=_auth_headers(admin_tokens)).json()
        assert body["total"] == 1


# --------------------------------------------------------------------------- #
# Detail
# --------------------------------------------------------------------------- #
class TestGetTicket:
    def test_requires_auth(self, client):
        assert client.get(f"{API}/{uuid.uuid4()}").status_code == 401

    def test_owner_gets_ticket_with_comments(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)
        client.post(
            f"{API}/{ticket['id']}/comments",
            json={"content": "Any update?"},
            headers=headers,
        )

        response = client.get(f"{API}/{ticket['id']}", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == ticket["id"]
        assert len(body["comments"]) == 1
        assert body["comments"][0]["content"] == "Any update?"

    def test_foreign_ticket_404(self, client):
        tokens = _register_verify(client, email="owner@example.com")
        other = _register_verify(client, email="stranger@example.com")
        ticket = _create_ticket(client, _auth_headers(other))

        response = client.get(f"{API}/{ticket['id']}", headers=_auth_headers(tokens))
        assert response.status_code == 404

    def test_missing_ticket_404(self, client):
        tokens = _register_verify(client)
        response = client.get(f"{API}/{uuid.uuid4()}", headers=_auth_headers(tokens))
        assert response.status_code == 404

    def test_invalid_id_422(self, client):
        tokens = _register_verify(client)
        response = client.get(f"{API}/not-a-uuid", headers=_auth_headers(tokens))
        assert response.status_code == 422

    def test_agent_can_view_any_ticket(self, client, db_session):
        customer = _register_verify(client)
        ticket = _create_ticket(client, _auth_headers(customer))
        agent_tokens, _agent = _agent_tokens(client, db_session)

        response = client.get(f"{API}/{ticket['id']}", headers=_auth_headers(agent_tokens))
        assert response.status_code == 200
        assert response.json()["id"] == ticket["id"]

    def test_admin_can_view_any_ticket(self, client, db_session):
        customer = _register_verify(client)
        ticket = _create_ticket(client, _auth_headers(customer))
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )

        response = client.get(f"{API}/{ticket['id']}", headers=_auth_headers(admin_tokens))
        assert response.status_code == 200


# --------------------------------------------------------------------------- #
# Updates (PATCH)
# --------------------------------------------------------------------------- #
class TestUpdateTicket:
    def test_requires_auth(self, client):
        assert client.patch(f"{API}/{uuid.uuid4()}", json={}).status_code == 401

    def test_customer_updates_content_fields(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)

        response = client.patch(
            f"{API}/{ticket['id']}",
            json={
                "subject": "New subject",
                "description": "New description",
                "category": "billing",
            },
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["subject"] == "New subject"
        assert body["description"] == "New description"
        assert body["category"] == "billing"
        # Unchanged fields keep their values.
        assert body["status"] == "open"
        assert body["priority"] == "medium"

    def test_customer_can_clear_category_with_null(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers, category="shipping")

        response = client.patch(
            f"{API}/{ticket['id']}", json={"category": None}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["category"] is None

    def test_customer_cannot_change_status_403(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)

        response = client.patch(
            f"{API}/{ticket['id']}", json={"status": "resolved"}, headers=headers
        )
        assert response.status_code == 403

    def test_customer_cannot_change_priority_403(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)

        response = client.patch(
            f"{API}/{ticket['id']}", json={"priority": "high"}, headers=headers
        )
        assert response.status_code == 403

    def test_customer_cannot_set_resolution_notes_403(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)

        response = client.patch(
            f"{API}/{ticket['id']}", json={"resolution_notes": "nope"}, headers=headers
        )
        assert response.status_code == 403

    def test_customer_cannot_update_foreign_ticket_404(self, client):
        tokens = _register_verify(client, email="owner@example.com")
        other = _register_verify(client, email="stranger@example.com")
        ticket = _create_ticket(client, _auth_headers(other))

        response = client.patch(
            f"{API}/{ticket['id']}",
            json={"subject": "hacked"},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 404

    def test_empty_payload_422(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)

        response = client.patch(f"{API}/{ticket['id']}", json={}, headers=headers)
        assert response.status_code == 422

    def test_explicit_null_subject_422(self, client):
        """Null for a NOT NULL column is a validation error, not a 500."""
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)

        response = client.patch(
            f"{API}/{ticket['id']}", json={"subject": None}, headers=headers
        )
        assert response.status_code == 422

    def test_explicit_null_description_422(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)

        response = client.patch(
            f"{API}/{ticket['id']}", json={"description": None}, headers=headers
        )
        assert response.status_code == 422

    def test_explicit_null_status_422(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        agent_tokens, _agent = _agent_tokens(client, db_session)

        response = client.patch(
            f"{API}/{ticket['id']}",
            json={"status": None},
            headers=_auth_headers(agent_tokens),
        )
        assert response.status_code == 422

    def test_blank_subject_on_update_422(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)

        response = client.patch(
            f"{API}/{ticket['id']}", json={"subject": "   "}, headers=headers
        )
        assert response.status_code == 422

    def test_invalid_status_value_422(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)

        response = client.patch(
            f"{API}/{ticket['id']}", json={"status": "bogus"}, headers=headers
        )
        assert response.status_code == 422

    def test_resolution_notes_too_long_422(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        agent_tokens, _agent = _agent_tokens(client, db_session)

        response = client.patch(
            f"{API}/{ticket['id']}",
            json={"resolution_notes": "x" * 4001},
            headers=_auth_headers(agent_tokens),
        )
        assert response.status_code == 422

    def test_agent_updates_assigned_ticket(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        agent_tokens, agent = _agent_tokens(client, db_session)
        agent_headers = _auth_headers(agent_tokens)
        # Admin assigns the ticket to the agent first.
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )
        assigned = client.patch(
            f"{API}/{ticket['id']}",
            json={"assigned_agent_id": str(agent.id)},
            headers=_auth_headers(admin_tokens),
        )
        assert assigned.status_code == 200

        response = client.patch(
            f"{API}/{ticket['id']}",
            json={
                "status": "in_progress",
                "priority": "high",
                "resolution_notes": "Investigating carrier delay.",
            },
            headers=agent_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "in_progress"
        assert body["priority"] == "high"
        assert body["resolution_notes"] == "Investigating carrier delay."

    def test_agent_claims_unassigned_ticket_on_update(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        agent_tokens, agent = _agent_tokens(client, db_session)

        response = client.patch(
            f"{API}/{ticket['id']}",
            json={"status": "in_progress"},
            headers=_auth_headers(agent_tokens),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["assigned_agent_id"] == str(agent.id)
        assert body["status"] == "in_progress"

    def test_agent_cannot_update_other_agents_ticket_403(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        agent_tokens, agent = _agent_tokens(client, db_session)
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )
        client.patch(
            f"{API}/{ticket['id']}",
            json={"assigned_agent_id": str(agent.id)},
            headers=_auth_headers(admin_tokens),
        )

        other_tokens, _other = _agent_tokens(
            client, db_session, email="other-agent@example.com"
        )
        response = client.patch(
            f"{API}/{ticket['id']}",
            json={"status": "pending"},
            headers=_auth_headers(other_tokens),
        )
        assert response.status_code == 403

    def test_agent_cannot_reassign_403(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        agent_tokens, agent = _agent_tokens(client, db_session)
        other_tokens, other = _agent_tokens(
            client, db_session, email="other-agent@example.com"
        )

        response = client.patch(
            f"{API}/{ticket['id']}",
            json={"assigned_agent_id": str(other.id)},
            headers=_auth_headers(agent_tokens),
        )
        assert response.status_code == 403

    def test_admin_updates_any_ticket(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )

        response = client.patch(
            f"{API}/{ticket['id']}",
            json={"status": "resolved", "resolution_notes": "Handled."},
            headers=_auth_headers(admin_tokens),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "resolved"
        assert body["resolution_notes"] == "Handled."
        # The customer sees the resolved status too.
        customer_view = client.get(
            f"{API}/{ticket['id']}", headers=customer_headers
        ).json()
        assert customer_view["status"] == "resolved"

    def test_admin_reassigns_agent(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        agent_tokens, agent = _agent_tokens(client, db_session)
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )

        response = client.patch(
            f"{API}/{ticket['id']}",
            json={"assigned_agent_id": str(agent.id)},
            headers=_auth_headers(admin_tokens),
        )
        assert response.status_code == 200
        assert response.json()["assigned_agent_id"] == str(agent.id)

        # The assigned agent can now manage the ticket.
        managed = client.patch(
            f"{API}/{ticket['id']}",
            json={"status": "in_progress"},
            headers=_auth_headers(agent_tokens),
        )
        assert managed.status_code == 200

    def test_admin_reassign_unknown_user_404(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )

        response = client.patch(
            f"{API}/{ticket['id']}",
            json={"assigned_agent_id": str(uuid.uuid4())},
            headers=_auth_headers(admin_tokens),
        )
        assert response.status_code == 404

    def test_admin_reassign_non_agent_400(self, client, db_session):
        from sqlalchemy import select

        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        _register_verify(client, email="plain@example.com")
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )

        plain_user = db_session.scalars(
            select(User).where(User.email == "plain@example.com")
        ).one()
        response = client.patch(
            f"{API}/{ticket['id']}",
            json={"assigned_agent_id": str(plain_user.id)},
            headers=_auth_headers(admin_tokens),
        )
        assert response.status_code == 400

    def test_admin_cannot_assign_inactive_agent_400(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        agent_tokens, agent = _agent_tokens(client, db_session)
        agent.is_active = False
        db_session.commit()
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )

        response = client.patch(
            f"{API}/{ticket['id']}",
            json={"assigned_agent_id": str(agent.id)},
            headers=_auth_headers(admin_tokens),
        )
        assert response.status_code == 400

    def test_admin_can_unassign(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        agent_tokens, agent = _agent_tokens(client, db_session)
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )
        client.patch(
            f"{API}/{ticket['id']}",
            json={"assigned_agent_id": str(agent.id)},
            headers=_auth_headers(admin_tokens),
        )

        response = client.patch(
            f"{API}/{ticket['id']}",
            json={"assigned_agent_id": None},
            headers=_auth_headers(admin_tokens),
        )
        assert response.status_code == 200
        assert response.json()["assigned_agent_id"] is None


# --------------------------------------------------------------------------- #
# Comments
# --------------------------------------------------------------------------- #
class TestAddComment:
    def test_requires_auth(self, client):
        assert (
            client.post(
                f"{API}/{uuid.uuid4()}/comments", json={"content": "hi"}
            ).status_code
            == 401
        )

    def test_customer_comments_on_own_ticket(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)

        response = client.post(
            f"{API}/{ticket['id']}/comments",
            json={"content": "Any update?"},
            headers=headers,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["ticket_id"] == ticket["id"]
        assert body["content"] == "Any update?"
        assert body["author_id"] is not None

        detail = client.get(f"{API}/{ticket['id']}", headers=headers).json()
        assert [c["content"] for c in detail["comments"]] == ["Any update?"]

    def test_customer_comments_on_foreign_ticket_404(self, client):
        tokens = _register_verify(client, email="owner@example.com")
        other = _register_verify(client, email="stranger@example.com")
        ticket = _create_ticket(client, _auth_headers(other))

        response = client.post(
            f"{API}/{ticket['id']}/comments",
            json={"content": "sneaky"},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 404

    def test_agent_comments_on_assigned_ticket(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        agent_tokens, agent = _agent_tokens(client, db_session)
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )
        client.patch(
            f"{API}/{ticket['id']}",
            json={"assigned_agent_id": str(agent.id)},
            headers=_auth_headers(admin_tokens),
        )

        response = client.post(
            f"{API}/{ticket['id']}/comments",
            json={"content": "We are on it."},
            headers=_auth_headers(agent_tokens),
        )
        assert response.status_code == 201
        assert response.json()["author_id"] == str(agent.id)

        detail = client.get(f"{API}/{ticket['id']}", headers=customer_headers).json()
        assert detail["comments"][-1]["content"] == "We are on it."

    def test_agent_comment_on_unassigned_ticket_claims_it(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        agent_tokens, agent = _agent_tokens(client, db_session)

        response = client.post(
            f"{API}/{ticket['id']}/comments",
            json={"content": "I will take this one."},
            headers=_auth_headers(agent_tokens),
        )
        assert response.status_code == 201
        assert response.json()["author_id"] == str(agent.id)

        # The comment claim assigned the ticket to the agent.
        record = _fresh(db_session, Ticket, uuid.UUID(ticket["id"]))
        assert record.assigned_agent_id == agent.id

    def test_agent_cannot_comment_other_agents_ticket_403(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        agent_tokens, agent = _agent_tokens(client, db_session)
        admin_tokens, _admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )
        client.patch(
            f"{API}/{ticket['id']}",
            json={"assigned_agent_id": str(agent.id)},
            headers=_auth_headers(admin_tokens),
        )

        other_tokens, _other = _agent_tokens(
            client, db_session, email="other-agent@example.com"
        )
        response = client.post(
            f"{API}/{ticket['id']}/comments",
            json={"content": "not mine"},
            headers=_auth_headers(other_tokens),
        )
        assert response.status_code == 403

    def test_admin_comments_on_any_ticket(self, client, db_session):
        customer = _register_verify(client)
        customer_headers = _auth_headers(customer)
        ticket = _create_ticket(client, customer_headers)
        admin_tokens, admin = _agent_tokens(
            client, db_session, email="admin@example.com", role=UserRole.ADMIN
        )

        response = client.post(
            f"{API}/{ticket['id']}/comments",
            json={"content": "Escalating to billing."},
            headers=_auth_headers(admin_tokens),
        )
        assert response.status_code == 201
        assert response.json()["author_id"] == str(admin.id)

    def test_blank_comment_422(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)

        response = client.post(
            f"{API}/{ticket['id']}/comments",
            json={"content": "   "},
            headers=headers,
        )
        assert response.status_code == 422

    def test_oversized_comment_422(self, client):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        ticket = _create_ticket(client, headers)

        response = client.post(
            f"{API}/{ticket['id']}/comments",
            json={"content": "x" * 2001},
            headers=headers,
        )
        assert response.status_code == 422

    def test_missing_ticket_404(self, client):
        tokens = _register_verify(client)
        response = client.post(
            f"{API}/{uuid.uuid4()}/comments",
            json={"content": "hi"},
            headers=_auth_headers(tokens),
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Chat → Ticket handoff
# --------------------------------------------------------------------------- #
class TestChatHandoff:
    def test_ticket_keeps_link_to_causing_conversation(self, client):
        """The AI Service will create a ticket from a chat conversation the same
        way: link the conversation_id and keep the full context recoverable."""
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_chat_conversation(client, headers)

        ticket = _create_ticket(
            client,
            headers,
            conversation_id=conversation["id"],
            description="The bot could not resolve my issue.",
        )
        assert ticket["conversation_id"] == conversation["id"]

        detail = client.get(f"{API}/{ticket['id']}", headers=headers).json()
        assert detail["conversation_id"] == conversation["id"]

    def test_ticket_survives_conversation_deletion(self, client, db_session):
        """ON DELETE SET NULL: removing the chat must not remove its tickets."""
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_chat_conversation(client, headers)
        ticket = _create_ticket(
            client, headers, conversation_id=conversation["id"]
        )

        # Delete the conversation directly (simulating cleanup).
        from app.models.chat import ChatConversation

        record = _fresh(db_session, ChatConversation, uuid.UUID(conversation["id"]))
        db_session.delete(record)
        db_session.commit()

        detail = client.get(f"{API}/{ticket['id']}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["conversation_id"] is None
