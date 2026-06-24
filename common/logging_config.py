"""Structured (JSON) logging shared by both services.

Every log line is a single JSON object containing at minimum: timestamp, log
level, service name, logger name, message, and -- when available -- the active
trace and span IDs. This makes logs trivially greppable/ingestable and lets you
follow a single request across both services by its trace ID.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any, Dict

from . import tracing

# Standard ``LogRecord`` attributes; anything else passed via ``extra`` is
# treated as a structured field and merged into the JSON output.
_RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}


class TraceContextFilter(logging.Filter):
    """Attach the current trace/span IDs to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = tracing.get_trace_id()
        record.span_id = tracing.get_span_id()
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None),
            "span_id": getattr(record, "span_id", None),
        }
        # Merge any structured extras supplied by the caller.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service_name: str, level: int = logging.INFO) -> None:
    """Install the JSON formatter + trace filter on the root logger.

    Idempotent: repeated calls (e.g. across tests) reconfigure cleanly rather
    than stacking handlers.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service_name))
    handler.addFilter(TraceContextFilter())
    root.addHandler(handler)

    # uvicorn installs its own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True

    # httpx/httpcore emit a request log line per call; keep our output focused.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
