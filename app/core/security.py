"""Security helpers: password hashing, JWT creation/validation, OTP generation.

Uses Passlib (bcrypt) for password hashing and PyJWT for tokens.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def now_utc() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(UTC)


def ensure_aware(dt: datetime | None) -> datetime | None:
    """Return a datetime that is timezone-aware (UTC).

    SQLite returns naive datetimes even for ``DateTime(timezone=True)`` columns;
    Postgres returns aware ones. Normalizing avoids naive/aware comparison errors.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def hash_password(password: str) -> str:
    """Hash a plain-text password with bcrypt."""
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against its bcrypt hash."""
    try:
        return _pwd_context.verify(plain_password, hashed_password)
    except ValueError:
        return False


def _create_token(
    user_id: uuid.UUID, role: str, token_type: str, expires: datetime
) -> tuple[str, datetime, uuid.UUID]:
    """Encode a JWT and return (token, expires_at, jti)."""
    settings = get_settings()
    jti = uuid.uuid4()
    now = now_utc()
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": token_type,
        "jti": str(jti),
        "iat": now,
        "exp": expires,
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, expires, jti


def create_access_token(user_id: uuid.UUID, role: str) -> tuple[str, datetime, uuid.UUID]:
    """Create a short-lived access token."""
    settings = get_settings()
    expires = now_utc() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return _create_token(user_id, role, "access", expires)


def create_refresh_token(user_id: uuid.UUID, role: str) -> tuple[str, datetime, uuid.UUID]:
    """Create a long-lived refresh token."""
    settings = get_settings()
    expires = now_utc() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return _create_token(user_id, role, "refresh", expires)


def decode_token(token: str, expected_type: str) -> dict:
    """Validate a JWT signature/expiry and enforce its token type.

    Raises UnauthorizedError for any invalid, expired, or wrongly-typed token.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.PyJWTError:
        raise UnauthorizedError("Invalid or expired token") from None

    if payload.get("type") != expected_type:
        raise UnauthorizedError("Invalid or expired token")
    return payload


def hash_token(token: str) -> str:
    """Return a sha256 digest used to store tokens at rest."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_otp(length: int = 6) -> str:
    """Generate a cryptographically secure numeric OTP."""
    return f"{secrets.randbelow(10**length):0{length}d}"
