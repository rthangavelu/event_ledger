"""Unit tests for the metrics registry and Prometheus exposition."""

from common.metrics import Counter, Histogram, MetricsRegistry


def test_counter_accumulates_by_labels():
    c = Counter("c", "desc")
    c.inc(method="GET", path="/a")
    c.inc(method="GET", path="/a")
    c.inc(method="POST", path="/a")
    snap = c.snapshot()
    assert snap[(("method", "GET"), ("path", "/a"))] == 2
    assert snap[(("method", "POST"), ("path", "/a"))] == 1


def test_counter_exposition_format():
    c = Counter("http_requests_total", "Total requests")
    c.inc(method="GET")
    text = "\n".join(c.expose())
    assert "# TYPE http_requests_total counter" in text
    assert 'http_requests_total{method="GET"} 1' in text


def test_histogram_observes_and_exposes():
    h = Histogram("lat", "latency", buckets=(0.1, 0.5, 1.0))
    for v in (0.05, 0.2, 0.7, 2.0):
        h.observe(v, path="/x")
    text = "\n".join(h.expose())
    assert "# TYPE lat histogram" in text
    assert "lat_count" in text
    assert "lat_sum" in text
    assert 'le="+Inf"' in text


def test_histogram_timer_records():
    h = Histogram("t", "timed")
    with h.time(path="/y"):
        pass
    text = "\n".join(h.expose())
    assert 'path="/y"' in text


def test_registry_render_and_snapshot():
    reg = MetricsRegistry()
    reg.requests_total.inc(method="GET", path="/h", status="200")
    reg.errors_total.inc(method="GET", path="/h")
    reg.request_latency.observe(0.01, method="GET", path="/h")
    rendered = reg.render()
    assert "http_requests_total" in rendered
    assert "http_request_duration_seconds_bucket" in rendered
    snap = reg.snapshot()
    assert snap["http_requests_total"] == 1
    assert snap["http_errors_total"] == 1
