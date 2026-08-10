"""AI Service: orchestrates one chat turn against the Groq-backed model.

Flow per customer message:

    classify (intent + sentiment + tool request + handoff flag)
      → execute the requested tool through the existing business services
      → open a support ticket when a handoff is warranted
      → generate the final customer-facing reply

The service talks only to business services — never to the database directly —
and never trusts ids produced by the model: every customer-scoped lookup
resolves through the authenticated user from the request/session (orders and
tickets are ownership-checked by their services). The service implements the
chat module's ``ChatReplyProvider`` protocol, so the Chat module talks to the
AI Service, never to Groq.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from app.ai.groq_service import GroqService, GroqServiceError, build_groq_service
from app.ai.types import (
    CustomerIntent,
    CustomerSentiment,
    IntentClassification,
    ToolRequest,
)
from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.chat import ChatConversation, ChatMessage, ChatMessageSender
from app.models.user import User
from app.schemas.ticket import DESCRIPTION_MAX_LENGTH, SUBJECT_MAX_LENGTH, TicketCreate
from app.services.chat import ChatReply
from app.services.customer import CustomerService
from app.services.knowledge import KnowledgeService
from app.services.order import OrderService
from app.services.product import ProductService
from app.services.ticket import TicketService

logger = logging.getLogger(__name__)

# Intents that always require a human, regardless of the model's flag —
# cancellations, returns, and refunds need human confirmation.
_FORCED_HANDOFF_INTENTS = frozenset(
    {
        CustomerIntent.ORDER_CANCELLATION,
        CustomerIntent.RETURN_REQUEST,
        CustomerIntent.REFUND_REQUEST,
        CustomerIntent.HUMAN_HANDOFF,
    }
)

FALLBACK_MESSAGE = (
    "I'm sorry — I'm having trouble reaching my assistant right now. Please "
    "try again in a moment, or a member of our support team will be happy to "
    "help you."
)

_TOOL_DISPATCH = {
    "knowledge_search": "_tool_knowledge_search",
    "product_search": "_tool_product_search",
    "product_recommendation": "_tool_product_recommendation",
    "get_product": "_tool_get_product",
    "list_orders": "_tool_list_orders",
    "order_status": "_tool_order_status",
    "order_tracking": "_tool_order_tracking",
    "order_details": "_tool_order_details",
    "customer_profile": "_tool_customer_profile",
    "create_ticket": "_tool_create_ticket",
}


def build_history_messages(
    history: list[ChatMessage], *, max_messages: int
) -> list[dict[str, str]]:
    """Convert chat messages to OpenAI-style roles, bounded by the window.

    customer → user, bot/agent → assistant, system → system. Only the last
    ``max_messages`` are sent to Groq so the context window stays bounded; the
    most recent message (the one being answered) is always included.
    """
    role_by_sender = {
        ChatMessageSender.CUSTOMER: "user",
        ChatMessageSender.BOT: "assistant",
        ChatMessageSender.AGENT: "assistant",
        # System notices (escalation messages etc.) are folded into the user
        # role so the API only ever sees one system message (the prompt);
        # some providers treat later system messages as overrides.
        ChatMessageSender.SYSTEM: "user",
    }
    messages = [
        {"role": role_by_sender.get(message.sender_type, "user"), "content": message.content}
        for message in history
    ]
    return messages[-max_messages:] if len(messages) > max_messages else messages


class AIService:
    """Orchestrates one AI chat turn (classification → tools → reply)."""

    def __init__(
        self, db: Session, *, groq_service: GroqService | None = None
    ) -> None:
        self.db = db
        self.groq = groq_service or build_groq_service()
        self.settings = get_settings()
        self.knowledge = KnowledgeService(db)
        self.products = ProductService(db)
        self.orders = OrderService(db)
        self.customers = CustomerService(db)
        self.tickets = TicketService(db)

    # ------------------------------------------------------------------ #
    # Public entry point (ChatReplyProvider contract)
    # ------------------------------------------------------------------ #
    def handle_turn(
        self,
        *,
        user: User,
        conversation: ChatConversation,
        customer_message: ChatMessage,
        history: list[ChatMessage],
    ) -> ChatReply:
        """Run the full turn. Always returns a ChatReply — never raises.

        Any Groq or tooling failure degrades to a safe fallback message so the
        customer never sees an internal error or stack trace.
        """
        try:
            return self._handle_turn_impl(
                user=user,
                conversation=conversation,
                customer_message=customer_message,
                history=history,
            )
        except GroqServiceError as exc:
            logger.warning("AI turn degraded to fallback: %s", exc)
            return self._fallback_reply()
        except Exception:
            logger.exception("Unexpected failure in AI turn")
            return self._fallback_reply()

    def _handle_turn_impl(
        self,
        *,
        user: User,
        conversation: ChatConversation,
        customer_message: ChatMessage,
        history: list[ChatMessage],
    ) -> ChatReply:
        history_messages = build_history_messages(
            history, max_messages=self.settings.AI_MAX_CONTEXT_MESSAGES
        )
        classification = self.groq.classify_intent(
            subject=conversation.subject, history=history_messages
        )

        tool_used: str | None = None
        tool_result: str | None = None
        ticket_id: uuid.UUID | None = None
        if classification.tool_request is not None:
            tool_used, tool_result, ticket_id = self._execute_tool(
                classification.tool_request, user=user, conversation=conversation
            )

        requires_human = classification.requires_human or (
            classification.intent in _FORCED_HANDOFF_INTENTS
        )
        if requires_human and ticket_id is None:
            ticket_id = self._create_handoff_ticket(
                user=user,
                conversation=conversation,
                classification=classification,
                customer_message=customer_message,
            )

        message = self.groq.generate_response(
            subject=conversation.subject,
            history=history_messages,
            tool_result=tool_result,
        )
        return ChatReply(
            message=message,
            intent=classification.intent,
            sentiment=classification.sentiment,
            confidence=classification.confidence,
            requires_human=requires_human,
            ticket_id=ticket_id,
            tool_used=tool_used,
        )

    # ------------------------------------------------------------------ #
    # Tool execution (via business services only)
    # ------------------------------------------------------------------ #
    def _execute_tool(
        self,
        tool: ToolRequest,
        *,
        user: User,
        conversation: ChatConversation,
    ) -> tuple[str, str, uuid.UUID | None]:
        """Execute one validated tool; returns (name, result_text, ticket_id)."""
        method_name = _TOOL_DISPATCH.get(tool.name)
        if method_name is None:
            return tool.name, f"Unknown tool: {tool.name!r}.", None
        handler = getattr(self, method_name)
        try:
            return handler(tool.arguments, user=user, conversation=conversation)
        except (NotFoundError, BadRequestError, ConflictError, ValueError) as exc:
            # Ownership failures and invalid arguments become context for the
            # model (e.g. "order not found") instead of raising.
            return tool.name, f"The request could not be completed: {exc}", None
        except Exception:
            logger.exception("Tool %r failed unexpectedly", tool.name)
            return tool.name, "The request could not be completed.", None

    def _tool_knowledge_search(
        self, args: dict, *, user: User, conversation: ChatConversation
    ) -> tuple[str, str, uuid.UUID | None]:
        query = self._require_str(args, "q", "query")
        articles = self.knowledge.search_articles(query, limit=5)
        if not articles:
            return "knowledge_search", "No knowledge base articles matched the query.", None
        return "knowledge_search", "\n".join(_format_article(a) for a in articles), None

    def _tool_product_search(
        self, args: dict, *, user: User, conversation: ChatConversation
    ) -> tuple[str, str, uuid.UUID | None]:
        query = self._require_str(args, "q", "query")
        result = self.products.search_products(query, limit=5)
        if not result.items:
            return "product_search", "No products matched the search.", None
        return "product_search", "\n".join(_format_product(p) for p in result.items), None

    def _tool_product_recommendation(
        self, args: dict, *, user: User, conversation: ChatConversation
    ) -> tuple[str, str, uuid.UUID | None]:
        query = self._require_optional_str(args, "query")
        if query:
            # A need expressed in natural language is better served by a
            # search than by generic recommendations.
            result = self.products.search_products(query, limit=5)
            products = result.items
        else:
            products = self.products.get_recommendations(limit=5)
        if not products:
            return "product_recommendation", "No recommendations available.", None
        return "product_recommendation", "\n".join(_format_product(p) for p in products), None

    def _tool_get_product(
        self, args: dict, *, user: User, conversation: ChatConversation
    ) -> tuple[str, str, uuid.UUID | None]:
        product_id = self._require_uuid(args, "product_id")
        product = self.products.get_product(product_id)
        return "get_product", _format_product(product), None

    def _tool_list_orders(
        self, args: dict, *, user: User, conversation: ChatConversation
    ) -> tuple[str, str, uuid.UUID | None]:
        result = self.orders.list_orders(user, limit=10)
        if not result.items:
            return "list_orders", "The customer has no orders.", None
        return "list_orders", "\n".join(_format_order(order) for order in result.items), None

    def _tool_order_status(
        self, args: dict, *, user: User, conversation: ChatConversation
    ) -> tuple[str, str, uuid.UUID | None]:
        order_id = self._require_uuid(args, "order_id")
        order = self.orders.get_order(user, order_id)  # ownership-checked by the service
        return "order_status", _format_order(order), None

    def _tool_order_tracking(
        self, args: dict, *, user: User, conversation: ChatConversation
    ) -> tuple[str, str, uuid.UUID | None]:
        order_id = self._require_uuid(args, "order_id")
        tracking = self.orders.get_tracking(user, order_id)  # ownership-checked
        return "order_tracking", _format_tracking(tracking), None

    def _tool_order_details(
        self, args: dict, *, user: User, conversation: ChatConversation
    ) -> tuple[str, str, uuid.UUID | None]:
        order_id = self._require_uuid(args, "order_id")
        order = self.orders.get_order(user, order_id)  # ownership-checked
        return "order_details", _format_order(order), None

    def _tool_customer_profile(
        self, args: dict, *, user: User, conversation: ChatConversation
    ) -> tuple[str, str, uuid.UUID | None]:
        profile = self.customers.get_customer(user)
        return "customer_profile", _format_customer(profile), None

    def _tool_create_ticket(
        self, args: dict, *, user: User, conversation: ChatConversation
    ) -> tuple[str, str, uuid.UUID | None]:
        subject = self._require_str(args, "subject", "subject")
        description = str(args.get("description") or "").strip()
        if not description:
            description = (
                f"AI-handled conversation about {conversation.subject or 'a support issue'}."
            )
        ticket = self.tickets.create_ticket(
            user,
            TicketCreate(
                subject=subject[:SUBJECT_MAX_LENGTH],
                description=description[:DESCRIPTION_MAX_LENGTH],
                conversation_id=conversation.id,
            ),
        )
        return (
            "create_ticket",
            f"Support ticket opened: {ticket.subject} (id {ticket.id}).",
            ticket.id,
        )

    # ------------------------------------------------------------------ #
    # Handoff tickets
    # ------------------------------------------------------------------ #
    def _create_handoff_ticket(
        self,
        *,
        user: User,
        conversation: ChatConversation,
        classification: IntentClassification,
        customer_message: ChatMessage,
    ) -> uuid.UUID | None:
        """Open a support ticket for an AI-triggered handoff, linked to the conversation.

        Failure to open the ticket must not fail the turn — the handoff still
        happens via conversation escalation.
        """
        try:
            subject = f"AI handoff — {classification.intent.value.replace('_', ' ')}"
            if conversation.subject:
                subject = f"{subject}: {conversation.subject}"
            description = (
                f"The AI assistant escalated this conversation to a human agent "
                f"(sentiment: {classification.sentiment.value}).\n\n"
                f"Latest customer message:\n{customer_message.content}"
            )
            ticket = self.tickets.create_ticket(
                user,
                TicketCreate(
                    subject=subject[:SUBJECT_MAX_LENGTH],
                    description=description[:DESCRIPTION_MAX_LENGTH],
                    conversation_id=conversation.id,
                ),
            )
            return ticket.id
        except Exception:
            logger.exception("Failed to create AI handoff ticket")
            return None

    # ------------------------------------------------------------------ #
    # Fallback & helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _fallback_reply() -> ChatReply:
        """A safe, generic reply used when Groq cannot produce a response.

        Deliberately never escalates: a handoff triggered by a system failure
        would be spurious. The customer can still request a human explicitly
        through the conversation handoff endpoint.
        """
        return ChatReply(
            message=FALLBACK_MESSAGE,
            intent=CustomerIntent.UNKNOWN,
            sentiment=CustomerSentiment.NEUTRAL,
            confidence=0.0,
            requires_human=False,
            ticket_id=None,
            tool_used=None,
        )

    @staticmethod
    def _require_str(args: dict, *keys: str) -> str:
        for key in keys:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise ValueError(f"Missing required argument: {keys[0]}")

    @staticmethod
    def _require_optional_str(args: dict, key: str) -> str | None:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _require_uuid(args: dict, key: str) -> uuid.UUID:
        value = args.get(key)
        if value is None:
            raise ValueError(f"Missing required argument: {key}")
        return uuid.UUID(str(value))


# --------------------------------------------------------------------------- #
# Tool result formatters (ground the model in real data; no raw DB exposure)
# --------------------------------------------------------------------------- #
def _format_product(product) -> str:
    return (
        f"{product.name} — {product.price} {product.currency} · {product.category} · "
        f"in stock: {'yes' if product.stock_quantity > 0 else 'no'} (sku {product.sku})"
    )


def _format_article(article) -> str:
    excerpt = " ".join(article.content.split())[:400]
    return f"[{article.category}] {article.title}: {excerpt}"


def _format_order(order) -> str:
    lines = [
        f"Order {order.order_number}: status {order.status.value}, "
        f"total {order.total_amount} {order.currency}, placed {order.created_at:%Y-%m-%d}"
    ]
    for item in order.items[:5]:
        lines.append(
            f"- {item.quantity}x {item.name} ({item.unit_price} {order.currency} each)"
        )
    return "\n".join(lines)


def _format_tracking(tracking) -> str:
    lines = [f"Order {tracking.order_number}: status {tracking.status.value}"]
    if tracking.carrier or tracking.tracking_number:
        lines.append(
            f"Carrier: {tracking.carrier or 'n/a'}, "
            f"tracking number: {tracking.tracking_number or 'n/a'}"
        )
    if tracking.estimated_delivery:
        lines.append(f"Estimated delivery: {tracking.estimated_delivery}")
    for event in tracking.events:
        lines.append(f"- {event.status}: {event.description}")
    return "\n".join(lines)


def _format_customer(profile) -> str:
    return (
        f"Name: {profile.full_name or 'n/a'} · email: {profile.email} · "
        f"phone: {profile.phone or 'n/a'}"
    )


class GroqChatReplyProvider:
    """``ChatReplyProvider`` backed by the Groq-powered AI Service.

    The Chat module talks to this provider — never to Groq directly.
    """

    def __init__(self, *, groq_service: GroqService | None = None) -> None:
        self.groq_service = groq_service

    def generate_reply(
        self,
        db: Session,
        *,
        user: User,
        conversation: ChatConversation,
        customer_message: ChatMessage,
        history: list[ChatMessage],
    ) -> ChatReply:
        return AIService(db, groq_service=self.groq_service).handle_turn(
            user=user,
            conversation=conversation,
            customer_message=customer_message,
            history=history,
        )


def build_groq_chat_reply_provider() -> GroqChatReplyProvider:
    """Return the Groq-backed provider (selected via ``CHAT_REPLY_BACKEND=groq``)."""
    return GroqChatReplyProvider()
