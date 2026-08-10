"""Chat endpoints: conversations, messages, handoff, agent queue, and feedback.

Routers stay thin: they parse/validate input and delegate to the service layer.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies.auth import CurrentUser, require_roles
from app.models.chat import ChatStatus
from app.models.user import User, UserRole
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
from app.services.chat import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])

AgentUser = Annotated[User, Depends(require_roles(UserRole.SUPPORT_AGENT))]


# --------------------------------------------------------------------------- #
# Customer conversations
# --------------------------------------------------------------------------- #
@router.post(
    "/conversations",
    response_model=ChatConversationDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    payload: ChatConversationCreate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ChatConversationDetailResponse:
    """Start a new chat conversation, optionally with a first message."""
    return ChatService(db).create_conversation(user, payload)


@router.get("/conversations", response_model=ChatConversationListResponse)
def list_conversations(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ChatConversationListResponse:
    """List the authenticated user's conversations, newest first."""
    return ChatService(db).list_conversations(user, limit=limit, offset=offset)


@router.get("/conversations/{conversation_id}", response_model=ChatConversationDetailResponse)
def get_conversation(
    conversation_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ChatConversationDetailResponse:
    """Fetch one of the user's conversations with its full message history."""
    return ChatService(db).get_conversation(user, conversation_id)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ChatSendResponse,
)
def send_message(
    conversation_id: uuid.UUID,
    payload: ChatMessageCreate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ChatSendResponse:
    """Send a message; the bot replies automatically while the chat is active."""
    return ChatService(db).send_message(user, conversation_id, payload)


@router.post(
    "/conversations/{conversation_id}/escalate",
    response_model=ChatConversationResponse,
)
def escalate_conversation(
    conversation_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ChatConversationResponse:
    """Request a human handoff for an active conversation."""
    return ChatService(db).escalate(user, conversation_id)


@router.post(
    "/conversations/{conversation_id}/resolve",
    response_model=ChatConversationResponse,
)
def resolve_conversation(
    conversation_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ChatConversationResponse:
    """Close one of the user's conversations."""
    return ChatService(db).resolve(user, conversation_id)


@router.post(
    "/conversations/{conversation_id}/feedback",
    response_model=ChatConversationResponse,
)
def submit_feedback(
    conversation_id: uuid.UUID,
    payload: ChatFeedbackCreate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ChatConversationResponse:
    """Rate a resolved conversation (once) and leave an optional comment."""
    return ChatService(db).submit_feedback(user, conversation_id, payload)


# --------------------------------------------------------------------------- #
# Support agent queue
# --------------------------------------------------------------------------- #
@router.get("/agent/conversations", response_model=ChatConversationListResponse)
def list_agent_conversations(
    agent: AgentUser,
    db: Annotated[Session, Depends(get_db)],
    status_: ChatStatus = Query(default=ChatStatus.ESCALATED, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ChatConversationListResponse:
    """List the agent queue for a status (default: escalated), oldest first."""
    return ChatService(db).list_agent_conversations(
        status_, limit=limit, offset=offset
    )


@router.get(
    "/agent/conversations/{conversation_id}",
    response_model=ChatConversationDetailResponse,
)
def get_agent_conversation(
    conversation_id: uuid.UUID,
    agent: AgentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ChatConversationDetailResponse:
    """Fetch any conversation (with history) for agent review."""
    return ChatService(db).get_agent_conversation(conversation_id)


@router.post(
    "/agent/conversations/{conversation_id}/claim",
    response_model=ChatConversationResponse,
)
def claim_conversation(
    conversation_id: uuid.UUID,
    agent: AgentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ChatConversationResponse:
    """Claim an escalated conversation so only this agent can reply/resolve it."""
    return ChatService(db).claim_conversation(agent, conversation_id)


@router.post(
    "/agent/conversations/{conversation_id}/messages",
    response_model=ChatMessageResponse,
)
def send_agent_message(
    conversation_id: uuid.UUID,
    payload: ChatMessageCreate,
    agent: AgentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ChatMessageResponse:
    """Reply to a claimed escalated conversation as the support agent."""
    return ChatService(db).send_agent_message(agent, conversation_id, payload)


@router.post(
    "/agent/conversations/{conversation_id}/resolve",
    response_model=ChatConversationResponse,
)
def resolve_agent_conversation(
    conversation_id: uuid.UUID,
    agent: AgentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ChatConversationResponse:
    """Resolve a conversation the agent is handling."""
    return ChatService(db).resolve_for_agent(agent, conversation_id)
