"""Customer endpoints: profile and address book."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies.auth import CurrentUser
from app.schemas.customer import (
    AddressCreate,
    AddressResponse,
    AddressUpdate,
    CustomerResponse,
    CustomerUpdateRequest,
)
from app.services.customer import CustomerService

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("/me", response_model=CustomerResponse)
def get_me(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> CustomerResponse:
    """Return the authenticated customer's profile."""
    return CustomerService(db).get_customer(user)


@router.patch("/me", response_model=CustomerResponse)
def update_me(
    payload: CustomerUpdateRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> CustomerResponse:
    """Partially update the authenticated customer's profile."""
    return CustomerService(db).update_customer(user, payload)


@router.get("/addresses", response_model=list[AddressResponse])
def list_addresses(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> list[AddressResponse]:
    """List the authenticated user's addresses (default first)."""
    return CustomerService(db).list_addresses(user)


@router.post(
    "/addresses",
    response_model=AddressResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_address(
    payload: AddressCreate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> AddressResponse:
    """Create a new address for the authenticated user."""
    return CustomerService(db).create_address(user, payload)


@router.patch("/addresses/{address_id}", response_model=AddressResponse)
def update_address(
    address_id: uuid.UUID,
    payload: AddressUpdate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> AddressResponse:
    """Update one of the authenticated user's addresses."""
    return CustomerService(db).update_address(user, address_id, payload)


@router.delete("/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_address(
    address_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Delete one of the authenticated user's addresses."""
    CustomerService(db).delete_address(user, address_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
