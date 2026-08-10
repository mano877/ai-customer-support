"""Knowledge base endpoints: public FAQ/article search and retrieval."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.knowledge import KnowledgeArticleResponse
from app.services.knowledge import KnowledgeService

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/search", response_model=list[KnowledgeArticleResponse])
def search_articles(
    db: Annotated[Session, Depends(get_db)],
    q: str = Query(min_length=1, max_length=200),
    category: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[KnowledgeArticleResponse]:
    """Search published articles, ranked by relevance (title > tags > content)."""
    return KnowledgeService(db).search_articles(q, category=category, limit=limit)


@router.get("/articles/{article_id}", response_model=KnowledgeArticleResponse)
def get_article(
    article_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> KnowledgeArticleResponse:
    """Fetch a published article by id (increments its view count)."""
    return KnowledgeService(db).get_article(article_id)
