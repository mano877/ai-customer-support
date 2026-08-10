"""Customer service: profile read/update and address book management."""

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.customer import Address, CustomerProfile
from app.models.user import User
from app.repositories.customer import AddressRepository, CustomerProfileRepository
from app.schemas.customer import (
    AddressCreate,
    AddressResponse,
    AddressUpdate,
    CustomerResponse,
    CustomerUpdateRequest,
)


class CustomerService:
    """Business logic for the customer module."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.profiles = CustomerProfileRepository(db)
        self.addresses = AddressRepository(db)

    # ------------------------------------------------------------------ #
    # Profile
    # ------------------------------------------------------------------ #
    def get_customer(self, user: User) -> CustomerResponse:
        """Return the combined user + profile view of the customer."""
        profile = self._get_or_create_profile(user)
        return self._to_customer_response(user, profile)

    def update_customer(self, user: User, payload: CustomerUpdateRequest) -> CustomerResponse:
        """Apply a partial update across the user record and its profile.

        Explicit nulls clear nullable fields (phone, avatar_url, ...) and are
        ignored for non-nullable fields (marketing_opt_in, preferred_language).
        """
        profile = self._get_or_create_profile(user)
        data = payload.model_dump(exclude_unset=True)

        # Non-nullable profile columns cannot be cleared to NULL.
        for field in ("marketing_opt_in", "preferred_language"):
            if field in data and data[field] is None:
                del data[field]

        if "full_name" in data:
            user.full_name = data.pop("full_name")
        if "phone" in data:
            user.phone = data.pop("phone")
        if "marketing_opt_in" in data:
            profile.marketing_opt_in = data.pop("marketing_opt_in")
        if "preferred_language" in data:
            profile.preferred_language = data.pop("preferred_language")
        if "date_of_birth" in data:
            profile.date_of_birth = data.pop("date_of_birth")
        if "gender" in data:
            profile.gender = data.pop("gender")
        if "avatar_url" in data:
            profile.avatar_url = data.pop("avatar_url")

        self.db.commit()
        self.db.refresh(user)
        self.db.refresh(profile)
        return self._to_customer_response(user, profile)

    # ------------------------------------------------------------------ #
    # Addresses
    # ------------------------------------------------------------------ #
    def list_addresses(self, user: User) -> list[AddressResponse]:
        addresses = self.addresses.list_for_user(user.id)
        return [AddressResponse.model_validate(address) for address in addresses]

    def create_address(self, user: User, payload: AddressCreate) -> AddressResponse:
        """Create an address; the first address or any explicitly-default one wins."""
        address = Address(user_id=user.id, **payload.model_dump())
        if payload.is_default or self.addresses.count_for_user(user.id) == 0:
            self.addresses.clear_default_for_user(user.id)
            address.is_default = True
        self.addresses.add(address)
        self.db.commit()
        self.db.refresh(address)
        return AddressResponse.model_validate(address)

    def update_address(
        self, user: User, address_id: uuid.UUID, payload: AddressUpdate
    ) -> AddressResponse:
        """Update an address owned by the user (404 for foreign addresses)."""
        address = self.addresses.get_for_user(address_id, user.id)
        if address is None:
            raise NotFoundError("Address not found")

        data = payload.model_dump(exclude_unset=True)
        is_default = data.get("is_default")
        if is_default is True:
            self.addresses.clear_default_for_user(user.id, except_id=address.id)
        elif is_default is False and address.is_default:
            # Unsetting the current default: promote the newest remaining address.
            remaining = [a for a in self.addresses.list_for_user(user.id) if a.id != address.id]
            if remaining:
                remaining[0].is_default = True

        for field, value in data.items():
            setattr(address, field, value)
        self.db.commit()
        self.db.refresh(address)
        return AddressResponse.model_validate(address)

    def delete_address(self, user: User, address_id: uuid.UUID) -> None:
        """Delete an address; if it was the default, promote the newest remaining."""
        address = self.addresses.get_for_user(address_id, user.id)
        if address is None:
            raise NotFoundError("Address not found")

        was_default = address.is_default
        self.addresses.delete(address)
        self.db.commit()

        if was_default:
            remaining = self.addresses.list_for_user(user.id)
            if remaining:
                remaining[0].is_default = True
                self.db.commit()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _get_or_create_profile(self, user: User) -> CustomerProfile:
        """Return the user's profile, creating a default one if missing."""
        profile = self.profiles.get_by_user_id(user.id)
        if profile is None:
            profile = CustomerProfile(user_id=user.id)
            self.db.add(profile)
            self.db.commit()
            self.db.refresh(profile)
        return profile

    @staticmethod
    def _to_customer_response(user: User, profile: CustomerProfile) -> CustomerResponse:
        return CustomerResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            phone=user.phone,
            role=user.role,
            is_verified=user.is_verified,
            created_at=user.created_at,
            date_of_birth=profile.date_of_birth,
            gender=profile.gender,
            marketing_opt_in=profile.marketing_opt_in,
            preferred_language=profile.preferred_language,
            avatar_url=profile.avatar_url,
        )
