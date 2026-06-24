"""Distributed trace propagation across Gateway -> Account Service."""

import logging
from contextlib import contextmanager

from common.logging_config import TraceContextFilter
from common.tracing import format_traceparent, parse_traceparent, TraceContext
from tests.conftest import sample_event


@contextmanager
def capture_logs():
    """Collect (logger_name, message, trace_id) from all services' log records."""
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(
                (record.name, record.getMessage(), getattr(record, "trace_id", None))
            )

    handler = _Capture()
    handler.addFilter(TraceContextFilter())  # populate trace_id at emit time
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield records
    finally:
        root.removeHandler(handler)


def _trace_ids_for(records, message):
    return [tid for name, msg, tid in records if msg == message]


def test_trace_id_in_response_header(gateway_client):
    resp = gateway_client.post("/events", json=sample_event())
    assert resp.status_code == 201
    assert "x-trace-id" in resp.headers
    assert len(resp.headers["x-trace-id"]) == 32


def test_trace_id_propagates_gateway_to_account(gateway_client):
    with capture_logs() as records:
        resp = gateway_client.post("/events", json=sample_event(eventId="traced"))
    trace_id = resp.headers["x-trace-id"]

    gateway_ids = _trace_ids_for(records, "event.accepted")
    account_ids = _trace_ids_for(records, "transaction.applied")

    assert trace_id in gateway_ids
    # Same trace ID shows up in the Account Service's log -> end-to-end trace.
    assert trace_id in account_ids


def test_gateway_continues_upstream_trace(gateway_client):
    upstream = TraceContext(
        trace_id="abcdef0123456789abcdef0123456789", span_id="1111111111111111"
    )
    with capture_logs() as records:
        resp = gateway_client.post(
            "/events",
            json=sample_event(eventId="upstream"),
            headers={"traceparent": format_traceparent(upstream)},
        )
    # Gateway continues the same trace id it was handed.
    assert resp.headers["x-trace-id"] == upstream.trace_id
    assert upstream.trace_id in _trace_ids_for(records, "transaction.applied")


def test_traceparent_roundtrip():
    ctx = TraceContext(
        trace_id="0af7651916cd43dd8448eb211c80319c", span_id="b7ad6b7169203331"
    )
    parsed = parse_traceparent(format_traceparent(ctx))
    assert parsed.trace_id == ctx.trace_id
    assert parse_traceparent("garbage") is None
