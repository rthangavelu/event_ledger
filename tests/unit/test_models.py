"""Unit tests for request-model validation (pydantic contracts)."""

import datetime as _dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from account_service.models import TransactionRequest
from gateway.models import EventRequest, StoredEvent


def _valid_event(**overrides):
    data = {
        "eventId": "evt-1",
        "accountId": "acct-1",
        "type": "CREDIT",
        "amount": 100,
        "currency": "USD",
        "eventTimestamp": "2026-05-15T14:02:11Z",
    }
    data.update(overrides)
    return data


def test_valid_event_parses():
    ev = EventRequest(**_valid_event())
    assert ev.amount == Decimal("100")
    assert isinstance(ev.eventTimestamp, _dt.datetime)


@pytest.mark.parametrize("amount", [0, -1, -0.01])
def test_non_positive_amount_rejected(amount):
    with pytest.raises(ValidationError):
        EventRequest(**_valid_event(amount=amount))


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        EventRequest(**_valid_event(type="TRANSFER"))


def test_blank_fields_rejected():
    with pytest.raises(ValidationError):
        EventRequest(**_valid_event(accountId="   "))


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        EventRequest(**_valid_event(surprise="x"))


def test_bad_timestamp_rejected():
    with pytest.raises(ValidationError):
        EventRequest(**_valid_event(eventTimestamp="nope"))


def test_transaction_request_validates_amount():
    with pytest.raises(ValidationError):
        TransactionRequest(
            eventId="e", type="DEBIT", amount=-5, currency="USD",
            eventTimestamp="2026-05-15T14:02:11Z",
        )


def test_stored_event_roundtrip_to_response():
    se = StoredEvent(
        event_id="e1", account_id="a1", type="CREDIT", amount="100",
        currency="USD", event_timestamp="2026-05-15T14:02:11+00:00",
        metadata_json='{"source":"x"}', received_at="2026-06-24T00:00:00+00:00",
    )
    resp = se.to_response(duplicate=True)
    assert resp.duplicate is True
    assert resp.metadata == {"source": "x"}
