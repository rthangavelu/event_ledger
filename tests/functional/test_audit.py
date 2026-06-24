"""Functional tests for the audit trail across the running stack."""

from tests.conftest import sample_event


def test_audit_records_accept_and_duplicate(gateway_client):
    gateway_client.post("/events", json=sample_event(eventId="au1"))
    gateway_client.post("/events", json=sample_event(eventId="au1"))  # duplicate

    entries = gateway_client.get("/audit", params={"account": "acct-123"}).json()["entries"]
    outcomes = [e["outcome"] for e in entries]
    assert "ACCEPTED" in outcomes
    assert "DUPLICATE" in outcomes


def test_audit_records_downstream_outage(account_server):
    from tests.conftest import make_gateway_client

    client = make_gateway_client(account_server.base_url)
    client.post("/events", json=sample_event(eventId="au2"))
    account_server.stop()
    client.post("/events", json=sample_event(eventId="au3"))

    entries = client.get("/audit").json()["entries"]
    outcomes = [e["outcome"] for e in entries]
    assert "DOWNSTREAM_UNAVAILABLE" in outcomes


def test_account_service_audit_trail(gateway_client, account_server):
    import httpx

    gateway_client.post("/events", json=sample_event(eventId="au4"))
    acct_audit = httpx.get(
        f"{account_server.base_url}/audit", params={"account": "acct-123"}
    ).json()["entries"]
    assert any(e["outcome"] == "APPLIED" for e in acct_audit)
    # Trace ID is recorded in the audit row -> ties the audit to the request.
    assert all("trace_id" in e for e in acct_audit)
