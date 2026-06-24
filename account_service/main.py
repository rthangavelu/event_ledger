"""Account Service -- internal service that owns account state.

Responsibilities: apply transactions to accounts (idempotently), and answer
balance / account-detail queries. It is only ever called by the Gateway.
"""

from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal

from fastapi import Depends, FastAPI, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse

from common.audit import AuditTrail
from common.errors import install_error_handlers
from common.logging_config import configure_logging
from common.metrics import MetricsRegistry
from common.middleware import ObservabilityMiddleware

from .db import AccountDB
from .models import (
    AccountDetailResponse,
    BalanceResponse,
    StoredTransaction,
    TransactionRequest,
    TransactionResponse,
)

SERVICE_NAME = "account-service"
logger = logging.getLogger(SERVICE_NAME)


def create_app(db: AccountDB | None = None) -> FastAPI:
    configure_logging(SERVICE_NAME)
    metrics = MetricsRegistry()
    app = FastAPI(title="Account Service", version="1.0.0")
    app.state.db = db or AccountDB()
    app.state.metrics = metrics
    app.state.audit = AuditTrail(SERVICE_NAME, app.state.db.append_audit)
    app.add_middleware(ObservabilityMiddleware, metrics=metrics)
    install_error_handlers(app, SERVICE_NAME)

    def get_db() -> AccountDB:
        return app.state.db

    audit = app.state.audit

    @app.post("/accounts/{account_id}/transactions")
    def apply_transaction(
        account_id: str,
        body: TransactionRequest,
        response: Response,
        db: AccountDB = Depends(get_db),
    ):
        existing = db.get_transaction(body.eventId)
        if existing is not None:
            # Idempotent replay: return the originally stored transaction.
            logger.info(
                "transaction.duplicate",
                extra={"event_id": body.eventId, "account_id": account_id},
            )
            audit.record(
                "TRANSACTION_APPLY",
                "DUPLICATE",
                account_id=account_id,
                event_id=body.eventId,
            )
            response.status_code = status.HTTP_200_OK
            return existing.to_response(duplicate=True)

        amount_str = format(Decimal(body.amount).normalize(), "f")
        tx = StoredTransaction(
            event_id=body.eventId,
            account_id=account_id,
            type=body.type,
            amount=amount_str,
            currency=body.currency,
            event_timestamp=body.eventTimestamp.isoformat(),
            created_at=_dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        )
        created = db.insert_transaction(tx)
        if not created:
            # Lost an insert race: fetch and return the winner idempotently.
            existing = db.get_transaction(body.eventId)
            audit.record(
                "TRANSACTION_APPLY",
                "DUPLICATE",
                account_id=account_id,
                event_id=body.eventId,
            )
            response.status_code = status.HTTP_200_OK
            return existing.to_response(duplicate=True)

        logger.info(
            "transaction.applied",
            extra={
                "event_id": tx.event_id,
                "account_id": account_id,
                "type": tx.type,
                "amount": tx.amount,
            },
        )
        audit.record(
            "TRANSACTION_APPLY",
            "APPLIED",
            account_id=account_id,
            event_id=tx.event_id,
            type=tx.type,
            amount=tx.amount,
            currency=tx.currency,
        )
        response.status_code = status.HTTP_201_CREATED
        return tx.to_response(duplicate=False)

    @app.get("/accounts/{account_id}/balance", response_model=BalanceResponse)
    def get_balance(account_id: str, db: AccountDB = Depends(get_db)):
        txs = db.list_transactions(account_id)
        balance = db.balance(account_id)
        currency = txs[0].currency if txs else None
        return BalanceResponse(
            accountId=account_id,
            balance=format(balance, "f"),
            currency=currency,
            transactionCount=len(txs),
        )

    @app.get("/accounts/{account_id}", response_model=AccountDetailResponse)
    def get_account(account_id: str, db: AccountDB = Depends(get_db)):
        txs = db.list_transactions(account_id)
        balance = db.balance(account_id)
        currency = txs[0].currency if txs else None
        recent = [t.to_response() for t in txs[-10:]][::-1]
        return AccountDetailResponse(
            accountId=account_id,
            balance=format(balance, "f"),
            currency=currency,
            transactionCount=len(txs),
            recentTransactions=recent,
        )

    @app.get("/health")
    def health(db: AccountDB = Depends(get_db)):
        db_ok = db.ping()
        payload = {
            "status": "ok" if db_ok else "degraded",
            "service": SERVICE_NAME,
            "checks": {"database": "ok" if db_ok else "down"},
        }
        code = status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(payload, status_code=code)

    @app.get("/metrics")
    def get_metrics():
        return PlainTextResponse(metrics.render())

    @app.get("/audit")
    def get_audit(
        account: str | None = None,
        limit: int = 100,
        db: AccountDB = Depends(get_db),
    ):
        return {"entries": db.list_audit(account, limit)}

    return app


app = create_app()
