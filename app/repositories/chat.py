"""Chat conversation and message repositories."""

import uuid

from sqlalchemy import func, select

from app.models.chat import ChatConversation, ChatMessage, ChatStatus
from app.repositories.base import BaseRepository


class ChatConversationRepository(BaseRepository[ChatConversation]):
    """Data access for chat conversations, scoped by owner or lifecycle status."""

    model = ChatConversation

    def get_for_user(
        self,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> ChatConversation | None:
        """Fetch a conversation by id, only if it belongs to ``user_id``.

        ``for_update`` takes a row lock so writers to the same conversation
        serialize — this keeps the per-conversation message ``position``
        assignment race-free (no-op on SQLite, which lacks row locks).
        """
        stmt = select(ChatConversation).where(
            ChatConversation.id == conversation_id,
            ChatConversation.user_id == user_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.session.scalars(stmt).first()

    def get_for_update(self, conversation_id: uuid.UUID) -> ChatConversation | None:
        """Fetch any conversation by id with a row lock (agent write paths)."""
        stmt = (
            select(ChatConversation)
            .where(ChatConversation.id == conversation_id)
            .with_for_update()
        )
        return self.session.scalars(stmt).first()

    def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ChatConversation]:
        """Return a page of the user's conversations, newest first."""
        stmt = (
            select(ChatConversation)
            .where(ChatConversation.user_id == user_id)
            .order_by(ChatConversation.created_at.desc(), ChatConversation.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def count_for_user(self, user_id: uuid.UUID) -> int:
        """Count the user's conversations for pagination metadata."""
        stmt = select(func.count(ChatConversation.id)).where(
            ChatConversation.user_id == user_id
        )
        return self.session.scalars(stmt).one()

    def list_by_status(
        self,
        status: ChatStatus,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ChatConversation]:
        """Return a page of conversations in the given status, oldest first.

        Oldest-first gives support agents the longest-waiting escalations first.
        """
        stmt = (
            select(ChatConversation)
            .where(ChatConversation.status == status.value)
            .order_by(ChatConversation.created_at.asc(), ChatConversation.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def count_by_status(self, status: ChatStatus) -> int:
        """Count conversations in the given status for pagination metadata."""
        stmt = select(func.count(ChatConversation.id)).where(
            ChatConversation.status == status.value
        )
        return self.session.scalars(stmt).one()


class ChatMessageRepository(BaseRepository[ChatMessage]):
    """Data access for chat messages (append-only history)."""

    model = ChatMessage

    def next_position(self, conversation_id: uuid.UUID) -> int:
        """Return the next monotonic position for a conversation (1-based).

        Messages are appended sequentially within a conversation (the bot reply
        is generated in the same request), so MAX+1 is safe; the unique
        constraint on (conversation_id, position) guards against duplicates.
        """
        stmt = select(func.max(ChatMessage.position)).where(
            ChatMessage.conversation_id == conversation_id
        )
        max_position = self.session.scalars(stmt).one()
        return (max_position or 0) + 1

    def list_for_conversation(self, conversation_id: uuid.UUID) -> list[ChatMessage]:
        """Return the full message history of a conversation in insertion order."""
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.position.asc())
        )
        return list(self.session.scalars(stmt).all())
