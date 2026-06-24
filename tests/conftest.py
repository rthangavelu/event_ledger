"""Shared pytest fixtures.

We run the Account Service in a real background uvicorn server on a random port
so the Gateway exercises genuine HTTP calls (real timeouts, real retries, real
trace-header propagation). This makes the integration / resiliency / tracing
tests trustworthy rather than over-mocked.
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import closing

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from account_service.db import AccountDB
from account_service.main import create_app as create_account_app
from gateway.account_client import AccountClient
from gateway.circuit_breaker import CircuitBreaker
from gateway.db import GatewayDB
from gateway.main import create_app as create_gateway_app


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BackgroundServer:
    """Run a FastAPI app in a uvicorn server on a background thread."""

    def __init__(self, app, port: int) -> None:
        self.port = port
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 5.0) -> None:
        self._thread.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.server.started:
                return
            time.sleep(0.02)
        raise RuntimeError("background server failed to start")

    def stop(self) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=5.0)


@pytest.fixture
def account_app():
    """A fresh in-memory Account Service app (for direct TestClient use)."""
    return create_account_app(AccountDB(":memory:"))


@pytest.fixture
def account_server():
    """A running Account Service over real HTTP with a fresh in-memory DB."""
    app = create_account_app(AccountDB(":memory:"))
    server = BackgroundServer(app, _free_port())
    server.start()
    try:
        yield server
    finally:
        server.stop()


def make_gateway_client(
    base_url: str, breaker: CircuitBreaker | None = None, **client_kwargs
) -> TestClient:
    """Build a Gateway TestClient wired to the Account Service at ``base_url``."""
    account_client = AccountClient(
        base_url=base_url,
        client=httpx.Client(base_url=base_url, timeout=client_kwargs.pop("timeout", 2.0)),
        breaker=breaker,
    )
    for key, value in client_kwargs.items():
        setattr(account_client, key, value)
    gateway_app = create_gateway_app(GatewayDB(":memory:"), account_client)
    return TestClient(gateway_app)


@pytest.fixture
def gateway_client(account_server):
    """Full stack: Gateway TestClient -> live Account Service."""
    client = make_gateway_client(account_server.base_url)
    yield client


def sample_event(**overrides):
    event = {
        "eventId": "evt-001",
        "accountId": "acct-123",
        "type": "CREDIT",
        "amount": 150.00,
        "currency": "USD",
        "eventTimestamp": "2026-05-15T14:02:11Z",
        "metadata": {"source": "mainframe-batch", "batchId": "B-9042"},
    }
    event.update(overrides)
    return event
