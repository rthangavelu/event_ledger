"""Functional tests for the UI-driven fault-injection test controls.

These verify that toggling the injected fault drives the *real* resiliency code
paths (503 on writes, degraded balance, local reads still working, circuit
opens, then recovery), which is exactly what the UI's scenario runner exercises.
"""

import time

from gateway.circuit_breaker import CircuitBreaker
from tests.conftest import make_gateway_client, sample_event


def test_fault_toggle_endpoint(gateway_client):
    assert gateway_client.get("/test/fault").json()["mode"] == "off"
    r = gateway_client.post("/test/fault", params={"mode": "error"})
    assert r.json()["mode"] == "error"
    gateway_client.post("/test/fault", params={"mode": "off"})
    assert gateway_client.get("/test/fault").json()["mode"] == "off"


def test_injected_fault_degrades_writes_but_not_reads(account_server):
    client = make_gateway_client(
        account_server.base_url, max_retries=0, backoff_base=0.01, backoff_max=0.02
    )
    # Seed an event while healthy.
    assert client.post("/events", json=sample_event(eventId="seed")).status_code == 201

    # Inject an outage.
    client.post("/test/fault", params={"mode": "error"})
    assert client.post("/events", json=sample_event(eventId="x")).status_code == 503
    assert client.get("/accounts/acct-123/balance").status_code == 503
    # Local reads keep working.
    assert client.get("/events/seed").status_code == 200
    assert client.get("/events", params={"account": "acct-123"}).json()["count"] == 1

    # Clear the fault -> writes succeed again.
    client.post("/test/fault", params={"mode": "off"})
    assert client.post("/events", json=sample_event(eventId="after")).status_code == 201


def test_injected_fault_opens_circuit(account_server):
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=60.0)
    client = make_gateway_client(
        account_server.base_url, breaker=breaker, max_retries=0,
        backoff_base=0.01, backoff_max=0.02,
    )
    client.post("/test/fault", params={"mode": "error"})
    client.post("/events", json=sample_event(eventId="f1"))
    client.post("/events", json=sample_event(eventId="f2"))
    assert client.get("/health").json()["checks"]["account_service_circuit"] == "OPEN"


def test_test_controls_can_be_disabled(monkeypatch, account_server):
    monkeypatch.setenv("ENABLE_TEST_CONTROLS", "false")
    client = make_gateway_client(account_server.base_url)
    assert client.get("/test/fault").status_code == 404
