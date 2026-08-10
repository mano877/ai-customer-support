"""Request logging middleware (access log with request id + duration)."""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("app.access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request and attach an ``X-Request-ID`` header to the response."""

    async def dispatch(self, request: Request, call_next):
        request_id = uuid.uuid4().hex
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # Unhandled errors bypass the response path; still record them.
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "request_id=%s method=%s path=%s status=500 duration_ms=%.1f",
                request_id,
                request.method,
                request.url.path,
                duration_ms,
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response
