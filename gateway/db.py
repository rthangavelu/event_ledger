"""SQLite-backed storage for the Event Gateway.

The Gateway persists every accepted event in its *own* database (separate from
the Account Service). This is what lets ``GET /events/{id}`` and
``GET /events?account=...`` keep working even when the Account Service is down.
Listings are returned ordered by ``event_timestamp`` so out-of-order arrivals
still read back chronologically.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import List, Optional

from common import audit

from .models import StoredEvent


class GatewayDB:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.getenv("GATEWAY_DB_PATH", ":memory:")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id        TEXT PRIMARY KEY,
                    account_id      TEXT NOT NULL,
                    type            TEXT NOT NULL,
                    amount          TEXT NOT NULL,
                    currency        TEXT NOT NULL,
                    event_timestamp TEXT NOT NULL,
                    metadata_json   TEXT,
                    received_at     TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_account "
                "ON events(account_id, event_timestamp)"
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

    def get_event(self, event_id: str) -> Optional[StoredEvent]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return StoredEvent.from_row(row) if row else None

    def insert_event(self, event: StoredEvent) -> bool:
        """Insert an event. Returns ``False`` if the event_id already exists."""
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO events
                        (event_id, account_id, type, amount, currency,
                         event_timestamp, metadata_json, received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.account_id,
                        event.type,
                        event.amount,
                        event.currency,
                        event.event_timestamp,
                        event.metadata_json,
                        event.received_at,
                    ),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                self._conn.rollback()
                return False

    def list_events(self, account_id: str) -> List[StoredEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE account_id = ? "
                "ORDER BY event_timestamp ASC, received_at ASC",
                (account_id,),
            ).fetchall()
        return [StoredEvent.from_row(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
