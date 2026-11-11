"""Logging: JSON lines in production, readable text locally.

Every record carries the current request id (if any) so logs from one request
can be followed across modules.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else was passed via ``extra=``.
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_") and value is not None:
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s")


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Install one stdout handler on the root logger (idempotent)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_ccradar", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler._ccradar = True  # type: ignore[attr-defined]
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    # httpx logs full outbound URLs at INFO, which can include tokens or codes.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # We emit our own access log with request ids and route templates.
    logging.getLogger("uvicorn.access").disabled = True
