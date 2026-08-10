"""FastAPI application factory.

Run with: ``uvicorn app.main:app --reload``
"""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.endpoints.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import setup_logging
from app.middleware.request_logging import RequestLoggingMiddleware

logger = logging.getLogger("app")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)

    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="AI Customer Care Bot backend API.",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Middleware order matters: request logging should wrap everything.
    application.add_middleware(RequestLoggingMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(health_router)
    application.include_router(api_router, prefix=settings.API_V1_PREFIX)

    register_exception_handlers(application)
    return application


def register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "App error code=%s path=%s detail=%s", exc.code, request.url.path, exc.message
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.message}},
        )

    @application.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": {"code": "internal_error", "message": "Internal server error"}},
        )


app = create_app()
