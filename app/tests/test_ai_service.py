"""Tests for the AI Service — Groq is fully mocked, no real API calls, no key.

Covers intent/sentiment, knowledge-base grounding, product tools, order tools
with ownership enforcement, customer profile, ticket creation, human handoff,
conversation context, the bounded context window, fallback responses, and the
chat-module integration.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.ai.ai_service import FALLBACK_MESSAGE, GroqChatReplyProvider
from app.ai.groq_service import GroqService
from app.ai.types import CustomerIntent, CustomerSentiment
from app.db.session import SessionLocal
from app.models.chat import ChatConversation, ChatMessage, ChatMessageSender
from app.models.knowledge import KnowledgeArticle
from app.models.product import Product
from app.models.ticket import Ticket
from app.models.user import User
from app.schemas.chat import ChatConversationCreate, ChatMessageCreate
from app.services.chat import ChatReply, ChatService, build_chat_reply_provider
from app.tests.ai_fakes import FakeGroqClient, classify_json, respond_json

CHAT_API = "/api/v1/chat"
CUSTOMER_EMAIL = "ai-customer@example.com"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _register_verify(client, email=CUSTOMER_EMAIL) -> dict:
    """Register + verify a customer and return its token dict."""
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass123!", "full_name": "AI User"},
    )
    assert response.status_code == 201, response.text
    otp = response.json()["dev_otp"]
    tokens = client.post("/api/v1/auth/verify-otp", json={"email": email, "otp": otp})
    assert tokens.status_code == 200, tokens.text
    return tokens.json()


def _auth_headers(tokens) -> dict:
    access = tokens["access_token"] if isinstance(tokens, dict) else tokens.access_token
    return {"Authorization": f"Bearer {access}"}


def _create_conversation(client, headers, **overrides) -> dict:
    payload = {"subject": "Help needed", **overrides}
    response = client.post(f"{CHAT_API}/conversations", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _seed_product(db_session, **overrides) -> Product:
    defaults = {
        "sku": "SKU-0001",
        "name": "Wireless Mouse",
        "description": "A comfortable wireless mouse.",
        "category": "Electronics",
        "brand": "Acme",
        "price": Decimal("29.99"),
        "stock_quantity": 10,
    }
    product = Product(**{**defaults, **overrides})
    db_session.add(product)
    db_session.commit()
    return product


def _seed_article(db_session, **overrides) -> KnowledgeArticle:
    defaults = {
        "title": "How do I return an item?",
        "content": "You can return any item within 30 days of delivery.",
        "category": "returns",
        "tags": ["return", "refund"],
    }
    article = KnowledgeArticle(**{**defaults, **overrides})
    db_session.add(article)
    db_session.commit()
    return article


def _ready_customer(client, email=CUSTOMER_EMAIL) -> dict:
    """Register + verify a customer with a shipping address."""
    tokens = _register_verify(client, email=email)
    response = client.post(
        "/api/v1/customers/addresses",
        json={
            "label": "Home",
            "recipient_name": "Jane Doe",
            "street": "123 Main St",
            "city": "Springfield",
            "state": "IL",
            "postal_code": "62701",
            "country": "US",
        },
        headers=_auth_headers(tokens),
    )
    assert response.status_code == 201, response.text
    return tokens


def _place_order(client, tokens, product) -> dict:
    response = client.post(
        "/api/v1/orders",
        json={"items": [{"product_id": str(product.id), "quantity": 1}]},
        headers=_auth_headers(tokens),
    )
    assert response.status_code == 201, response.text
    return response.json()


class _RecordingProvider:
    """Wraps a ChatReplyProvider and records the ChatReply it produces."""

    def __init__(self, provider) -> None:
        self.provider = provider
        self.reply: ChatReply | None = None

    def generate_reply(
        self, db, *, user, conversation, customer_message, history
    ) -> ChatReply:
        self.reply = self.provider.generate_reply(
            db,
            user=user,
            conversation=conversation,
            customer_message=customer_message,
            history=history,
        )
        return self.reply


def _run_ai_turn(db_session, conversation_id, content, contents, *, email=CUSTOMER_EMAIL):
    """Drive ChatService.send_message with the fake-Groq provider (real DB).

    Returns (ChatSendResponse, FakeGroqClient, ChatReply) so tests can inspect
    both the API response and the structured AI metadata.
    """
    fake = FakeGroqClient(contents)
    provider = _RecordingProvider(
        GroqChatReplyProvider(
            groq_service=GroqService(client=fake, model="test-model")
        )
    )
    session = SessionLocal()
    try:
        user = session.scalars(select(User).where(User.email == email)).one()
        service = ChatService(session, provider=provider)
        result = service.send_message(
            user, uuid.UUID(conversation_id), ChatMessageCreate(content=content)
        )
    finally:
        session.close()
    return result, fake, provider.reply


def _fresh_conversation(db_session, conversation_id) -> ChatConversation:
    conversation = db_session.get(ChatConversation, uuid.UUID(conversation_id))
    db_session.refresh(conversation)
    return conversation


# --------------------------------------------------------------------------- #
# Intent & sentiment
# --------------------------------------------------------------------------- #
class TestIntentAndSentiment:
    def test_intent_and_sentiment_surface_on_reply(self, client, db_session):
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, _fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "Where is my order?",
            [
                classify_json("order_status", sentiment="frustrated", confidence=0.88),
                respond_json("Your order is being processed."),
            ],
        )
        assert result.bot_message.content == "Your order is being processed."
        assert result.requires_human is False
        assert result.ticket_id is None
        # The structured AI metadata propagates through the turn.
        assert _reply.intent == CustomerIntent.ORDER_STATUS
        assert _reply.sentiment == CustomerSentiment.FRUSTRATED
        assert _reply.confidence == 0.88
        assert _reply.tool_used is None
        # Conversation stays active (no handoff).
        assert _fresh_conversation(db_session, conversation["id"]).status.value == "active"

    def test_history_roles_are_mapped(self, client, db_session):
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation = _create_conversation(
            client, headers, initial_message="Where is my order?"
        )

        fake = FakeGroqClient(
            [
                classify_json("order_status", tool=None),
                respond_json("Sure."),
            ]
        )
        session = SessionLocal()
        try:
            user = session.scalars(select(User).where(User.email == CUSTOMER_EMAIL)).one()
            service = ChatService(
                session,
                provider=GroqChatReplyProvider(
                    groq_service=GroqService(client=fake, model="test-model")
                ),
            )
            service.send_message(
                user,
                uuid.UUID(conversation["id"]),
                ChatMessageCreate(content="what is its status?"),
            )
        finally:
            session.close()

        request = fake.requests[0]
        history = request["messages"][1:]
        assert history[0] == {"role": "user", "content": "Where is my order?"}
        assert history[1]["role"] == "assistant"  # the stub bot reply
        assert history[-1] == {"role": "user", "content": "what is its status?"}


# --------------------------------------------------------------------------- #
# Knowledge base grounding
# --------------------------------------------------------------------------- #
class TestKnowledgeBaseTool:
    def test_knowledge_search_grounds_the_reply(self, client, db_session):
        _seed_article(
            db_session, title="Refund policy", content="Refunds take 5-7 business days."
        )
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "What is your refund policy?",
            [
                classify_json(
                    "knowledge_base_query",
                    tool={"name": "knowledge_search", "arguments": {"q": "refund policy"}},
                ),
                respond_json("Refunds take 5-7 business days."),
            ],
        )
        assert result.bot_message.content == "Refunds take 5-7 business days."
        # The response stage saw the tool result with the real article title.
        respond_request = fake.requests[1]
        assert "[Tool result]" in respond_request["messages"][-1]["content"]
        assert "Refund policy" in respond_request["messages"][-1]["content"]

    def test_knowledge_search_no_results(self, client, db_session):
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "What is your warranty?",
            [
                classify_json(
                    "knowledge_base_query",
                    tool={"name": "knowledge_search", "arguments": {"q": "warranty"}},
                ),
                respond_json("I could not verify that — a human can help."),
            ],
        )
        assert _reply.tool_used == "knowledge_search"
        respond_request = fake.requests[1]
        assert "No knowledge base articles matched" in respond_request["messages"][-1]["content"]


# --------------------------------------------------------------------------- #
# Product tools
# --------------------------------------------------------------------------- #
class TestProductTools:
    def test_product_search_tool(self, client, db_session):
        _seed_product(db_session, sku="SKU-L", name="ProBook Laptop", price=Decimal("999.00"))
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "Do you have laptops?",
            [
                classify_json(
                    "product_search",
                    tool={"name": "product_search", "arguments": {"q": "laptop"}},
                ),
                respond_json("We have the ProBook Laptop."),
            ],
        )
        assert _reply.tool_used == "product_search"
        assert "ProBook Laptop" in fake.requests[1]["messages"][-1]["content"]

    def test_product_recommendation_with_query_searches(self, client, db_session):
        """A natural-language need falls back to search instead of generic picks."""
        _seed_product(db_session, sku="SKU-L", name="ProBook Laptop", price=Decimal("999.00"))
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "I need a laptop for programming.",
            [
                classify_json(
                    "product_recommendation",
                    tool={
                        "name": "product_recommendation",
                        "arguments": {"query": "laptop"},
                    },
                ),
                respond_json("Try the ProBook Laptop."),
            ],
        )
        assert _reply.tool_used == "product_recommendation"
        assert "ProBook Laptop" in fake.requests[1]["messages"][-1]["content"]

    def test_product_recommendation_tool(self, client, db_session):
        _seed_product(db_session, sku="SKU-1", name="Widget", is_featured=True)
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "Recommend something for programming.",
            [
                classify_json(
                    "product_recommendation",
                    tool={"name": "product_recommendation", "arguments": {}},
                ),
                respond_json("Try the Widget."),
            ],
        )
        assert _reply.tool_used == "product_recommendation"
        assert "Widget" in fake.requests[1]["messages"][-1]["content"]

    def test_get_product_tool(self, client, db_session):
        product = _seed_product(db_session, sku="SKU-77", name="Noise Headset")
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, _fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "Tell me about that headset.",
            [
                classify_json(
                    "product_search",
                    tool={"name": "get_product", "arguments": {"product_id": str(product.id)}},
                ),
                respond_json("Here are the details."),
            ],
        )
        assert _reply.tool_used == "get_product"


# --------------------------------------------------------------------------- #
# Order tools (ownership enforced by OrderService)
# --------------------------------------------------------------------------- #
class TestOrderTools:
    def test_order_status_own_order(self, client, db_session):
        tokens = _ready_customer(client)
        product = _seed_product(db_session)
        order = _place_order(client, tokens, product)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "What is my order status?",
            [
                classify_json(
                    "order_status",
                    tool={"name": "order_status", "arguments": {"order_id": order["id"]}},
                ),
                respond_json("Your order is pending."),
            ],
        )
        assert _reply.tool_used == "order_status"
        tool_content = fake.requests[1]["messages"][-1]["content"]
        assert order["order_number"] in tool_content
        assert "pending" in tool_content

    def test_foreign_order_is_never_leaked(self, client, db_session):
        """An order belonging to another customer must not reach the model."""
        owner_tokens = _ready_customer(client, email="owner@example.com")
        product = _seed_product(db_session)
        foreign_order = _place_order(client, owner_tokens, product)

        tokens = _register_verify(client, email="stranger@example.com")
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "What is the status of order 123?",
            [
                classify_json(
                    "order_status",
                    tool={
                        "name": "order_status",
                        "arguments": {"order_id": foreign_order["id"]},
                    },
                ),
                respond_json("I could not find that order."),
            ],
            email="stranger@example.com",
        )
        assert _reply.tool_used == "order_status"
        tool_content = fake.requests[1]["messages"][-1]["content"]
        assert foreign_order["order_number"] not in tool_content
        assert "could not be completed" in tool_content

    def test_list_orders_tool(self, client, db_session):
        tokens = _ready_customer(client)
        product = _seed_product(db_session)
        order = _place_order(client, tokens, product)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "Show my orders.",
            [
                classify_json(
                    "order_status", tool={"name": "list_orders", "arguments": {}}
                ),
                respond_json("Here are your orders."),
            ],
        )
        assert _reply.tool_used == "list_orders"
        assert order["order_number"] in fake.requests[1]["messages"][-1]["content"]

    def test_missing_order_id_argument_is_validated(self, client, db_session):
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "Where is my order?",
            [
                classify_json(
                    "order_status", tool={"name": "order_status", "arguments": {}}
                ),
                respond_json("Can you share your order number?"),
            ],
        )
        assert _reply.tool_used == "order_status"
        assert "could not be completed" in fake.requests[1]["messages"][-1]["content"]


# --------------------------------------------------------------------------- #
# Customer profile tool
# --------------------------------------------------------------------------- #
class TestCustomerProfileTool:
    def test_profile_uses_authenticated_user_only(self, client, db_session):
        tokens = _ready_customer(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "What is my name on file?",
            [
                classify_json(
                    "account_help",
                    tool={"name": "customer_profile", "arguments": {}},
                ),
                respond_json("You are registered as AI User."),
            ],
        )
        assert _reply.tool_used == "customer_profile"
        assert "AI User" in fake.requests[1]["messages"][-1]["content"]


# --------------------------------------------------------------------------- #
# Tickets & human handoff
# --------------------------------------------------------------------------- #
class TestTicketsAndHandoff:
    def test_first_message_handoff_is_applied(self, client, db_session):
        """A handoff signaled on the very first message escalates immediately."""
        _register_verify(client)
        fake = FakeGroqClient(
            [
                classify_json("human_handoff", requires_human=True),
                respond_json("A human agent will join shortly."),
            ]
        )
        provider = GroqChatReplyProvider(
            groq_service=GroqService(client=fake, model="test-model")
        )
        session = SessionLocal()
        try:
            user = session.scalars(select(User).where(User.email == CUSTOMER_EMAIL)).one()
            service = ChatService(session, provider=provider)
            created = service.create_conversation(
                user, ChatConversationCreate(initial_message="Talk to a human please")
            )
        finally:
            session.close()
        assert created.status.value == "escalated"
        assert created.handoff_requested_at is not None

    def test_requires_human_creates_ticket_and_escalates(self, client, db_session):
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, _fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "I am very frustrated, talk to a human now.",
            [
                classify_json("support_request", sentiment="angry", requires_human=True),
                respond_json("I've connected you with a human agent."),
            ],
        )
        assert result.requires_human is True
        assert result.ticket_id is not None
        assert result.bot_message.content == "I've connected you with a human agent."

        # The conversation was escalated by the chat service.
        conversation_record = _fresh_conversation(db_session, conversation["id"])
        assert conversation_record.status.value == "escalated"
        assert conversation_record.handoff_requested_at is not None

        # A ticket was opened and linked to the conversation.
        ticket = db_session.scalars(
            select(Ticket).where(Ticket.conversation_id == conversation_record.id)
        ).one()
        assert ticket.id == result.ticket_id
        assert ticket.user_id == conversation_record.user_id
        assert "AI handoff" in ticket.subject

    def test_create_ticket_tool(self, client, db_session):
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, _fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "I want to request a refund.",
            [
                classify_json(
                    "refund_request",
                    tool={
                        "name": "create_ticket",
                        "arguments": {
                            "subject": "Refund request",
                            "description": "Customer wants a refund for order 123.",
                        },
                    },
                ),
                respond_json("I've opened a ticket for you."),
            ],
        )
        assert result.ticket_id is not None
        assert _reply.tool_used == "create_ticket"
        ticket = db_session.scalars(
            select(Ticket).where(Ticket.conversation_id == uuid.UUID(conversation["id"]))
        ).one()
        assert ticket.subject == "Refund request"
        assert ticket.description == "Customer wants a refund for order 123."

    def test_cancellation_intent_forces_handoff(self, client, db_session):
        """Cancellations/returns/refunds always require a human, even if the
        model does not set requires_human."""
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, _fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "Cancel my order please.",
            [
                classify_json("order_cancellation", requires_human=False),
                respond_json("A human agent will help you cancel the order."),
            ],
        )
        assert result.requires_human is True
        assert result.ticket_id is not None
        assert _fresh_conversation(db_session, conversation["id"]).status.value == "escalated"


# --------------------------------------------------------------------------- #
# Conversation context window
# --------------------------------------------------------------------------- #
class TestContextWindow:
    def test_history_window_is_bounded(self, client, db_session):
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))
        conversation_id = uuid.UUID(conversation["id"])

        # Seed a long history directly (50 messages) so the window matters.
        for index in range(25):
            db_session.add(
                ChatMessage(
                    conversation_id=conversation_id,
                    position=index * 2 + 1,
                    sender_type=ChatMessageSender.CUSTOMER,
                    content=f"customer {index}",
                )
            )
            db_session.add(
                ChatMessage(
                    conversation_id=conversation_id,
                    position=index * 2 + 2,
                    sender_type=ChatMessageSender.BOT,
                    content=f"bot {index}",
                )
            )
        db_session.commit()

        result, fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "latest question",
            [
                classify_json("general_question", tool=None),
                respond_json("Understood."),
            ],
        )
        request = fake.requests[0]
        # System message + at most AI_MAX_CONTEXT_MESSAGES (20) history messages.
        assert len(request["messages"]) <= 21
        assert request["messages"][-1]["content"] == "latest question"
        assert result.bot_message.content == "Understood."


# --------------------------------------------------------------------------- #
# Fallbacks (no key, Groq failure, invalid responses)
# --------------------------------------------------------------------------- #
class TestFallbacks:
    def test_no_api_key_degrades_gracefully(self, db_session, client):
        """The suite must work without a GROQ_API_KEY — calls fall back."""
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        session = SessionLocal()
        try:
            user = session.scalars(select(User).where(User.email == CUSTOMER_EMAIL)).one()
            conversation_id = uuid.UUID(conversation["id"])
            service = ChatService(
                session, provider=GroqChatReplyProvider(groq_service=GroqService(model="x"))
            )
            outcome = service.send_message(
                user, conversation_id, ChatMessageCreate(content="hello")
            )
        finally:
            session.close()
        assert outcome.bot_message.content == FALLBACK_MESSAGE
        assert outcome.requires_human is False

    def test_groq_api_failure_returns_fallback(self, client, db_session):
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        from groq import APIError

        error = APIError.__new__(APIError)
        error.args = ("boom",)
        error.message = "boom"
        fake = FakeGroqClient(contents=[], error=error)
        session = SessionLocal()
        try:
            user = session.scalars(select(User).where(User.email == CUSTOMER_EMAIL)).one()
            service = ChatService(
                session,
                provider=GroqChatReplyProvider(
                    groq_service=GroqService(client=fake, model="test-model")
                ),
            )
            outcome = service.send_message(
                user, uuid.UUID(conversation["id"]), ChatMessageCreate(content="hello")
            )
        finally:
            session.close()
        assert outcome.bot_message.content == FALLBACK_MESSAGE
        # No handoff, no ticket, conversation stays active.
        assert outcome.requires_human is False
        assert outcome.ticket_id is None
        assert _fresh_conversation(db_session, conversation["id"]).status.value == "active"

    def test_invalid_classification_falls_back(self, client, db_session):
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, _fake, _reply = _run_ai_turn(
            db_session, conversation["id"], "hello", ["this is not json"]
        )
        assert result.bot_message.content == FALLBACK_MESSAGE

    def test_invalid_response_falls_back(self, client, db_session):
        tokens = _register_verify(client)
        conversation = _create_conversation(client, _auth_headers(tokens))

        result, _fake, _reply = _run_ai_turn(
            db_session,
            conversation["id"],
            "hello",
            [classify_json("general_question", tool=None), "not json"],
        )
        assert result.bot_message.content == FALLBACK_MESSAGE

    def test_fallback_message_is_clean(self):
        """Customers never see internals: no provider names, keys, or traces."""
        assert "Groq" not in FALLBACK_MESSAGE
        assert "API" not in FALLBACK_MESSAGE
        assert "traceback" not in FALLBACK_MESSAGE.lower()
        assert "key" not in FALLBACK_MESSAGE.lower()

    def test_endpoint_never_500s_on_groq_failure(self, client, db_session, monkeypatch):
        """The chat endpoint returns 200 with a graceful message when Groq is down."""
        tokens = _register_verify(client)
        headers = _auth_headers(tokens)
        conversation_id = _create_conversation(client, headers)["id"]

        from groq import RateLimitError

        error = RateLimitError.__new__(RateLimitError)
        error.args = ("rate limited",)
        error.message = "rate limited"
        provider = GroqChatReplyProvider(
            groq_service=GroqService(client=FakeGroqClient(error=error), model="test-model")
        )
        monkeypatch.setattr(
            "app.services.chat.build_chat_reply_provider", lambda _backend: provider
        )

        response = client.post(
            f"{CHAT_API}/conversations/{conversation_id}/messages",
            json={"content": "hello"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["bot_message"]["content"] == FALLBACK_MESSAGE


# --------------------------------------------------------------------------- #
# Provider factory
# --------------------------------------------------------------------------- #
class TestProviderFactory:
    def test_build_groq_provider(self):
        assert isinstance(build_chat_reply_provider("groq"), GroqChatReplyProvider)

    def test_build_unknown_backend_raises(self):
        with pytest.raises(ValueError):
            build_chat_reply_provider("claude")

    def test_stub_reply_is_structured(self, client, db_session):
        tokens = _register_verify(client)
        conversation = _create_conversation(
            client, _auth_headers(tokens), initial_message="hello"
        )
        provider = build_chat_reply_provider("stub")
        session = SessionLocal()
        try:
            user = session.scalars(select(User).where(User.email == CUSTOMER_EMAIL)).one()
            conversation_record = session.get(ChatConversation, uuid.UUID(conversation["id"]))
            history = list(conversation_record.messages)
            reply = provider.generate_reply(
                session,
                user=user,
                conversation=conversation_record,
                customer_message=history[-1],
                history=history,
            )
        finally:
            session.close()
        assert reply.message
        assert reply.requires_human is False
