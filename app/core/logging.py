"""Logging configuration for the application."""

import logging
import sys

_CONFIGURED = False

LOGGING_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOGGING_FORMAT, datefmt=DATE_FORMAT))

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    root.addHandler(handler)
    # Keep uvicorn's access log from double-printing through the root logger.
    logging.getLogger("uvicorn.access").propagate = False
    _CONFIGURED = True
