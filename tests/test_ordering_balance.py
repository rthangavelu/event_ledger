"""Out-of-order tolerance and balance correctness."""

from tests.conftest import sample_event


def test_events_listed_in_chronological_order(gateway_client):
    # Submit in a deliberately scrambled order.
    gateway_client.post(
        "/events", json=sample_event(eventId="b", eventTimestamp="2026-05-15T12:00:00Z")
    )
    gateway_client.post(
        "/events", json=sample_event(eventId="a", eventTimestamp="2026-05-15T09:00:00Z")
    )
    gateway_client.post(
        "/events", json=sample_event(eventId="c", eventTimestamp="2026-05-15T18:00:00Z")
    )

    events = gateway_client.get("/events", params={"account": "acct-123"}).json()["events"]
    ids = [e["eventId"] for e in events]
    assert ids == ["a", "b", "c"]


def test_balance_correct_regardless_of_order(gateway_client):
    gateway_client.post(
        "/events",
        json=sample_event(
            eventId="late-credit",
            type="CREDIT",
            amount=200,
            eventTimestamp="2026-05-15T20:00:00Z",
        ),
    )
    gateway_client.post(
        "/events",
        json=sample_event(
            eventId="early-debit",
            type="DEBIT",
            amount=75.50,
            eventTimestamp="2026-05-15T08:00:00Z",
        ),
    )

    balance = gateway_client.get("/accounts/acct-123/balance").json()
    # 200 CREDIT - 75.50 DEBIT = 124.50
    assert balance["balance"] == "124.5"


def test_decimal_precision_is_exact(gateway_client):
    # Three 0.10 credits should equal exactly 0.30 (would fail with floats).
    for i in range(3):
        gateway_client.post(
            "/events", json=sample_event(eventId=f"p{i}", amount=0.10)
        )
    balance = gateway_client.get("/accounts/acct-123/balance").json()
    assert balance["balance"] == "0.3"
