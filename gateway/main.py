"""Event Gateway -- the public-facing service.

Receives transaction events, validates them, enforces idempotency, persists them
locally, and forwards the transaction to the Account Service through a resilient
client. Read endpoints are served from the Gateway's own DB so they remain
available even when the Account Service is down (graceful degradation).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from decimal import Decimal
from typing import Optional

from fastapi import Depends, FastAPI, Query, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

from common.audit import AuditTrail
from common.errors import install_error_handlers
from common.logging_config import configure_logging
from common.metrics import MetricsRegistry
from common.middleware import ObservabilityMiddleware

from .account_client import (
    AccountClient,
    AccountClientError,
    AccountServiceUnavailable,
)
from .db import GatewayDB
from .models import EventRequest, EventResponse, StoredEvent

SERVICE_NAME = "event-gateway"
logger = logging.getLogger(SERVICE_NAME)


def create_app(
    db: Optional[GatewayDB] = None, account_client: Optional[AccountClient] = None
) -> FastAPI:
    configure_logging(SERVICE_NAME)
    metrics = MetricsRegistry()
    app = FastAPI(title="Event Gateway", version="1.0.0")
    app.state.db = db or GatewayDB()
    app.state.account_client = account_client or AccountClient()
    app.state.metrics = metrics
    app.state.audit = AuditTrail(SERVICE_NAME, app.state.db.append_audit)
    app.add_middleware(ObservabilityMiddleware, metrics=metrics)
    install_error_handlers(app, SERVICE_NAME)

    def get_db() -> GatewayDB:
        return app.state.db

    def get_client() -> AccountClient:
        return app.state.account_client

    audit = app.state.audit

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request, exc: RequestValidationError):
        # Surface validation failures as 400 with meaningful, structured details.
        # pydantic stuffs the raw exception into ``ctx`` which isn't JSON-safe,
        # so we project each error onto a clean, serialisable shape.
        detail = [
            {
                "type": err.get("type"),
                "field": ".".join(str(p) for p in err.get("loc", []) if p != "body"),
                "message": err.get("msg"),
            }
            for err in exc.errors()
        ]
        audit.record("EVENT_SUBMIT", "REJECTED_VALIDATION", errors=len(detail))
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "validation_error", "detail": detail},
        )

    @app.post("/events")
    def submit_event(
        body: EventRequest,
        response: Response,
        db: GatewayDB = Depends(get_db),
        client: AccountClient = Depends(get_client),
    ):
        # 1. Idempotency: a previously-seen eventId returns the stored record.
        existing = db.get_event(body.eventId)
        if existing is not None:
            logger.info(
                "event.duplicate",
                extra={"event_id": body.eventId, "account_id": body.accountId},
            )
            audit.record(
                "EVENT_SUBMIT",
                "DUPLICATE",
                account_id=body.accountId,
                event_id=body.eventId,
            )
            response.status_code = status.HTTP_200_OK
            return existing.to_response(duplicate=True)

        amount_str = format(Decimal(body.amount).normalize(), "f")

        # 2. Apply the transaction downstream first. The Account Service is also
        #    idempotent, so retries/replays never double-count.
        tx_payload = {
            "eventId": body.eventId,
            "type": body.type,
            "amount": amount_str,
            "currency": body.currency,
            "eventTimestamp": body.eventTimestamp.isoformat(),
        }
        try:
            client.apply_transaction(body.accountId, tx_payload)
        except AccountServiceUnavailable as exc:
            logger.error(
                "event.account_unavailable",
                extra={"event_id": body.eventId, "error": str(exc)},
            )
            audit.record(
                "EVENT_SUBMIT",
                "DOWNSTREAM_UNAVAILABLE",
                account_id=body.accountId,
                event_id=body.eventId,
                circuit_state=client.circuit_state.value,
            )
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "error": "account_service_unavailable",
                    "message": "The Account Service is currently unavailable. "
                    "Please retry; this request was not recorded.",
                },
            )
        except AccountClientError as exc:
            audit.record(
                "EVENT_SUBMIT",
                "DOWNSTREAM_REJECTED",
                account_id=body.accountId,
                event_id=body.eventId,
                status=exc.status_code,
            )
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": "account_service_rejected", "detail": exc.detail},
            )

        # 3. Persist locally after a successful apply.
        stored = StoredEvent(
            event_id=body.eventId,
            account_id=body.accountId,
            type=body.type,
            amount=amount_str,
            currency=body.currency,
            event_timestamp=body.eventTimestamp.isoformat(),
            metadata_json=json.dumps(body.metadata) if body.metadata else None,
            received_at=_dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        )
        created = db.insert_event(stored)
        if not created:
            # Concurrent duplicate landed first; return it idempotently.
            existing = db.get_event(body.eventId)
            response.status_code = status.HTTP_200_OK
            return existing.to_response(duplicate=True)

        logger.info(
            "event.accepted",
            extra={
                "event_id": stored.event_id,
                "account_id": stored.account_id,
                "type": stored.type,
                "amount": stored.amount,
            },
        )
        audit.record(
            "EVENT_SUBMIT",
            "ACCEPTED",
            account_id=stored.account_id,
            event_id=stored.event_id,
            type=stored.type,
            amount=stored.amount,
            currency=stored.currency,
        )
        response.status_code = status.HTTP_201_CREATED
        return stored.to_response(duplicate=False)

    @app.get("/events/{event_id}", response_model=EventResponse)
    def get_event(event_id: str, db: GatewayDB = Depends(get_db)):
        event = db.get_event(event_id)
        if event is None:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "not_found", "message": f"No event '{event_id}'"},
            )
        return event.to_response()

    @app.get("/events")
    def list_events(
        account: str = Query(..., description="Account ID to list events for"),
        db: GatewayDB = Depends(get_db),
    ):
        events = db.list_events(account)
        return {
            "accountId": account,
            "count": len(events),
            "events": [e.to_response().model_dump() for e in events],
        }

    @app.get("/accounts/{account_id}/balance")
    def get_balance(account_id: str, client: AccountClient = Depends(get_client)):
        try:
            return client.get_balance(account_id)
        except AccountServiceUnavailable as exc:
            return _account_unavailable(exc)
        except AccountClientError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": "account_service_rejected", "detail": exc.detail},
            )

    @app.get("/accounts/{account_id}")
    def get_account(account_id: str, client: AccountClient = Depends(get_client)):
        try:
            return client.get_account(account_id)
        except AccountServiceUnavailable as exc:
            return _account_unavailable(exc)
        except AccountClientError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": "account_service_rejected", "detail": exc.detail},
            )

    @app.get("/health")
    def health(
        db: GatewayDB = Depends(get_db), client: AccountClient = Depends(get_client)
    ):
        db_ok = db.ping()
        payload = {
            "status": "ok" if db_ok else "degraded",
            "service": SERVICE_NAME,
            "checks": {
                "database": "ok" if db_ok else "down",
                "account_service_circuit": client.circuit_state.value,
            },
            "metrics": metrics.snapshot(),
        }
        code = status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(payload, status_code=code)

    @app.get("/metrics")
    def get_metrics():
        return PlainTextResponse(metrics.render())

    @app.get("/audit")
    def get_audit(
        account: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        db: GatewayDB = Depends(get_db),
    ):
        # Read-only view over the append-only audit trail (Gateway-local).
        return {"entries": db.list_audit(account, limit)}

    return app


def _account_unavailable(exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": "account_service_unavailable",
            "message": "The Account Service is currently unreachable.",
        },
    )


app = create_app()
