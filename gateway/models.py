"""API contract + storage models for the Event Gateway."""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

TxType = Literal["CREDIT", "DEBIT"]


class EventRequest(BaseModel):
    """Payload for POST /events. Validation here enforces the public contract."""

    model_config = ConfigDict(extra="forbid")

    eventId: str = Field(min_length=1)
    accountId: str = Field(min_length=1)
    type: TxType
    amount: Decimal
    currency: str = Field(min_length=1)
    eventTimestamp: _dt.datetime
    metadata: Optional[Dict[str, Any]] = None

    @field_validator("amount")
    @classmethod
    def _amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be greater than 0")
        return v

    @field_validator("eventId", "accountId", "currency")
    @classmethod
    def _strip_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class EventResponse(BaseModel):
    eventId: str
    accountId: str
    type: TxType
    amount: str
    currency: str
    eventTimestamp: str
    metadata: Optional[Dict[str, Any]] = None
    receivedAt: str
    duplicate: bool = False


@dataclass
class StoredEvent:
    event_id: str
    account_id: str
    type: str
    amount: str
    currency: str
    event_timestamp: str
    metadata_json: Optional[str]
    received_at: str

    @classmethod
    def from_row(cls, row) -> "StoredEvent":
        return cls(
            event_id=row["event_id"],
            account_id=row["account_id"],
            type=row["type"],
            amount=row["amount"],
            currency=row["currency"],
            event_timestamp=row["event_timestamp"],
            metadata_json=row["metadata_json"],
            received_at=row["received_at"],
        )

    def to_response(self, duplicate: bool = False) -> EventResponse:
        return EventResponse(
            eventId=self.event_id,
            accountId=self.account_id,
            type=self.type,
            amount=self.amount,
            currency=self.currency,
            eventTimestamp=self.event_timestamp,
            metadata=json.loads(self.metadata_json) if self.metadata_json else None,
            receivedAt=self.received_at,
            duplicate=duplicate,
        )
