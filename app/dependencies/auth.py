"""FastAPI dependencies: authentication and role-based authorization."""

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User, UserRole
from app.repositories.user import UserRepository

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Resolve the authenticated user from the access token (protected routes)."""
    if credentials is None:
        raise UnauthorizedError("Authentication required")

    payload = decode_token(credentials.credentials, expected_type="access")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise UnauthorizedError("Invalid or expired token") from None

    user = UserRepository(db).get(user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("Invalid or expired token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole):
    """Dependency factory enforcing that the user holds one of ``roles``.

    Admins always pass. Usage: ``Depends(require_roles(UserRole.SUPPORT_AGENT))``.
    """

    def _checker(user: CurrentUser) -> User:
        if user.role != UserRole.ADMIN and user.role not in roles:
            raise ForbiddenError("Insufficient permissions for this resource")
        return user

    return _checker
