"""Resiliency + graceful degradation when the Account Service misbehaves."""

import httpx

from gateway.account_client import AccountClient, AccountServiceUnavailable
from gateway.circuit_breaker import CircuitBreaker, CircuitState
from tests.conftest import _free_port, make_gateway_client, sample_event


def _dead_url() -> str:
    # A port with nothing listening -> connection refused (fast failure).
    return f"http://127.0.0.1:{_free_port()}"


def test_post_returns_503_when_account_service_down():
    client = make_gateway_client(
        _dead_url(), timeout=0.2, max_retries=1, backoff_base=0.01, backoff_max=0.02
    )
    resp = client.post("/events", json=sample_event())
    assert resp.status_code == 503
    assert resp.json()["error"] == "account_service_unavailable"


def test_balance_query_returns_503_when_account_down():
    client = make_gateway_client(
        _dead_url(), timeout=0.2, max_retries=1, backoff_base=0.01, backoff_max=0.02
    )
    resp = client.get("/accounts/acct-123/balance")
    assert resp.status_code == 503
    assert resp.json()["error"] == "account_service_unavailable"


def test_reads_still_work_after_account_service_goes_down(account_server):
    # Account is up: record an event so the Gateway has local data.
    client = make_gateway_client(account_server.base_url)
    client.post("/events", json=sample_event(eventId="durable"))

    # Account Service goes away.
    account_server.stop()

    # Local reads keep working (served entirely from the Gateway DB)...
    single = client.get("/events/durable")
    assert single.status_code == 200
    assert single.json()["eventId"] == "durable"

    listing = client.get("/events", params={"account": "acct-123"})
    assert listing.status_code == 200
    assert listing.json()["count"] == 1

    # ...but a balance query (which needs the downstream) degrades clearly.
    assert client.get("/accounts/acct-123/balance").status_code == 503


def test_circuit_breaker_opens_and_fails_fast():
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=60.0)
    client = make_gateway_client(
        _dead_url(),
        breaker=breaker,
        timeout=0.2,
        max_retries=0,
        backoff_base=0.01,
        backoff_max=0.02,
    )

    assert breaker.state == CircuitState.CLOSED
    # Two failed calls reach the threshold and trip the breaker.
    client.post("/events", json=sample_event(eventId="f1"))
    client.post("/events", json=sample_event(eventId="f2"))
    assert breaker.state == CircuitState.OPEN

    # While open, calls fail fast without touching the network.
    resp = client.post("/events", json=sample_event(eventId="f3"))
    assert resp.status_code == 503

    # Health reflects the open circuit as a diagnostic.
    assert client.get("/health").json()["checks"]["account_service_circuit"] == "OPEN"


def test_circuit_breaker_recovers_after_timeout():
    # Manual clock so we can simulate the recovery window deterministically.
    now = {"t": 0.0}
    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout=5.0,
        success_threshold=1,
        clock=lambda: now["t"],
    )
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False

    # After the cooldown, a trial request is allowed (HALF_OPEN) and success closes it.
    now["t"] = 6.0
    assert breaker.allow_request() is True
    assert breaker.state == CircuitState.HALF_OPEN
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


def test_retry_eventually_succeeds_on_transient_failure(account_server):
    # A flaky transport that fails the first call, then delegates to the real one.
    real = httpx.Client(base_url=account_server.base_url, timeout=2.0)
    state = {"calls": 0}
    original_request = real.request

    def flaky_request(method, url, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise httpx.ConnectError("simulated transient failure")
        return original_request(method, url, **kwargs)

    real.request = flaky_request  # type: ignore[assignment]
    account_client = AccountClient(
        base_url=account_server.base_url, client=real
    )
    account_client.max_retries = 2
    account_client.backoff_base = 0.01
    account_client.backoff_max = 0.02

    from gateway.db import GatewayDB
    from gateway.main import create_app
    from starlette.testclient import TestClient

    client = TestClient(create_app(GatewayDB(":memory:"), account_client))
    resp = client.post("/events", json=sample_event(eventId="retry-me"))
    assert resp.status_code == 201
    assert state["calls"] >= 2  # proves a retry happened
