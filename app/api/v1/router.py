"""Aggregated API v1 router."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    chat,
    customers,
    feedback,
    knowledge,
    notifications,
    orders,
    products,
    tickets,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(customers.router)
api_router.include_router(products.router)
api_router.include_router(knowledge.router)
api_router.include_router(orders.router)
api_router.include_router(chat.router)
api_router.include_router(tickets.router)
api_router.include_router(feedback.router)
api_router.include_router(notifications.router)
