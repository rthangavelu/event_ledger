"""Lightweight distributed tracing using W3C ``traceparent`` propagation.

We deliberately implement a small, dependency-free tracer instead of pulling in
the full OpenTelemetry SDK. It satisfies the project's tracing requirements:

* a trace ID is generated at the edge (Gateway) for each incoming request,
* the trace ID is propagated downstream over the standard ``traceparent`` header,
* both services attach the trace ID (and a per-service span ID) to their logs.

The trace context is stored in a :class:`contextvars.ContextVar` so it is
implicitly available to logging without threading it through every function.
The implementation is compatible with the W3C Trace Context spec, so swapping in
OpenTelemetry later would be straightforward.
"""

from __future__ import annotations

import contextvars
import os
import re
from dataclasses import dataclass
from typing import Optional

# W3C traceparent: version "-" trace-id "-" parent-id "-" trace-flags
#   00-<32 hex>-<16 hex>-<2 hex>
_TRACEPARENT_RE = re.compile(
    r"^[0-9a-f]{2}-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-[0-9a-f]{2}$"
)

TRACEPARENT_HEADER = "traceparent"


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None


_current: "contextvars.ContextVar[Optional[TraceContext]]" = contextvars.ContextVar(
    "trace_context", default=None
)


def _rand_hex(num_bytes: int) -> str:
    return os.urandom(num_bytes).hex()


def new_trace_id() -> str:
    return _rand_hex(16)  # 16 bytes -> 32 hex chars


def new_span_id() -> str:
    return _rand_hex(8)  # 8 bytes -> 16 hex chars


def parse_traceparent(header: Optional[str]) -> Optional[TraceContext]:
    """Parse an incoming ``traceparent`` header, returning ``None`` if invalid."""
    if not header:
        return None
    match = _TRACEPARENT_RE.match(header.strip())
    if not match:
        return None
    return TraceContext(
        trace_id=match.group("trace_id"),
        parent_span_id=match.group("span_id"),
        span_id=match.group("span_id"),
    )


def format_traceparent(ctx: TraceContext) -> str:
    """Render a ``traceparent`` header for the given context (sampled flag on)."""
    return f"00-{ctx.trace_id}-{ctx.span_id}-01"


def start_span(incoming_traceparent: Optional[str] = None) -> TraceContext:
    """Begin a new span for the current execution context.

    If an upstream ``traceparent`` is supplied we continue that trace; otherwise
    we mint a brand new trace ID (this is what happens at the public Gateway).
    A fresh span ID is always generated for this service's unit of work.
    """
    parent = parse_traceparent(incoming_traceparent)
    if parent is not None:
        ctx = TraceContext(
            trace_id=parent.trace_id,
            span_id=new_span_id(),
            parent_span_id=parent.parent_span_id,
        )
    else:
        ctx = TraceContext(trace_id=new_trace_id(), span_id=new_span_id())
    _current.set(ctx)
    return ctx


def set_context(ctx: Optional[TraceContext]) -> contextvars.Token:
    return _current.set(ctx)


def reset_context(token: contextvars.Token) -> None:
    _current.reset(token)


def get_context() -> Optional[TraceContext]:
    return _current.get()


def get_trace_id() -> Optional[str]:
    ctx = _current.get()
    return ctx.trace_id if ctx else None


def get_span_id() -> Optional[str]:
    ctx = _current.get()
    return ctx.span_id if ctx else None


def outbound_headers() -> dict:
    """Headers to inject into a downstream HTTP call to propagate the trace."""
    ctx = _current.get()
    if ctx is None:
        return {}
    return {TRACEPARENT_HEADER: format_traceparent(ctx)}
