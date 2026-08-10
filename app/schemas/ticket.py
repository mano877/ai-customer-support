"""Support ticket-related schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.ticket import TicketPriority, TicketStatus

SUBJECT_MAX_LENGTH = 255
DESCRIPTION_MAX_LENGTH = 4000
RESOLUTION_NOTES_MAX_LENGTH = 4000
CATEGORY_MAX_LENGTH = 64
COMMENT_MAX_LENGTH = 2000


class TicketCreate(BaseModel):
    """Payload for POST /tickets.

    ``conversation_id`` optionally links the ticket to the chat conversation
    that caused it (the Chat → Ticket handoff); it must belong to the caller.
    Tickets always start with status ``open``.
    """

    subject: str = Field(min_length=1, max_length=SUBJECT_MAX_LENGTH)
    description: str = Field(min_length=1, max_length=DESCRIPTION_MAX_LENGTH)
    category: str | None = Field(default=None, min_length=1, max_length=CATEGORY_MAX_LENGTH)
    priority: TicketPriority = TicketPriority.MEDIUM
    conversation_id: uuid.UUID | None = None

    @field_validator("subject", "description", "category")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Field must not be blank")
        return value


class TicketUpdate(BaseModel):
    """Payload for PATCH /tickets (all fields optional, at least one required).

    Which fields a caller may change depends on their role: customers can only
    edit ``subject``/``description``/``category`` of their own tickets; support
    agents can update any field except reassignment on tickets they handle;
    admins can change everything, including ``assigned_agent_id``.
    """

    subject: str | None = Field(default=None, min_length=1, max_length=SUBJECT_MAX_LENGTH)
    description: str | None = Field(default=None, min_length=1, max_length=DESCRIPTION_MAX_LENGTH)
    category: str | None = Field(default=None, min_length=1, max_length=CATEGORY_MAX_LENGTH)
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    resolution_notes: str | None = Field(default=None, max_length=RESOLUTION_NOTES_MAX_LENGTH)
    assigned_agent_id: uuid.UUID | None = None

    @field_validator("subject", "description", "category")
    @classmethod
    def _not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Field must not be blank")
        return value

    @field_validator("resolution_notes")
    @classmethod
    def _notes_not_blank(cls, value: str | None) -> str | None:
        """Strip notes; a blank string is treated as no notes (or clears them)."""
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _validate_null_and_required(self) -> "TicketUpdate":
        """Reject empty payloads and explicit nulls for NOT NULL columns.

        ``subject``, ``description``, ``status``, and ``priority`` are NOT NULL
        in the database, so an explicit null here would be a 500; treat it as a
        validation error instead. Nullable fields (``category``,
        ``resolution_notes``, ``assigned_agent_id``) may still be cleared with
        an explicit null.
        """
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        for field in ("subject", "description", "status", "priority"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be set to null")
        return self


class TicketCommentCreate(BaseModel):
    """Payload for POST /tickets/{id}/comments."""

    content: str = Field(min_length=1, max_length=COMMENT_MAX_LENGTH)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be blank")
        return value


class TicketResponse(BaseModel):
    """Public representation of a support ticket."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    assigned_agent_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    subject: str
    description: str
    status: TicketStatus
    priority: TicketPriority
    category: str | None = None
    resolution_notes: str | None = None
    created_at: datetime
    updated_at: datetime


class TicketCommentResponse(BaseModel):
    """A single ticket comment."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_id: uuid.UUID
    author_id: uuid.UUID | None = None
    content: str
    created_at: datetime


class TicketDetailResponse(TicketResponse):
    """A ticket together with its full comment thread."""

    comments: list[TicketCommentResponse]


class TicketListResponse(BaseModel):
    """Paginated ticket listing."""

    items: list[TicketResponse]
    total: int
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
