"""Token-related schemas."""

from typing import Literal

from pydantic import BaseModel


class TokenResponse(BaseModel):
    """Access + refresh token pair returned by auth endpoints."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
