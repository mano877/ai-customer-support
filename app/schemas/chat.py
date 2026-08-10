"""Chat-related schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.chat import ChatMessageSender, ChatStatus

MESSAGE_MAX_LENGTH = 4000
SUBJECT_MAX_LENGTH = 120


class ChatConversationCreate(BaseModel):
    """Payload for POST /chat/conversations.

    ``initial_message`` is optional; when provided the bot's reply is generated
    immediately so the conversation starts with a customer + bot exchange.
    """

    subject: str | None = Field(default=None, min_length=1, max_length=SUBJECT_MAX_LENGTH)
    initial_message: str | None = Field(default=None, min_length=1, max_length=MESSAGE_MAX_LENGTH)

    @field_validator("subject", "initial_message")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Field must not be blank")
        return value


class ChatMessageCreate(BaseModel):
    """Payload for sending a chat message (customer or support agent)."""

    content: str = Field(min_length=1, max_length=MESSAGE_MAX_LENGTH)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be blank")
        return value


class ChatFeedbackCreate(BaseModel):
    """Payload for POST /chat/conversations/{id}/feedback."""

    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)


class ChatMessageResponse(BaseModel):
    """A single chat message."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    sender_type: ChatMessageSender
    sender_user_id: uuid.UUID | None = None
    content: str
    created_at: datetime


class ChatConversationResponse(BaseModel):
    """Public representation of a chat conversation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    subject: str | None = None
    status: ChatStatus
    assigned_agent_id: uuid.UUID | None = None
    assigned_at: datetime | None = None
    handoff_requested_at: datetime | None = None
    resolved_at: datetime | None = None
    rating: int | None = None
    feedback_comment: str | None = None
    created_at: datetime
    updated_at: datetime


class ChatConversationDetailResponse(ChatConversationResponse):
    """A conversation together with its full message history."""

    messages: list[ChatMessageResponse]


class ChatConversationListResponse(BaseModel):
    """Paginated conversation listing."""

    items: list[ChatConversationResponse]
    total: int
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class ChatSendResponse(BaseModel):
    """Result of sending a customer message.

    ``bot_message`` is null when a human agent is handling the conversation
    (escalated) or the chat is already resolved. ``requires_human`` is true
    when the AI triggered a handoff (the conversation is escalated) and
    ``ticket_id`` is set when a support ticket was opened for the turn.
    """

    customer_message: ChatMessageResponse
    bot_message: ChatMessageResponse | None = None
    requires_human: bool | None = None
    ticket_id: uuid.UUID | None = None
