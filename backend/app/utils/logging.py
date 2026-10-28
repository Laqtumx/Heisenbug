"""Structured logging.

Every log line carries the request id of the request that caused it, so one
support question can be traced through the whole run. Locally the output stays
readable; in a deployed environment it becomes JSON for a log collector.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar

from app.config import settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_CONFIGURED = False


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return (
            f"{self.formatTime(record, '%H:%M:%S')} "
            f"{record.levelname:<8} [{getattr(record, 'request_id', '-')[:8]}] "
            f"{record.name}: {record.getMessage()}"
        )


def configure_logging() -> None:
    """Install the handlers once, whatever the entrypoint."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    if settings.environment == "local" and settings.debug:
        handler.setFormatter(_ConsoleFormatter())
    else:
        handler.setFormatter(_JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    # Uvicorn installs its own handlers; route them through ours so every line
    # has the same shape.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    _CONFIGURED = True
