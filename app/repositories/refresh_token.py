"""Refresh token repository."""

from sqlalchemy import select

from app.models.refresh_token import RefreshToken
from app.repositories.base import BaseRepository


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    """Data access for refresh tokens."""

    model = RefreshToken

    def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        """Fetch a refresh token record by its stored digest."""
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        return self.session.scalars(stmt).first()

    def revoke_for_user(self, user_id) -> None:
        """Revoke every active refresh token belonging to a user (mass logout)."""
        from app.core.security import now_utc

        stmt = select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
        now = now_utc()
        for token in self.session.scalars(stmt):
            token.revoked_at = now
