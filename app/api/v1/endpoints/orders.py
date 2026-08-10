"""Order endpoints: place, list, track, cancel, and return orders."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies.auth import CurrentUser
from app.schemas.order import (
    OrderCreate,
    OrderListResponse,
    OrderResponse,
    OrderTrackingResponse,
)
from app.services.order import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderCreate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> OrderResponse:
    """Place a new order for the authenticated customer."""
    return OrderService(db).create_order(user, payload)


@router.get("", response_model=OrderListResponse)
def list_orders(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> OrderListResponse:
    """List the authenticated user's orders, newest first."""
    return OrderService(db).list_orders(user, limit=limit, offset=offset)


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> OrderResponse:
    """Fetch one of the authenticated user's orders."""
    return OrderService(db).get_order(user, order_id)


@router.get("/{order_id}/tracking", response_model=OrderTrackingResponse)
def get_order_tracking(
    order_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> OrderTrackingResponse:
    """Return the tracking status and event history of an order."""
    return OrderService(db).get_tracking(user, order_id)


@router.post("/{order_id}/cancel", response_model=OrderResponse)
def cancel_order(
    order_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> OrderResponse:
    """Cancel a pending/paid/processing order (restores product stock)."""
    return OrderService(db).cancel_order(user, order_id)


@router.post("/{order_id}/return", response_model=OrderResponse)
def return_order(
    order_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> OrderResponse:
    """Request a return for a delivered order."""
    return OrderService(db).return_order(user, order_id)
