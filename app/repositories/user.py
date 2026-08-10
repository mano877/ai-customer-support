"""User repository."""

from sqlalchemy import select

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Data access for users."""

    model = User

    def get_by_email(self, email: str) -> User | None:
        """Fetch a user by (normalized) email address."""
        stmt = select(User).where(User.email == email.strip().lower())
        return self.session.scalars(stmt).first()
