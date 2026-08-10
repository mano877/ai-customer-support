"""Thin entrypoint kept for backward compatibility.

The application lives in the `app` package: `uvicorn app.main:app`.
"""

from app.main import app  # noqa: F401
