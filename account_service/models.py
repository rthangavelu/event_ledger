"""API contract + storage models for the Account Service.

These Pydantic models define the contract the Gateway depends on. Amounts are
exchanged as JSON numbers but normalised to canonical decimal strings on the
way in/out so balances stay exact.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

TxType = Literal["CREDIT", "DEBIT"]


def _normalize_amount(value) -> str:
    """Return a canonical, non-negative decimal string."""
    dec = Decimal(str(value))
    if dec <= 0:
        raise ValueError("amount must be greater than 0")
    return format(dec.normalize(), "f")


class TransactionRequest(BaseModel):
    """Body of POST /accounts/{accountId}/transactions (sent by the Gateway)."""

    eventId: str = Field(min_length=1)
    type: TxType
    amount: Decimal
    currency: str = Field(min_length=1)
    eventTimestamp: _dt.datetime

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be greater than 0")
        return v


class TransactionResponse(BaseModel):
    eventId: str
    accountId: str
    type: TxType
    amount: str
    currency: str
    eventTimestamp: str
    createdAt: str
    duplicate: bool = False


class BalanceResponse(BaseModel):
    accountId: str
    balance: str
    currency: Optional[str] = None
    transactionCount: int


class AccountDetailResponse(BaseModel):
    accountId: str
    balance: str
    currency: Optional[str] = None
    transactionCount: int
    recentTransactions: List[TransactionResponse]


@dataclass
class StoredTransaction:
    event_id: str
    account_id: str
    type: str
    amount: str
    currency: str
    event_timestamp: str
    created_at: str

    @classmethod
    def from_row(cls, row) -> "StoredTransaction":
        return cls(
            event_id=row["event_id"],
            account_id=row["account_id"],
            type=row["type"],
            amount=row["amount"],
            currency=row["currency"],
            event_timestamp=row["event_timestamp"],
            created_at=row["created_at"],
        )

    def to_response(self, duplicate: bool = False) -> TransactionResponse:
        return TransactionResponse(
            eventId=self.event_id,
            accountId=self.account_id,
            type=self.type,
            amount=self.amount,
            currency=self.currency,
            eventTimestamp=self.event_timestamp,
            createdAt=self.created_at,
            duplicate=duplicate,
        )
