"""Input validation on POST /events."""

from tests.conftest import sample_event


def test_valid_event_is_accepted(gateway_client):
    resp = gateway_client.post("/events", json=sample_event())
    assert resp.status_code == 201
    assert resp.json()["duplicate"] is False


def test_missing_required_field_rejected(gateway_client):
    event = sample_event()
    del event["accountId"]
    resp = gateway_client.post("/events", json=event)
    assert resp.status_code == 400
    assert resp.json()["error"] == "validation_error"


def test_zero_amount_rejected(gateway_client):
    resp = gateway_client.post("/events", json=sample_event(eventId="z", amount=0))
    assert resp.status_code == 400


def test_negative_amount_rejected(gateway_client):
    resp = gateway_client.post("/events", json=sample_event(eventId="n", amount=-5))
    assert resp.status_code == 400


def test_unknown_type_rejected(gateway_client):
    resp = gateway_client.post(
        "/events", json=sample_event(eventId="t", type="TRANSFER")
    )
    assert resp.status_code == 400


def test_bad_timestamp_rejected(gateway_client):
    resp = gateway_client.post(
        "/events", json=sample_event(eventId="ts", eventTimestamp="not-a-date")
    )
    assert resp.status_code == 400


def test_unknown_field_rejected(gateway_client):
    resp = gateway_client.post(
        "/events", json=sample_event(eventId="x", surprise="boom")
    )
    assert resp.status_code == 400
