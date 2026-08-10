"""Knowledge article repository."""

import uuid

from sqlalchemy import String, case, cast, func, or_, select, update

from app.models.knowledge import KnowledgeArticle
from app.repositories.base import BaseRepository
from app.utils.like import escape_like
from app.utils.slugify import slugify


class KnowledgeArticleRepository(BaseRepository[KnowledgeArticle]):
    """Data access for published knowledge base articles."""

    model = KnowledgeArticle

    def get_published(self, article_id: uuid.UUID) -> KnowledgeArticle | None:
        stmt = select(KnowledgeArticle).where(
            KnowledgeArticle.id == article_id,
            KnowledgeArticle.is_published.is_(True),
        )
        return self.session.scalars(stmt).first()

    def increment_view_count(self, article_id: uuid.UUID) -> None:
        """Atomically bump an article's view counter (no lost updates)."""
        self.session.execute(
            update(KnowledgeArticle)
            .where(KnowledgeArticle.id == article_id)
            .values(view_count=KnowledgeArticle.view_count + 1)
        )

    def unique_slug(self, title: str) -> str:
        """Return a slug for ``title`` that is unique among existing articles.

        Intended for the future authoring flow; appends ``-2``, ``-3``, ... on
        collision with an existing slug.
        """
        base = slugify(title)
        candidate = base
        counter = 2
        while self._slug_exists(candidate):
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    def _slug_exists(self, slug: str) -> bool:
        stmt = select(KnowledgeArticle.id).where(KnowledgeArticle.slug == slug)
        return self.session.scalars(stmt).first() is not None

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int = 20,
    ) -> list[KnowledgeArticle]:
        """Relevance-ranked search over title, tags, and content.

        Ranking weights: title match (3) > tag match (2) > content match (1).
        Tags are matched via their JSON text representation for cross-dialect
        portability (SQLite and PostgreSQL).
        """
        pattern = f"%{escape_like(query.strip())}%"
        title_match = KnowledgeArticle.title.ilike(pattern, escape="\\")
        tags_match = cast(KnowledgeArticle.tags, String).ilike(pattern, escape="\\")
        content_match = KnowledgeArticle.content.ilike(pattern, escape="\\")

        score = case(
            (title_match, 3),
            (tags_match, 2),
            (content_match, 1),
            else_=0,
        )

        where = [
            KnowledgeArticle.is_published.is_(True),
            or_(title_match, tags_match, content_match),
        ]
        if category:
            where.append(func.lower(KnowledgeArticle.category) == category.strip().lower())

        stmt = (
            select(KnowledgeArticle)
            .where(*where)
            .order_by(score.desc(), KnowledgeArticle.view_count.desc(), KnowledgeArticle.id.asc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt).all())
