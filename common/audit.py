"""Append-only audit trail shared by both services.

Auditing is a first-class concern for a financial ledger: every meaningful
action (an event accepted, a duplicate rejected, a transaction applied, a
downstream outage) is recorded as an immutable row *and* emitted as a structured
``audit`` log line carrying the active trace ID. The table is insert-only --
there are no update/delete paths -- so it forms a tamper-evident history.

The persistence helpers operate on a raw sqlite connection + lock so both
services can reuse them against their own database without duplicating SQL.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional

from . import tracing

logger = logging.getLogger("audit")

AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT NOT NULL,
    service      TEXT NOT NULL,
    action       TEXT NOT NULL,
    outcome      TEXT NOT NULL,
    account_id   TEXT,
    event_id     TEXT,
    trace_id     TEXT,
    detail_json  TEXT
)
"""


@dataclass
class AuditEntry:
    timestamp: str
    service: str
    action: str
    outcome: str
    account_id: Optional[str] = None
    event_id: Optional[str] = None
    trace_id: Optional[str] = None
    detail: dict = field(default_factory=dict)


def init_audit_schema(conn: sqlite3.Connection) -> None:
    conn.execute(AUDIT_DDL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_account ON audit_log(account_id, id)"
    )
    conn.commit()


def append_audit(conn: sqlite3.Connection, lock: threading.Lock, entry: AuditEntry) -> None:
    with lock:
        conn.execute(
            """
            INSERT INTO audit_log
                (timestamp, service, action, outcome, account_id, event_id,
                 trace_id, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.timestamp,
                entry.service,
                entry.action,
                entry.outcome,
                entry.account_id,
                entry.event_id,
                entry.trace_id,
                json.dumps(entry.detail) if entry.detail else None,
            ),
        )
        conn.commit()


def list_audit(
    conn: sqlite3.Connection,
    lock: threading.Lock,
    account_id: Optional[str] = None,
    limit: int = 100,
) -> List[dict]:
    with lock:
        if account_id is not None:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE account_id = ? ORDER BY id DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        raw = item.pop("detail_json", None)
        item["detail"] = json.loads(raw) if raw else None
        result.append(item)
    return result


class AuditTrail:
    """Records audit entries to a sink (DB) and to the structured log stream."""

    def __init__(self, service_name: str, sink: Callable[[AuditEntry], None]) -> None:
        self.service_name = service_name
        self._sink = sink

    def record(
        self,
        action: str,
        outcome: str,
        *,
        account_id: Optional[str] = None,
        event_id: Optional[str] = None,
        **detail,
    ) -> None:
        entry = AuditEntry(
            timestamp=_dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
            service=self.service_name,
            action=action,
            outcome=outcome,
            account_id=account_id,
            event_id=event_id,
            trace_id=tracing.get_trace_id(),
            detail=detail,
        )
        try:
            self._sink(entry)
        except Exception:  # auditing must never break the request path
            logger.exception("audit.persist_failed", extra={"audit_action": action})
        logger.info(
            "audit",
            extra={
                "audit": True,
                "audit_action": action,
                "audit_outcome": outcome,
                "account_id": account_id,
                "event_id": event_id,
                **{f"audit_{k}": v for k, v in detail.items()},
            },
        )
