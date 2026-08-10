"""Knowledge base service.

Search is delegated to a ``KnowledgeSearchProvider`` so the current SQL
backend can later be swapped for a vector search backend (Pinecone / ChromaDB)
without touching the service or the API layer.
"""

import uuid
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models.knowledge import KnowledgeArticle
from app.repositories.knowledge import KnowledgeArticleRepository
from app.schemas.knowledge import KnowledgeArticleResponse


class KnowledgeSearchProvider(Protocol):
    """Search backend contract for knowledge articles."""

    def search(
        self,
        db: Session,
        query: str,
        *,
        category: str | None,
        limit: int,
    ) -> list[KnowledgeArticle]: ...


class DatabaseKnowledgeSearch:
    """SQL LIKE-based search (current backend)."""

    def search(
        self,
        db: Session,
        query: str,
        *,
        category: str | None,
        limit: int,
    ) -> list[KnowledgeArticle]:
        return KnowledgeArticleRepository(db).search(
            query, category=category, limit=limit
        )


def build_knowledge_search_provider(backend: str) -> KnowledgeSearchProvider:
    """Return the configured search backend.

    ``KNOWLEDGE_SEARCH_BACKEND`` currently supports ``database``. Future
    values (``pinecone``, ``chromadb``) will construct the vector provider here.
    """
    if backend == "database":
        return DatabaseKnowledgeSearch()
    raise ValueError(f"Unsupported knowledge search backend: {backend!r}")


class KnowledgeService:
    """Business logic for the knowledge base."""

    def __init__(
        self,
        db: Session,
        provider: KnowledgeSearchProvider | None = None,
    ) -> None:
        self.db = db
        self.provider = provider or build_knowledge_search_provider(
            get_settings().KNOWLEDGE_SEARCH_BACKEND
        )
        self.articles = KnowledgeArticleRepository(db)

    def search_articles(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int = 20,
    ) -> list[KnowledgeArticleResponse]:
        results = self.provider.search(self.db, query, category=category, limit=limit)
        return [KnowledgeArticleResponse.model_validate(article) for article in results]

    def get_article(self, article_id: uuid.UUID) -> KnowledgeArticleResponse:
        """Fetch a published article and record the view atomically."""
        article = self.articles.get_published(article_id)
        if article is None:
            raise NotFoundError("Article not found")
        self.articles.increment_view_count(article.id)
        self.db.commit()
        self.db.refresh(article)
        return KnowledgeArticleResponse.model_validate(article)
