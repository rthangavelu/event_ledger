"""Unit tests for the append-only audit trail."""

import sqlite3
import threading

from common import audit, tracing
from common.audit import AuditEntry, AuditTrail


def _conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    audit.init_audit_schema(conn)
    return conn


def test_append_and_list():
    conn = _conn()
    lock = threading.Lock()
    audit.append_audit(
        conn, lock,
        AuditEntry(
            timestamp="t1", service="svc", action="A", outcome="OK",
            account_id="acct-1", event_id="e1", trace_id="tr", detail={"k": "v"},
        ),
    )
    rows = audit.list_audit(conn, lock, account_id="acct-1")
    assert len(rows) == 1
    assert rows[0]["action"] == "A"
    assert rows[0]["detail"] == {"k": "v"}
    assert "detail_json" not in rows[0]


def test_list_filters_by_account_and_orders_desc():
    conn = _conn()
    lock = threading.Lock()
    for i in range(3):
        audit.append_audit(
            conn, lock,
            AuditEntry(timestamp=f"t{i}", service="svc", action="A", outcome="OK",
                       account_id="acct-1", event_id=f"e{i}"),
        )
    audit.append_audit(
        conn, lock,
        AuditEntry(timestamp="x", service="svc", action="A", outcome="OK",
                   account_id="acct-2", event_id="other"),
    )
    rows = audit.list_audit(conn, lock, account_id="acct-1")
    assert [r["event_id"] for r in rows] == ["e2", "e1", "e0"]  # newest first
    assert audit.list_audit(conn, lock) and len(audit.list_audit(conn, lock)) == 4


def test_audit_trail_persists_and_captures_trace_id():
    captured = []
    trail = AuditTrail("svc", sink=captured.append)
    tracing.start_span("00-" + "c" * 32 + "-" + "d" * 16 + "-01")
    trail.record("EVENT_SUBMIT", "ACCEPTED", account_id="acct-1", event_id="e1", amount="5")
    assert len(captured) == 1
    entry = captured[0]
    assert entry.action == "EVENT_SUBMIT"
    assert entry.trace_id == "c" * 32
    assert entry.detail == {"amount": "5"}


def test_audit_trail_survives_sink_failure():
    def boom(_entry):
        raise RuntimeError("db down")

    trail = AuditTrail("svc", sink=boom)
    # Must not raise -- auditing should never break the request path.
    trail.record("EVENT_SUBMIT", "ACCEPTED")
