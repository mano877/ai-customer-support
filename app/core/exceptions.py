"""Domain exceptions with associated HTTP status codes."""


class AppError(Exception):
    """Base class for all application-level errors."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class BadRequestError(AppError):
    """Invalid client input."""

    status_code = 400
    code = "bad_request"


class UnauthorizedError(AppError):
    """Missing or invalid authentication credentials."""

    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    """Authenticated but without the required role/permission."""

    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    """Requested resource does not exist."""

    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    """Request conflicts with current resource state (e.g. duplicate)."""

    status_code = 409
    code = "conflict"
