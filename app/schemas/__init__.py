"""Pydantic schemas."""

from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    VerifyOtpRequest,
)
from app.schemas.customer import (
    AddressCreate,
    AddressResponse,
    AddressUpdate,
    CustomerResponse,
    CustomerUpdateRequest,
)
from app.schemas.knowledge import KnowledgeArticleResponse
from app.schemas.order import (
    OrderCreate,
    OrderItemCreate,
    OrderItemResponse,
    OrderListResponse,
    OrderResponse,
    OrderTrackingResponse,
    TrackingEventResponse,
)
from app.schemas.product import ProductListResponse, ProductResponse
from app.schemas.token import TokenResponse
from app.schemas.user import UserResponse

__all__ = [
    "AddressCreate",
    "AddressResponse",
    "AddressUpdate",
    "CustomerResponse",
    "CustomerUpdateRequest",
    "KnowledgeArticleResponse",
    "LoginRequest",
    "LogoutRequest",
    "OrderCreate",
    "OrderItemCreate",
    "OrderItemResponse",
    "OrderListResponse",
    "OrderResponse",
    "OrderTrackingResponse",
    "ProductListResponse",
    "ProductResponse",
    "RefreshRequest",
    "RegisterRequest",
    "RegisterResponse",
    "TokenResponse",
    "TrackingEventResponse",
    "UserResponse",
    "VerifyOtpRequest",
]
