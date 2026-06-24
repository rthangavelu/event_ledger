"""SQLite-backed storage for the Account Service.

Each service owns its database; nothing is shared with the Gateway. By default
we use an in-memory SQLite database (``ACCOUNT_DB_PATH=:memory:``). We keep a
single connection with ``check_same_thread=False`` plus a lock, because FastAPI
executes sync endpoints across a threadpool and an in-memory DB only lives as
long as its connection.

Monetary amounts are stored as canonical decimal *strings* and summed with
:class:`decimal.Decimal` to avoid binary floating-point rounding errors -- this
matters for a financial ledger.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from decimal import Decimal
from typing import List, Optional

from common import audit

from .models import StoredTransaction


class AccountDB:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.getenv("ACCOUNT_DB_PATH", ":memory:")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    event_id        TEXT PRIMARY KEY,
                    account_id      TEXT NOT NULL,
                    type            TEXT NOT NULL,
                    amount          TEXT NOT NULL,
                    currency        TEXT NOT NULL,
                    event_timestamp TEXT NOT NULL,
                    created_at      TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tx_account "
                "ON transactions(account_id, event_timestamp)"
            )
            audit.init_audit_schema(self._conn)
            self._conn.commit()

    def append_audit(self, entry: audit.AuditEntry) -> None:
        audit.append_audit(self._conn, self._lock, entry)

    def list_audit(self, account_id: Optional[str] = None, limit: int = 100) -> List[dict]:
        return audit.list_audit(self._conn, self._lock, account_id, limit)

    def ping(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def get_transaction(self, event_id: str) -> Optional[StoredTransaction]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM transactions WHERE event_id = ?", (event_id,)
            ).fetchone()
        return StoredTransaction.from_row(row) if row else None

    def insert_transaction(self, tx: StoredTransaction) -> bool:
        """Insert a transaction. Returns ``False`` if the event_id already exists
        (idempotent no-op), ``True`` if a new row was created."""
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO transactions
                        (event_id, account_id, type, amount, currency,
                         event_timestamp, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tx.event_id,
                        tx.account_id,
                        tx.type,
                        tx.amount,
                        tx.currency,
                        tx.event_timestamp,
                        tx.created_at,
                    ),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                self._conn.rollback()
                return False

    def list_transactions(self, account_id: str) -> List[StoredTransaction]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM transactions WHERE account_id = ? "
                "ORDER BY event_timestamp ASC, created_at ASC",
                (account_id,),
            ).fetchall()
        return [StoredTransaction.from_row(r) for r in rows]

    def balance(self, account_id: str) -> Decimal:
        total = Decimal("0")
        for tx in self.list_transactions(account_id):
            amt = Decimal(tx.amount)
            total += amt if tx.type == "CREDIT" else -amt
        return total

    def close(self) -> None:
        with self._lock:
            self._conn.close()
