"""Authentication-related schemas."""

from pydantic import BaseModel, EmailStr, Field

from app.schemas.user import UserResponse

OTP_PATTERN = r"^\d{6}$"


class RegisterRequest(BaseModel):
    """Payload for POST /auth/register."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=32)


class RegisterResponse(BaseModel):
    """Result of a successful registration.

    ``dev_otp`` is only populated when ``EXPOSE_OTP_IN_RESPONSE`` is enabled
    (development). In production the OTP is delivered out-of-band.
    """

    user: UserResponse
    requires_otp_verification: bool = True
    dev_otp: str | None = None


class VerifyOtpRequest(BaseModel):
    """Payload for POST /auth/verify-otp."""

    email: EmailStr
    otp: str = Field(min_length=6, max_length=6, pattern=OTP_PATTERN)


class LoginRequest(BaseModel):
    """Payload for POST /auth/login."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    """Payload for POST /auth/refresh."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Payload for POST /auth/logout."""

    refresh_token: str


def normalize_email(email: str) -> str:
    """Normalize an email address for storage (lowercase)."""
    return email.strip().lower()
