"""Generic base repository with common CRUD operations."""

from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import Base


class BaseRepository[ModelT: Base]:
    """Thin data-access layer over a single SQLAlchemy model."""

    model: ClassVar[type[ModelT]]

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, id: Any) -> ModelT | None:
        """Fetch a single row by primary key."""
        return self.session.get(self.model, id)

    def list(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        """Fetch a page of rows."""
        stmt = select(self.model).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def add(self, obj: ModelT) -> ModelT:
        """Queue an object for insertion (flushed to DB on next flush/commit)."""
        self.session.add(obj)
        return obj

    def delete(self, obj: ModelT) -> None:
        """Queue an object for deletion (flushed to DB on next flush/commit)."""
        self.session.delete(obj)
