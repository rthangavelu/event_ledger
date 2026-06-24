"""Resilient HTTP client the Gateway uses to talk to the Account Service.

Resiliency patterns applied to every downstream call (layered together):

1. **Timeout** -- each attempt is bounded so a slow Account Service can't make
   Gateway requests hang.
2. **Retry with exponential backoff + jitter** -- transient failures (network
   errors, timeouts, 5xx) are retried a bounded number of times; jitter avoids
   thundering-herd retries.
3. **Circuit breaker** -- if the Account Service keeps failing, the breaker opens
   and the Gateway fails fast with a clear 503 instead of waiting on every call.

4xx responses are treated as *successful* downstream interactions (the service
is healthy, it just rejected the request) -- they are not retried and do not
trip the breaker.
"""

from __future__ import annotations

import logging
import os
import random
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from common import tracing

from .circuit_breaker import CircuitBreaker, CircuitState

logger = logging.getLogger("gateway.account_client")


class AccountServiceUnavailable(Exception):
    """Downstream is unreachable / failing / circuit open -> maps to HTTP 503."""


class AccountClientError(Exception):
    """Account Service returned a 4xx -> propagate the status to the caller."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(f"account service returned {status_code}")
        self.status_code = status_code
        self.detail = detail


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class AccountClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        breaker: Optional[CircuitBreaker] = None,
    ) -> None:
        self.base_url = (base_url or os.getenv(
            "ACCOUNT_SERVICE_URL", "http://localhost:8001"
        )).rstrip("/")
        self.timeout = _env_float("ACCOUNT_TIMEOUT_SECONDS", 2.0)
        self.max_retries = _env_int("ACCOUNT_MAX_RETRIES", 2)
        self.backoff_base = _env_float("ACCOUNT_BACKOFF_BASE", 0.1)
        self.backoff_max = _env_float("ACCOUNT_BACKOFF_MAX", 2.0)

        self._client = client or httpx.Client(base_url=self.base_url, timeout=self.timeout)
        self._breaker = breaker or CircuitBreaker(
            failure_threshold=_env_int("CB_FAILURE_THRESHOLD", 5),
            recovery_timeout=_env_float("CB_RECOVERY_TIMEOUT", 10.0),
            half_open_max_calls=_env_int("CB_HALF_OPEN_MAX_CALLS", 1),
            success_threshold=_env_int("CB_SUCCESS_THRESHOLD", 1),
        )

    @property
    def circuit_state(self) -> CircuitState:
        return self._breaker.state

    def _sleep_backoff(self, attempt: int) -> None:
        # Full jitter: sleep in [0, min(cap, base * 2**attempt)].
        ceiling = min(self.backoff_max, self.backoff_base * (2 ** attempt))
        time.sleep(random.uniform(0, ceiling))

    def _call(
        self, method: str, path: str, json: Optional[dict] = None
    ) -> httpx.Response:
        if not self._breaker.allow_request():
            logger.warning(
                "account_client.circuit_open",
                extra={"circuit_state": self._breaker.state.value, "path": path},
            )
            raise AccountServiceUnavailable("circuit breaker is open")

        headers = tracing.outbound_headers()
        last_error: str = "unknown error"

        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.request(
                    method, path, json=json, headers=headers, timeout=self.timeout
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "account_client.transport_error",
                    extra={"attempt": attempt, "path": path, "error": last_error},
                )
            else:
                if resp.status_code < 400:
                    self._breaker.record_success()
                    return resp
                if resp.status_code < 500:
                    # Client error: downstream is healthy, request was bad.
                    self._breaker.record_success()
                    raise AccountClientError(resp.status_code, _safe_json(resp))
                last_error = f"upstream status {resp.status_code}"
                logger.warning(
                    "account_client.server_error",
                    extra={"attempt": attempt, "path": path, "status": resp.status_code},
                )

            if attempt < self.max_retries:
                self._sleep_backoff(attempt)

        # All attempts exhausted -> count as a circuit failure and surface 503.
        self._breaker.record_failure()
        logger.error(
            "account_client.exhausted",
            extra={
                "path": path,
                "attempts": self.max_retries + 1,
                "error": last_error,
                "circuit_state": self._breaker.state.value,
            },
        )
        raise AccountServiceUnavailable(last_error)

    def apply_transaction(
        self, account_id: str, payload: dict
    ) -> Tuple[Dict[str, Any], int]:
        resp = self._call(
            "POST", f"/accounts/{account_id}/transactions", json=payload
        )
        return resp.json(), resp.status_code

    def get_balance(self, account_id: str) -> Dict[str, Any]:
        resp = self._call("GET", f"/accounts/{account_id}/balance")
        return resp.json()

    def get_account(self, account_id: str) -> Dict[str, Any]:
        resp = self._call("GET", f"/accounts/{account_id}")
        return resp.json()

    def ping(self) -> bool:
        try:
            resp = self._client.get("/health", timeout=self.timeout)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        self._client.close()


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"detail": resp.text}
