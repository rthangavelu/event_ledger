"""Unit tests for the W3C trace-context helpers."""

from common import tracing


def test_new_ids_have_correct_length():
    assert len(tracing.new_trace_id()) == 32
    assert len(tracing.new_span_id()) == 16


def test_parse_valid_traceparent():
    tp = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    ctx = tracing.parse_traceparent(tp)
    assert ctx.trace_id == "0af7651916cd43dd8448eb211c80319c"
    assert ctx.parent_span_id == "b7ad6b7169203331"


def test_parse_invalid_traceparent_returns_none():
    assert tracing.parse_traceparent(None) is None
    assert tracing.parse_traceparent("") is None
    assert tracing.parse_traceparent("not-valid") is None
    assert tracing.parse_traceparent("00-short-x-01") is None


def test_start_span_without_parent_mints_new_trace():
    ctx = tracing.start_span(None)
    assert len(ctx.trace_id) == 32
    assert ctx.parent_span_id is None
    assert tracing.get_trace_id() == ctx.trace_id


def test_start_span_continues_existing_trace():
    upstream = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
    ctx = tracing.start_span(upstream)
    assert ctx.trace_id == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert ctx.parent_span_id == "bbbbbbbbbbbbbbbb"
    assert ctx.span_id != "bbbbbbbbbbbbbbbb"  # a fresh span for this hop


def test_outbound_headers_roundtrip():
    ctx = tracing.start_span(None)
    headers = tracing.outbound_headers()
    assert tracing.TRACEPARENT_HEADER in headers
    parsed = tracing.parse_traceparent(headers[tracing.TRACEPARENT_HEADER])
    assert parsed.trace_id == ctx.trace_id


def test_context_reset():
    token = tracing.set_context(None)
    assert tracing.get_trace_id() is None
    tracing.reset_context(token)
