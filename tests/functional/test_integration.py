"""End-to-end integration across the full Gateway -> Account Service flow."""

from tests.conftest import sample_event


def test_full_flow_event_to_balance(gateway_client):
    # Submit a credit and a debit through the public Gateway.
    r1 = gateway_client.post(
        "/events",
        json=sample_event(eventId="i1", type="CREDIT", amount=500),
    )
    assert r1.status_code == 201

    r2 = gateway_client.post(
        "/events",
        json=sample_event(eventId="i2", type="DEBIT", amount=125.25),
    )
    assert r2.status_code == 201

    # Read the event back from the Gateway.
    got = gateway_client.get("/events/i1")
    assert got.status_code == 200
    assert got.json()["amount"] == "500"

    # Balance is computed by the Account Service and proxied through the Gateway.
    balance = gateway_client.get("/accounts/acct-123/balance").json()
    assert balance["balance"] == "374.75"
    assert balance["transactionCount"] == 2

    # Account detail comes from the Account Service too.
    detail = gateway_client.get("/accounts/acct-123").json()
    assert detail["transactionCount"] == 2
    assert len(detail["recentTransactions"]) == 2


def test_health_endpoints(gateway_client, account_server):
    gw = gateway_client.get("/health")
    assert gw.status_code == 200
    assert gw.json()["checks"]["database"] == "ok"

    import httpx

    acct = httpx.get(f"{account_server.base_url}/health")
    assert acct.status_code == 200
    assert acct.json()["checks"]["database"] == "ok"


def test_metrics_endpoint_exposes_custom_metric(gateway_client):
    gateway_client.post("/events", json=sample_event(eventId="m1"))
    metrics = gateway_client.get("/metrics")
    assert metrics.status_code == 200
    assert "http_requests_total" in metrics.text
    assert "http_request_duration_seconds" in metrics.text


def test_event_not_found_returns_404(gateway_client):
    assert gateway_client.get("/events/does-not-exist").status_code == 404


def test_ui_is_served(gateway_client):
    resp = gateway_client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Event Ledger" in resp.text
