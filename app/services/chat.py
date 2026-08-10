"""Chat service: conversations, messages, handoff, feedback, and the AI reply pipeline.

The bot's reply is delegated to a ``ChatReplyProvider``. Today the stub backend
(``CHAT_REPLY_BACKEND=stub``) serves a deterministic placeholder; the
Groq-backed AI Service (``CHAT_REPLY_BACKEND=groq``) plugs in through the same
protocol — this module never talks to an LLM directly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.ai.types import CustomerIntent, CustomerSentiment
from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.core.security import now_utc
from app.models.chat import (
    ChatConversation,
    ChatMessage,
    ChatMessageSender,
    ChatStatus,
)
from app.models.user import User
from app.repositories.chat import ChatConversationRepository, ChatMessageRepository
from app.schemas.chat import (
    ChatConversationCreate,
    ChatConversationDetailResponse,
    ChatConversationListResponse,
    ChatConversationResponse,
    ChatFeedbackCreate,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatSendResponse,
)


class ChatReplyProvider(Protocol):
    """Contract for generating the AI bot's reply to a customer message.

    ``user`` is the authenticated customer — always the session user, never
    derived from anything the model says. ``history`` is the message history
    including the customer message being answered. Implementations return a
    structured ``ChatReply``; the service applies its side effects (e.g.
    escalating the conversation when ``requires_human`` is set).
    """

    def generate_reply(
        self,
        db: Session,
        *,
        user: User,
        conversation: ChatConversation,
        customer_message: ChatMessage,
        history: list[ChatMessage],
    ) -> ChatReply: ...


@dataclass(frozen=True)
class ChatReply:
    """Structured result of one AI bot turn (the provider contract).

    ``intent`` / ``sentiment`` / ``confidence`` are analytics metadata;
    ``ticket_id`` and ``tool_used`` are set when the turn opened a support
    ticket or executed a tool. ``requires_human`` triggers conversation
    escalation in the service (guarded, so it happens at most once).
    """

    message: str
    intent: CustomerIntent = CustomerIntent.UNKNOWN
    sentiment: CustomerSentiment = CustomerSentiment.NEUTRAL
    confidence: float = 0.0
    requires_human: bool = False
    ticket_id: uuid.UUID | None = None
    tool_used: str | None = None


class StubChatReplyProvider:
    """Placeholder reply generator used until the Groq-backed AI is selected.

    Deterministic and fully offline; swap it out by implementing
    ``ChatReplyProvider`` and setting ``CHAT_REPLY_BACKEND`` accordingly.
    """

    def generate_reply(
        self,
        db: Session,
        *,
        user: User,
        conversation: ChatConversation,
        customer_message: ChatMessage,
        history: list[ChatMessage],
    ) -> ChatReply:
        return ChatReply(
            message=(
                "Thanks for your message! Our AI assistant is currently being "
                "set up, so a member of our support team will follow up with "
                "you shortly."
            ),
            intent=CustomerIntent.GENERAL_QUESTION,
        )


def build_chat_reply_provider(backend: str) -> ChatReplyProvider:
    """Return the configured bot reply backend.

    ``CHAT_REPLY_BACKEND`` supports ``stub`` (offline placeholder) and ``groq``
    (the Groq-powered AI Service). The Groq provider is imported lazily so the
    chat module stays independent of the AI layer at import time.
    """
    if backend == "stub":
        return StubChatReplyProvider()
    if backend == "groq":
        from app.ai.ai_service import build_groq_chat_reply_provider

        return build_groq_chat_reply_provider()
    raise ValueError(f"Unsupported chat reply backend: {backend!r}")


class ChatService:
    """Business logic for the chat module."""

    def __init__(
        self,
        db: Session,
        provider: ChatReplyProvider | None = None,
    ) -> None:
        self.db = db
        self.conversations = ChatConversationRepository(db)
        self.messages = ChatMessageRepository(db)
        self.provider = provider or build_chat_reply_provider(
            get_settings().CHAT_REPLY_BACKEND
        )

    # ------------------------------------------------------------------ #
    # Customer-facing flows
    # ------------------------------------------------------------------ #
    def create_conversation(
        self, user: User, payload: ChatConversationCreate
    ) -> ChatConversationDetailResponse:
        """Start a new active conversation, optionally with a first message."""
        conversation = ChatConversation(user_id=user.id, subject=payload.subject)
        self.conversations.add(conversation)
        if payload.initial_message is not None:
            self.db.flush()  # persist the conversation so messages can reference it
            self._add_message(
                conversation, ChatMessageSender.CUSTOMER, user.id, payload.initial_message
            )
            _bot_message, reply = self._append_bot_reply(conversation, user)
            if reply is not None and reply.requires_human:
                self._escalate_from_ai_reply(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return self._to_detail_response(conversation)

    def list_conversations(
        self, user: User, *, limit: int = 20, offset: int = 0
    ) -> ChatConversationListResponse:
        """Return a paginated list of the user's conversations, newest first."""
        items = self.conversations.list_for_user(user.id, limit=limit, offset=offset)
        total = self.conversations.count_for_user(user.id)
        return ChatConversationListResponse(
            items=[ChatConversationResponse.model_validate(item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_conversation(
        self, user: User, conversation_id: uuid.UUID
    ) -> ChatConversationDetailResponse:
        """Return one of the user's conversations with its full history."""
        conversation = self._get_owned_conversation(user, conversation_id)
        return self._to_detail_response(conversation)

    def send_message(
        self,
        user: User,
        conversation_id: uuid.UUID,
        payload: ChatMessageCreate,
    ) -> ChatSendResponse:
        """Append a customer message; the bot replies while the chat is active.

        The reply is generated through ``self.provider``, keeping the AI
        integration swappable. Once a human handoff is requested (or the
        conversation is resolved) the bot stays silent.
        """
        # Lock the conversation row so concurrent writers to the same chat
        # serialize; this keeps the per-conversation message position monotonic.
        conversation = self._get_owned_conversation(
            user, conversation_id, for_update=True
        )
        if conversation.status == ChatStatus.RESOLVED:
            raise ConflictError(
                "Conversation is resolved; start a new one to continue chatting"
            )

        customer_message = self._add_message(
            conversation, ChatMessageSender.CUSTOMER, user.id, payload.content
        )
        bot_message, reply = self._append_bot_reply(conversation, user)
        if reply is not None and reply.requires_human:
            self._escalate_from_ai_reply(conversation)
        self.db.commit()
        self.db.refresh(customer_message)
        if bot_message is not None:
            self.db.refresh(bot_message)
        return ChatSendResponse(
            customer_message=ChatMessageResponse.model_validate(customer_message),
            bot_message=(
                ChatMessageResponse.model_validate(bot_message)
                if bot_message is not None
                else None
            ),
            requires_human=(reply.requires_human if reply is not None else None),
            ticket_id=(reply.ticket_id if reply is not None else None),
        )

    def escalate(self, user: User, conversation_id: uuid.UUID) -> ChatConversationResponse:
        """Request a human handoff for an active conversation.

        Uses a guarded UPDATE so the escalation is atomic — concurrent requests
        can only succeed once.
        """
        conversation = self._get_owned_conversation(user, conversation_id)
        requested_at = now_utc()
        result = self.db.execute(
            update(ChatConversation)
            .where(
                ChatConversation.id == conversation.id,
                ChatConversation.status == ChatStatus.ACTIVE.value,
            )
            .values(
                status=ChatStatus.ESCALATED.value,
                handoff_requested_at=requested_at,
            )
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConflictError("Conversation is already escalated or resolved")
        # Keep the in-memory state in sync with the guarded UPDATE.
        conversation.status = ChatStatus.ESCALATED
        conversation.handoff_requested_at = requested_at
        self._add_message(
            conversation,
            ChatMessageSender.SYSTEM,
            None,
            "A human agent has been requested for this conversation.",
        )
        self.db.commit()
        self.db.refresh(conversation)
        return ChatConversationResponse.model_validate(conversation)

    def resolve(self, user: User, conversation_id: uuid.UUID) -> ChatConversationResponse:
        """Close a conversation from the customer side (active or escalated)."""
        conversation = self._get_owned_conversation(user, conversation_id)
        resolved_at = now_utc()
        result = self.db.execute(
            update(ChatConversation)
            .where(
                ChatConversation.id == conversation.id,
                ChatConversation.status.in_(
                    [ChatStatus.ACTIVE.value, ChatStatus.ESCALATED.value]
                ),
            )
            .values(status=ChatStatus.RESOLVED.value, resolved_at=resolved_at)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConflictError("Conversation is already resolved")
        conversation.status = ChatStatus.RESOLVED
        conversation.resolved_at = resolved_at
        self._add_message(
            conversation,
            ChatMessageSender.SYSTEM,
            None,
            "Conversation marked as resolved.",
        )
        self.db.commit()
        self.db.refresh(conversation)
        return ChatConversationResponse.model_validate(conversation)

    def submit_feedback(
        self, user: User, conversation_id: uuid.UUID, payload: ChatFeedbackCreate
    ) -> ChatConversationResponse:
        """Capture post-chat feedback on a resolved conversation (once only)."""
        conversation = self._get_owned_conversation(user, conversation_id)
        if conversation.status != ChatStatus.RESOLVED:
            raise BadRequestError(
                "Feedback can only be submitted once the conversation is resolved"
            )

        # Guarded update: "once only" holds even under concurrent submissions.
        result = self.db.execute(
            update(ChatConversation)
            .where(
                ChatConversation.id == conversation.id,
                ChatConversation.rating.is_(None),
            )
            .values(rating=payload.rating, feedback_comment=payload.comment)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConflictError("Feedback has already been submitted for this conversation")
        conversation.rating = payload.rating
        conversation.feedback_comment = payload.comment
        self.db.commit()
        self.db.refresh(conversation)
        return ChatConversationResponse.model_validate(conversation)

    # ------------------------------------------------------------------ #
    # Support-agent flows
    # ------------------------------------------------------------------ #
    def list_agent_conversations(
        self,
        status: ChatStatus,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> ChatConversationListResponse:
        """Return the agent queue for a status (default: escalated), oldest first."""
        items = self.conversations.list_by_status(status, limit=limit, offset=offset)
        total = self.conversations.count_by_status(status)
        return ChatConversationListResponse(
            items=[ChatConversationResponse.model_validate(item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_agent_conversation(self, conversation_id: uuid.UUID) -> ChatConversationDetailResponse:
        """Return any conversation with its history for agent review."""
        conversation = self._get_conversation(conversation_id)
        return self._to_detail_response(conversation)

    def claim_conversation(
        self, agent: User, conversation_id: uuid.UUID
    ) -> ChatConversationResponse:
        """Assign an escalated conversation to the claiming agent (atomic)."""
        conversation = self._get_conversation(conversation_id)
        assigned_at = now_utc()
        result = self.db.execute(
            update(ChatConversation)
            .where(
                ChatConversation.id == conversation.id,
                ChatConversation.status == ChatStatus.ESCALATED.value,
                ChatConversation.assigned_agent_id.is_(None),
            )
            .values(assigned_agent_id=agent.id, assigned_at=assigned_at)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConflictError(
                "Conversation cannot be claimed: it is not escalated or already claimed"
            )
        conversation.assigned_agent_id = agent.id
        conversation.assigned_at = assigned_at
        self._add_message(
            conversation,
            ChatMessageSender.SYSTEM,
            None,
            "A support agent has joined the conversation.",
        )
        self.db.commit()
        self.db.refresh(conversation)
        return ChatConversationResponse.model_validate(conversation)

    def send_agent_message(
        self,
        agent: User,
        conversation_id: uuid.UUID,
        payload: ChatMessageCreate,
    ) -> ChatMessageResponse:
        """Reply as the support agent handling the conversation."""
        # Row-lock the conversation so the agent reply cannot race the customer
        # sending a message to the same chat (see send_message).
        conversation = self.conversations.get_for_update(conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        if conversation.assigned_agent_id != agent.id:
            raise ForbiddenError("Conversation must be claimed by you before replying")
        if conversation.status != ChatStatus.ESCALATED:
            raise ConflictError("Conversation is no longer open for agent replies")

        message = self._add_message(
            conversation, ChatMessageSender.AGENT, agent.id, payload.content
        )
        self.db.commit()
        self.db.refresh(message)
        return ChatMessageResponse.model_validate(message)

    def resolve_for_agent(
        self, agent: User, conversation_id: uuid.UUID
    ) -> ChatConversationResponse:
        """Close a conversation the agent is handling."""
        conversation = self._get_conversation(conversation_id)
        if conversation.assigned_agent_id != agent.id:
            raise ForbiddenError("Conversation must be claimed by you first")
        resolved_at = now_utc()
        result = self.db.execute(
            update(ChatConversation)
            .where(
                ChatConversation.id == conversation.id,
                ChatConversation.status == ChatStatus.ESCALATED.value,
            )
            .values(status=ChatStatus.RESOLVED.value, resolved_at=resolved_at)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConflictError("Conversation is already resolved")
        conversation.status = ChatStatus.RESOLVED
        conversation.resolved_at = resolved_at
        self._add_message(
            conversation,
            ChatMessageSender.SYSTEM,
            None,
            "Conversation resolved by a support agent.",
        )
        self.db.commit()
        self.db.refresh(conversation)
        return ChatConversationResponse.model_validate(conversation)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _escalate_from_ai_reply(self, conversation: ChatConversation) -> None:
        """Escalate an active conversation on the AI's handoff signal (no commit).

        Uses a guarded UPDATE so only one escalation can ever succeed. If a
        concurrent request already escalated, the bot's reply is still stored
        and the escalation is simply skipped.
        """
        requested_at = now_utc()
        result = self.db.execute(
            update(ChatConversation)
            .where(
                ChatConversation.id == conversation.id,
                ChatConversation.status == ChatStatus.ACTIVE.value,
            )
            .values(
                status=ChatStatus.ESCALATED.value,
                handoff_requested_at=requested_at,
            )
        )
        if result.rowcount != 1:
            return
        conversation.status = ChatStatus.ESCALATED
        conversation.handoff_requested_at = requested_at
        # Flush so the pending bot message is visible to ``next_position``
        # (the session runs with autoflush disabled); otherwise the system
        # notice would compute the same position and violate the unique
        # (conversation_id, position) constraint.
        self.db.flush()
        self._add_message(
            conversation,
            ChatMessageSender.SYSTEM,
            None,
            "A human agent has been requested for this conversation.",
        )

    def _get_owned_conversation(
        self,
        user: User,
        conversation_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> ChatConversation:
        """Fetch a conversation owned by the user or raise 404."""
        conversation = self.conversations.get_for_user(
            conversation_id, user.id, for_update=for_update
        )
        if conversation is None:
            raise NotFoundError("Conversation not found")
        return conversation

    def _get_conversation(self, conversation_id: uuid.UUID) -> ChatConversation:
        """Fetch any conversation by id or raise 404 (agent-side reads)."""
        conversation = self.conversations.get(conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        return conversation

    def _add_message(
        self,
        conversation: ChatConversation,
        sender_type: ChatMessageSender,
        sender_user_id: uuid.UUID | None,
        content: str,
    ) -> ChatMessage:
        """Queue a message for the conversation (flushed on the next flush/commit)."""
        message = ChatMessage(
            conversation_id=conversation.id,
            position=self.messages.next_position(conversation.id),
            sender_type=sender_type,
            sender_user_id=sender_user_id,
            content=content,
        )
        self.messages.add(message)
        return message

    def _append_bot_reply(
        self, conversation: ChatConversation, user: User
    ) -> tuple[ChatMessage | None, ChatReply | None]:
        """Generate and store the AI bot's reply while the bot handles the chat.

        Returns ``(bot_message, reply)``; both are None when the conversation
        is not in the active state (escalated/resolved) — in that case the
        human agent answers instead.
        """
        if conversation.status != ChatStatus.ACTIVE:
            return None, None
        # Flush so the history query below includes the customer message that
        # was just queued (the session runs with autoflush disabled).
        self.db.flush()
        history = self.messages.list_for_conversation(conversation.id)
        customer_message = history[-1]
        reply = self.provider.generate_reply(
            self.db,
            user=user,
            conversation=conversation,
            customer_message=customer_message,
            history=history,
        )
        return (
            self._add_message(conversation, ChatMessageSender.BOT, None, reply.message),
            reply,
        )

    def _to_detail_response(
        self, conversation: ChatConversation
    ) -> ChatConversationDetailResponse:
        """Build the detail view: conversation fields plus message history."""
        messages = self.messages.list_for_conversation(conversation.id)
        base = ChatConversationResponse.model_validate(conversation)
        return ChatConversationDetailResponse(
            **base.model_dump(),
            messages=[ChatMessageResponse.model_validate(message) for message in messages],
        )
