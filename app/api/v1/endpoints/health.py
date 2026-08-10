"""Health-check endpoint."""

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """Liveness probe used by load balancers / orchestrators."""
    return {"status": "ok", "service": get_settings().APP_NAME}
