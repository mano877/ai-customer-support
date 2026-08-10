"""Customer profile and address schemas."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.user import UserRole


class CustomerUpdateRequest(BaseModel):
    """Partial update of the customer profile. At least one field required."""

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=32)
    marketing_opt_in: bool | None = None
    preferred_language: str | None = Field(default=None, min_length=2, max_length=16)
    avatar_url: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "CustomerUpdateRequest":
        # An empty body is invalid; an explicit null counts as providing a field
        # (used to clear nullable fields like phone / avatar_url).
        if not self.model_dump(exclude_unset=True):
            raise ValueError("At least one field must be provided")
        return self


class CustomerResponse(BaseModel):
    """Full profile of the authenticated customer (user + profile fields)."""

    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    phone: str | None = None
    role: UserRole
    is_verified: bool
    created_at: datetime
    date_of_birth: date | None = None
    gender: str | None = None
    marketing_opt_in: bool
    preferred_language: str
    avatar_url: str | None = None


class AddressCreate(BaseModel):
    """Payload for POST /customers/addresses."""

    label: str = Field(default="Home", min_length=1, max_length=32)
    recipient_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    street: str = Field(min_length=1, max_length=255)
    city: str = Field(min_length=1, max_length=128)
    state: str | None = Field(default=None, max_length=128)
    postal_code: str = Field(min_length=1, max_length=32)
    country: str = Field(default="US", min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    is_default: bool = False

    @model_validator(mode="after")
    def _uppercase_country(self) -> "AddressCreate":
        self.country = self.country.upper()
        return self


class AddressUpdate(BaseModel):
    """Partial update of an address. At least one field required."""

    label: str | None = Field(default=None, min_length=1, max_length=32)
    recipient_name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    street: str | None = Field(default=None, min_length=1, max_length=255)
    city: str | None = Field(default=None, min_length=1, max_length=128)
    state: str | None = Field(default=None, max_length=128)
    postal_code: str | None = Field(default=None, min_length=1, max_length=32)
    country: str | None = Field(default=None, min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    is_default: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "AddressUpdate":
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("At least one field must be provided")
        return self

    @model_validator(mode="after")
    def _uppercase_country(self) -> "AddressUpdate":
        if self.country is not None:
            self.country = self.country.upper()
        return self


class AddressResponse(BaseModel):
    """Public representation of an address."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    recipient_name: str
    phone: str | None = None
    street: str
    city: str
    state: str | None = None
    postal_code: str
    country: str
    is_default: bool
    created_at: datetime
    updated_at: datetime
