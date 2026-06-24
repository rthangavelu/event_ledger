"""Shared ASGI middleware: trace context, request logging, and metrics.

Both services install this so they behave identically with respect to
observability. Keeping it here avoids drift between the two services.

This is implemented as a *pure ASGI* middleware rather than Starlette's
``BaseHTTPMiddleware`` on purpose: ``contextvars`` set inside a
``BaseHTTPMiddleware.dispatch`` do not reliably propagate into the route
handler (Starlette runs the inner app in a separate task), which would break
trace-ID logging from endpoints. A pure ASGI middleware runs the inner app in
the same context, so the trace context we set here is visible everywhere
downstream.
"""

from __future__ import annotations

import logging
import time

from . import tracing
from .metrics import MetricsRegistry

logger = logging.getLogger("http")


class ObservabilityMiddleware:
    def __init__(self, app, metrics: MetricsRegistry) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header(scope, b"traceparent")
        ctx = tracing.start_span(incoming)
        token = tracing.set_context(ctx)

        method = scope.get("method", "GET")
        start = time.perf_counter()
        state = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                headers = message.setdefault("headers", [])
                headers.append(
                    (b"traceparent", tracing.format_traceparent(ctx).encode())
                )
                headers.append((b"x-trace-id", ctx.trace_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            status = state["status"]
            # The router sets scope["route"] during handling; use its template
            # (e.g. /events/{id}) to keep metric label cardinality bounded.
            route = scope.get("route")
            path = getattr(route, "path", None) or scope.get("path", "unknown")
            self.metrics.requests_total.inc(
                method=method, path=path, status=str(status)
            )
            self.metrics.request_latency.observe(duration, method=method, path=path)
            if status >= 500:
                self.metrics.errors_total.inc(method=method, path=path)
            logger.info(
                "request.completed",
                extra={
                    "http_method": method,
                    "http_path": scope.get("path"),
                    "http_route": path,
                    "http_status": status,
                    "duration_ms": round(duration * 1000, 2),
                },
            )
            tracing.reset_context(token)


def _header(scope, name: bytes):
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None
