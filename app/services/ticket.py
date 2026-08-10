"""Support ticket service: creation, role-scoped updates, comments, and the
Chat → Ticket handoff hook.

Authorization model:

- Customers create tickets, view/comment on their own tickets only (foreign
  tickets are indistinguishable from missing ones: 404), and may update only
  the content fields (``subject``, ``description``, ``category``) of their own
  tickets.
- Support agents see the full ticket queue (list/detail) so they can pick up
  open work, but may only *write* to tickets that are unassigned or assigned
  to them. Touching an unassigned ticket claims it (assigns it to the agent)
  atomically — this is the queue → claim → manage flow.
- Admins may read and manage every ticket, including reassigning agents.
"""

import uuid

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.ticket import Ticket, TicketComment
from app.models.user import User, UserRole
from app.repositories.chat import ChatConversationRepository
from app.repositories.ticket import TicketCommentRepository, TicketRepository
from app.repositories.user import UserRepository
from app.schemas.ticket import (
    TicketCommentCreate,
    TicketCommentResponse,
    TicketCreate,
    TicketDetailResponse,
    TicketListResponse,
    TicketResponse,
    TicketUpdate,
)

# Fields a customer is allowed to edit on their own ticket.
_CUSTOMER_EDITABLE_FIELDS = frozenset({"subject", "description", "category"})


class TicketService:
    """Business logic for the support tickets module."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.tickets = TicketRepository(db)
        self.comments = TicketCommentRepository(db)
        self.conversations = ChatConversationRepository(db)
        self.users = UserRepository(db)

    # ------------------------------------------------------------------ #
    # Creation
    # ------------------------------------------------------------------ #
    def create_ticket(self, user: User, payload: TicketCreate) -> TicketResponse:
        """Open a new ticket for the authenticated user.

        When ``conversation_id`` is provided (the Chat → Ticket handoff), the
        conversation must belong to ``user`` so a customer cannot attach
        another user's chat history to their ticket.
        """
        if payload.conversation_id is not None:
            conversation = self.conversations.get_for_user(
                payload.conversation_id, user.id
            )
            if conversation is None:
                raise NotFoundError("Conversation not found")

        ticket = Ticket(
            user_id=user.id,
            subject=payload.subject,
            description=payload.description,
            category=payload.category,
            priority=payload.priority,
            conversation_id=payload.conversation_id,
        )
        self.tickets.add(ticket)
        self.db.commit()
        self.db.refresh(ticket)
        return TicketResponse.model_validate(ticket)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def list_tickets(
        self,
        user: User,
        *,
        status=None,
        priority=None,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> TicketListResponse:
        """Return a paginated ticket list with optional status/priority/category
        filters.

        Customers see only their own tickets; support agents and admins see the
        full queue.
        """
        if user.role in (UserRole.ADMIN, UserRole.SUPPORT_AGENT):
            items = self.tickets.list_all(
                status=status,
                priority=priority,
                category=category,
                limit=limit,
                offset=offset,
            )
            total = self.tickets.count_all(
                status=status, priority=priority, category=category
            )
        else:
            items = self.tickets.list_for_user(
                user.id,
                status=status,
                priority=priority,
                category=category,
                limit=limit,
                offset=offset,
            )
            total = self.tickets.count_for_user(
                user.id, status=status, priority=priority, category=category
            )
        return TicketListResponse(
            items=[TicketResponse.model_validate(item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_ticket(self, user: User, ticket_id: uuid.UUID) -> TicketDetailResponse:
        """Fetch one ticket with its comment thread (role-scoped read)."""
        ticket = self._get_ticket_for_read(user, ticket_id)
        return self._to_detail_response(ticket)

    # ------------------------------------------------------------------ #
    # Updates
    # ------------------------------------------------------------------ #
    def update_ticket(
        self, user: User, ticket_id: uuid.UUID, payload: TicketUpdate
    ) -> TicketResponse:
        """Update a ticket subject to the caller's role permissions.

        - Customer: content fields only, own ticket.
        - Support agent: any field except ``assigned_agent_id`` on an unassigned
          or self-assigned ticket; updating an unassigned ticket claims it.
        - Admin: every field, any ticket (reassignment included).
        """
        ticket = self._get_ticket_for_read(user, ticket_id)
        updates = payload.model_dump(exclude_unset=True)

        if user.role == UserRole.ADMIN:
            if updates.get("assigned_agent_id") is not None:
                self._validate_assigned_agent(updates["assigned_agent_id"])
            values = self._coerce_enum_values(updates)
            result = self.db.execute(
                update(Ticket).where(Ticket.id == ticket.id).values(**values)
            )
        elif user.role == UserRole.SUPPORT_AGENT:
            if "assigned_agent_id" in updates:
                raise ForbiddenError("Only admins can reassign tickets")
            values = self._coerce_enum_values(updates)
            if ticket.assigned_agent_id is None:
                values["assigned_agent_id"] = user.id  # claim on first touch
            # Guarded: only an unassigned or self-assigned ticket can be
            # updated, so two agents cannot both claim (or edit a colleague's
            # ticket) even under concurrency.
            result = self.db.execute(
                update(Ticket)
                .where(
                    Ticket.id == ticket.id,
                    or_(
                        Ticket.assigned_agent_id.is_(None),
                        Ticket.assigned_agent_id == user.id,
                    ),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                self.db.rollback()
                raise ForbiddenError("Ticket is assigned to another agent")
        else:  # customer
            disallowed = set(updates) - _CUSTOMER_EDITABLE_FIELDS
            if disallowed:
                raise ForbiddenError(
                    "Customers may only update the subject, description, and "
                    "category of their tickets"
                )
            values = self._coerce_enum_values(updates)
            result = self.db.execute(
                update(Ticket)
                .where(Ticket.id == ticket.id, Ticket.user_id == user.id)
                .values(**values)
            )

        if result.rowcount != 1:
            self.db.rollback()
            raise NotFoundError("Ticket not found")
        self.db.commit()
        self.db.refresh(ticket)
        return TicketResponse.model_validate(ticket)

    # ------------------------------------------------------------------ #
    # Comments
    # ------------------------------------------------------------------ #
    def add_comment(
        self, user: User, ticket_id: uuid.UUID, payload: TicketCommentCreate
    ) -> TicketCommentResponse:
        """Append a comment to a ticket's thread.

        Customers comment on their own tickets; agents on tickets they handle
        (an agent's first comment on an unassigned ticket claims it); admins on
        any ticket.
        """
        ticket = self._get_ticket_for_read(user, ticket_id)
        if user.role == UserRole.SUPPORT_AGENT:
            if ticket.assigned_agent_id is not None and ticket.assigned_agent_id != user.id:
                raise ForbiddenError("Ticket is assigned to another agent")
            if ticket.assigned_agent_id is None:
                result = self.db.execute(
                    update(Ticket)
                    .where(Ticket.id == ticket.id, Ticket.assigned_agent_id.is_(None))
                    .values(assigned_agent_id=user.id)
                )
                if result.rowcount != 1:
                    self.db.rollback()
                    raise ForbiddenError("Ticket is assigned to another agent")
                ticket.assigned_agent_id = user.id  # keep the in-memory value in sync

        comment = TicketComment(
            ticket_id=ticket.id,
            author_id=user.id,
            content=payload.content,
        )
        self.comments.add(comment)
        self.db.commit()
        self.db.refresh(comment)
        return TicketCommentResponse.model_validate(comment)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _get_ticket_for_read(self, user: User, ticket_id: uuid.UUID) -> Ticket:
        """Fetch a ticket under the caller's read scope or raise 404.

        Customers are scoped to their own tickets (foreign → 404); agents and
        admins may read any ticket.
        """
        if user.role in (UserRole.ADMIN, UserRole.SUPPORT_AGENT):
            ticket = self.tickets.get(ticket_id)
        else:
            ticket = self.tickets.get_for_user(ticket_id, user.id)
        if ticket is None:
            raise NotFoundError("Ticket not found")
        return ticket

    def _validate_assigned_agent(self, agent_id: uuid.UUID) -> None:
        """Ensure an assignment target exists and is an active support agent."""
        agent = self.users.get(agent_id)
        if agent is None:
            raise NotFoundError("Assigned agent not found")
        if agent.role != UserRole.SUPPORT_AGENT:
            raise BadRequestError("Assigned agent must be a support agent")
        if not agent.is_active:
            raise BadRequestError("Assigned agent is not active")

    @staticmethod
    def _coerce_enum_values(updates: dict) -> dict:
        """Convert enum-typed update values to their string values for SQL."""
        values = dict(updates)
        if values.get("status") is not None:
            values["status"] = values["status"].value
        if values.get("priority") is not None:
            values["priority"] = values["priority"].value
        return values

    def _to_detail_response(self, ticket: Ticket) -> TicketDetailResponse:
        """Build the detail view: ticket fields plus the comment thread."""
        comments = self.comments.list_for_ticket(ticket.id)
        base = TicketResponse.model_validate(ticket)
        return TicketDetailResponse(
            **base.model_dump(),
            comments=[
                TicketCommentResponse.model_validate(comment) for comment in comments
            ],
        )
