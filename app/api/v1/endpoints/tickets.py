"""Support ticket endpoints: create, list, detail, update, and comments.

Routers stay thin: they parse/validate input and delegate to the service layer.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies.auth import CurrentUser
from app.models.ticket import TicketPriority, TicketStatus
from app.schemas.ticket import (
    TicketCommentCreate,
    TicketCommentResponse,
    TicketCreate,
    TicketDetailResponse,
    TicketListResponse,
    TicketResponse,
    TicketUpdate,
)
from app.services.ticket import TicketService

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.post("", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(
    payload: TicketCreate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> TicketResponse:
    """Open a new support ticket for the authenticated user."""
    return TicketService(db).create_ticket(user, payload)


@router.get("", response_model=TicketListResponse)
def list_tickets(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    status_: TicketStatus | None = Query(default=None, alias="status"),
    priority: TicketPriority | None = Query(default=None),
    category: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> TicketListResponse:
    """List tickets, newest first.

    Customers see only their own tickets; support agents and admins see the
    full queue. Supports status/priority/category filters and pagination.
    """
    return TicketService(db).list_tickets(
        user,
        status=status_,
        priority=priority,
        category=category,
        limit=limit,
        offset=offset,
    )


@router.get("/{ticket_id}", response_model=TicketDetailResponse)
def get_ticket(
    ticket_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> TicketDetailResponse:
    """Fetch one ticket with its full comment thread (role-scoped)."""
    return TicketService(db).get_ticket(user, ticket_id)


@router.patch("/{ticket_id}", response_model=TicketResponse)
def update_ticket(
    ticket_id: uuid.UUID,
    payload: TicketUpdate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> TicketResponse:
    """Update a ticket — which fields are allowed depends on the caller's role.

    Customers edit content fields of their own tickets; support agents manage
    tickets they handle (updating an unassigned ticket claims it); admins can
    change anything, including reassignment.
    """
    return TicketService(db).update_ticket(user, ticket_id, payload)


@router.post(
    "/{ticket_id}/comments",
    response_model=TicketCommentResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_comment(
    ticket_id: uuid.UUID,
    payload: TicketCommentCreate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> TicketCommentResponse:
    """Append a comment to a ticket's thread (customers: own tickets only)."""
    return TicketService(db).add_comment(user, ticket_id, payload)
