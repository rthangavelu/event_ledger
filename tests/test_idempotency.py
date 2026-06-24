"""Idempotency: replaying an eventId must not duplicate or double-count."""

from tests.conftest import sample_event


def test_duplicate_event_returns_original(gateway_client):
    first = gateway_client.post("/events", json=sample_event())
    assert first.status_code == 201
    assert first.json()["duplicate"] is False

    second = gateway_client.post("/events", json=sample_event())
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["eventId"] == first.json()["eventId"]


def test_duplicate_does_not_change_balance(gateway_client):
    gateway_client.post("/events", json=sample_event(eventId="d1", amount=100))
    for _ in range(5):
        gateway_client.post("/events", json=sample_event(eventId="d1", amount=100))

    balance = gateway_client.get("/accounts/acct-123/balance").json()
    assert balance["balance"] == "100"
    assert balance["transactionCount"] == 1


def test_listing_has_no_duplicates(gateway_client):
    for _ in range(3):
        gateway_client.post("/events", json=sample_event(eventId="same"))
    listing = gateway_client.get("/events", params={"account": "acct-123"}).json()
    assert listing["count"] == 1
