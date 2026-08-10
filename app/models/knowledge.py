"""Knowledge base article model."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDPrimaryKeyMixin
from app.utils.slugify import slugify


class KnowledgeArticle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A knowledge base article (e.g. an FAQ entry).

    The slug is derived from the title at construction time; the authoring
    flow must call ``KnowledgeArticleRepository.unique_slug`` to avoid
    collisions on the unique slug index.
    """

    __tablename__ = "knowledge_articles"

    title: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(JSON)
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("1"), nullable=False
    )
    view_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )

    @validates("title")
    def _auto_slug(self, _key: str, value: str) -> str:
        """Derive the slug from the title unless one was provided explicitly."""
        if not self.slug:
            self.slug = slugify(value)
        return value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<KnowledgeArticle id={self.id} slug={self.slug!r}>"
