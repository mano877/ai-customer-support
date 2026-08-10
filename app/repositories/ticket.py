"""Ticket and ticket comment repositories."""

import uuid

from sqlalchemy import func, select

from app.models.ticket import Ticket, TicketComment, TicketPriority, TicketStatus
from app.repositories.base import BaseRepository


def _filter_tickets(
    stmt,
    *,
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    category: str | None = None,
):
    """Apply the shared status/priority/category filters to a ticket query."""
    if status is not None:
        stmt = stmt.where(Ticket.status == status.value)
    if priority is not None:
        stmt = stmt.where(Ticket.priority == priority.value)
    if category is not None:
        stmt = stmt.where(Ticket.category == category)
    return stmt


class TicketRepository(BaseRepository[Ticket]):
    """Data access for support tickets.

    Listing is either scoped to the owning customer (``list_for_user``) or the
    full queue (``list_all``, used by support agents and admins), always
    newest-first with optional status/priority/category filters.
    """

    model = Ticket

    def get_for_user(self, ticket_id: uuid.UUID, user_id: uuid.UUID) -> Ticket | None:
        """Fetch a ticket by id, only if it belongs to ``user_id``."""
        stmt = select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.user_id == user_id,
        )
        return self.session.scalars(stmt).first()

    def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
        status: TicketStatus | None = None,
        priority: TicketPriority | None = None,
        category: str | None = None,
    ) -> list[Ticket]:
        """Return a page of the customer's tickets, newest first."""
        stmt = _filter_tickets(
            select(Ticket).where(Ticket.user_id == user_id),
            status=status,
            priority=priority,
            category=category,
        )
        stmt = (
            stmt.order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def count_for_user(
        self,
        user_id: uuid.UUID,
        *,
        status: TicketStatus | None = None,
        priority: TicketPriority | None = None,
        category: str | None = None,
    ) -> int:
        """Count the customer's tickets for pagination metadata."""
        stmt = _filter_tickets(
            select(func.count(Ticket.id)).where(Ticket.user_id == user_id),
            status=status,
            priority=priority,
            category=category,
        )
        return self.session.scalars(stmt).one()

    def list_all(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: TicketStatus | None = None,
        priority: TicketPriority | None = None,
        category: str | None = None,
    ) -> list[Ticket]:
        """Return a page of all tickets (the agent/admin queue), newest first."""
        stmt = _filter_tickets(
            select(Ticket),
            status=status,
            priority=priority,
            category=category,
        )
        stmt = (
            stmt.order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def count_all(
        self,
        *,
        status: TicketStatus | None = None,
        priority: TicketPriority | None = None,
        category: str | None = None,
    ) -> int:
        """Count all tickets for pagination metadata."""
        stmt = _filter_tickets(
            select(func.count(Ticket.id)),
            status=status,
            priority=priority,
            category=category,
        )
        return self.session.scalars(stmt).one()


class TicketCommentRepository(BaseRepository[TicketComment]):
    """Data access for ticket comments (append-only thread)."""

    model = TicketComment

    def list_for_ticket(self, ticket_id: uuid.UUID) -> list[TicketComment]:
        """Return the full comment thread of a ticket in chronological order."""
        stmt = (
            select(TicketComment)
            .where(TicketComment.ticket_id == ticket_id)
            .order_by(TicketComment.created_at.asc(), TicketComment.id.asc())
        )
        return list(self.session.scalars(stmt).all())
