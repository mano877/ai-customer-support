"""Auth service: registration, OTP verification, login, token refresh/rotation, logout.

Business logic lives here — routers stay thin and only orchestrate I/O.
"""

import uuid
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    UnauthorizedError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    ensure_aware,
    generate_otp,
    hash_password,
    hash_token,
    now_utc,
    verify_password,
)
from app.models.customer import CustomerProfile
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest, normalize_email
from app.schemas.token import TokenResponse


class AuthService:
    """Handles the full authentication lifecycle."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.refresh_tokens = RefreshTokenRepository(db)
        self.settings = get_settings()

    # ------------------------------------------------------------------ #
    # Registration & OTP verification
    # ------------------------------------------------------------------ #
    def register(self, payload: RegisterRequest) -> tuple[User, str]:
        """Create a new customer account and return it with the generated OTP.

        Raises ConflictError when the email is already registered.
        """
        email = normalize_email(payload.email)
        if self.users.get_by_email(email) is not None:
            raise ConflictError("An account with this email already exists")

        otp = generate_otp()
        # The UUID is generated eagerly so it can be referenced by the
        # one-to-one customer profile before the flush.
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password=hash_password(payload.password),
            full_name=payload.full_name,
            phone=payload.phone,
            role=UserRole.CUSTOMER,
            is_verified=False,
            is_active=True,
            otp_hash=hash_password(otp),
            otp_expires_at=now_utc() + timedelta(minutes=self.settings.OTP_EXPIRE_MINUTES),
        )
        self.users.add(user)
        # Every customer gets a default profile on registration.
        self.db.add(CustomerProfile(user_id=user.id))
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            # Only a unique-email violation means a duplicate account; anything
            # else is a genuine server-side problem and must not be masked.
            detail = str(exc.orig).lower()
            if "users.email" in detail or "ix_users_email" in detail:
                raise ConflictError("An account with this email already exists") from None
            raise
        self.db.refresh(user)
        return user, otp

    def verify_otp(self, email: str, otp: str) -> TokenResponse:
        """Verify a registration OTP, activate the account, and issue tokens."""
        user = self.users.get_by_email(email)
        if (
            user is None
            or user.otp_hash is None
            or user.otp_expires_at is None
            or not user.is_active
        ):
            raise BadRequestError("Invalid or expired OTP")

        if user.is_verified:
            raise BadRequestError("Account is already verified")

        if ensure_aware(user.otp_expires_at) < now_utc() or not verify_password(otp, user.otp_hash):
            # Brute-force protection: invalidate the OTP after repeated failures.
            user.otp_attempts += 1
            if user.otp_attempts >= self.settings.MAX_OTP_ATTEMPTS:
                user.otp_hash = None
                user.otp_expires_at = None
            self.db.commit()
            raise BadRequestError("Invalid or expired OTP")

        user.is_verified = True
        user.otp_hash = None
        user.otp_expires_at = None
        user.otp_attempts = 0
        self.db.commit()
        return self._issue_tokens(user)

    # ------------------------------------------------------------------ #
    # Login / refresh / logout
    # ------------------------------------------------------------------ #
    def login(self, payload: LoginRequest) -> TokenResponse:
        """Authenticate with email + password and return a token pair."""
        user = self.users.get_by_email(normalize_email(payload.email))
        if user is None or not verify_password(payload.password, user.hashed_password):
            raise UnauthorizedError("Invalid email or password")

        if not user.is_verified or not user.is_active:
            raise ForbiddenError("Account has not been verified yet")
        return self._issue_tokens(user)

    def refresh(self, refresh_token: str) -> TokenResponse:
        """Rotate a refresh token: atomically revoke the old one, issue a new pair."""
        payload = decode_token(refresh_token, expected_type="refresh")
        record = self.refresh_tokens.get_by_token_hash(hash_token(refresh_token))
        if record is None or record.is_expired or str(record.jti) != payload.get("jti"):
            raise UnauthorizedError("Invalid or expired refresh token")

        user = self.users.get(record.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("Invalid or expired refresh token")

        # Single-use rotation, made atomic so concurrent double-use of the same
        # token can only succeed once (the losing request finds it revoked).
        result = self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.id == record.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now_utc())
        )
        if result.rowcount != 1:
            raise UnauthorizedError("Invalid or expired refresh token")

        return self._issue_tokens(user)

    def logout(self, user: User, refresh_token: str) -> None:
        """Revoke the presented refresh token (idempotent)."""
        record = self.refresh_tokens.get_by_token_hash(hash_token(refresh_token))
        if record is not None and record.user_id == user.id and not record.is_revoked:
            record.revoked_at = now_utc()
            self.db.commit()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _issue_tokens(self, user: User) -> TokenResponse:
        """Create an access/refresh pair, persist the refresh token, and commit."""
        access_token, _access_expires, _access_jti = create_access_token(
            user.id, user.role.value
        )
        refresh_token, refresh_expires, refresh_jti = create_refresh_token(
            user.id, user.role.value
        )
        self.refresh_tokens.add(
            RefreshToken(
                user_id=user.id,
                jti=uuid.UUID(str(refresh_jti)),
                token_hash=hash_token(refresh_token),
                expires_at=refresh_expires,
            )
        )
        self.db.commit()
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self.settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
