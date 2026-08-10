"""Knowledge base schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class KnowledgeArticleResponse(BaseModel):
    """Public representation of a knowledge base article."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    slug: str
    content: str
    category: str
    tags: list[str] | None = None
    view_count: int
    created_at: datetime
    updated_at: datetime
