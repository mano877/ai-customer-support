"""Authentication endpoints.

Routers stay thin: they parse/validate input and delegate to the service layer.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.dependencies.auth import CurrentUser
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    VerifyOtpRequest,
)
from app.schemas.token import TokenResponse
from app.schemas.user import UserResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: RegisterRequest,
    db: Annotated[Session, Depends(get_db)],
) -> RegisterResponse:
    """Create a customer account. Returns an OTP that must be verified next."""
    user, otp = AuthService(db).register(payload)
    dev_otp = otp if get_settings().EXPOSE_OTP_IN_RESPONSE else None
    return RegisterResponse(
        user=UserResponse.model_validate(user),
        requires_otp_verification=True,
        dev_otp=dev_otp,
    )


@router.post("/verify-otp", response_model=TokenResponse)
def verify_otp(
    payload: VerifyOtpRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Verify the registration OTP, activate the account, and return tokens."""
    return AuthService(db).verify_otp(payload.email, payload.otp)


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Authenticate with email/password and receive an access + refresh pair."""
    return AuthService(db).login(payload)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    payload: RefreshRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Rotate a refresh token: old token is revoked, a new pair is issued."""
    return AuthService(db).refresh(payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    payload: LogoutRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Revoke the presented refresh token (idempotent)."""
    AuthService(db).logout(user, payload.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> User:
    """Return the profile of the authenticated user."""
    return user
