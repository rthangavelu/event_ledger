"""Consistent, observable error handling shared by both services.

Any unhandled exception is logged with a stack trace and the active trace ID,
then returned to the caller as a structured JSON ``500`` that echoes the trace
ID -- so a client error report can be tied straight back to the server logs.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from . import tracing

logger = logging.getLogger("errors")


def install_error_handlers(app: FastAPI, service_name: str) -> None:
    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception):
        trace_id = tracing.get_trace_id()
        logger.exception(
            "unhandled_exception",
            extra={
                "service": service_name,
                "http_path": request.url.path,
                "error_type": type(exc).__name__,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_error",
                "message": "An unexpected error occurred.",
                "trace_id": trace_id,
            },
        )
