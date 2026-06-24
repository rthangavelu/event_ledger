"""A small, thread-safe circuit breaker.

States:

* ``CLOSED``    -- calls flow normally; consecutive failures are counted.
* ``OPEN``      -- the downstream is considered unhealthy; calls fail fast
                   without touching it until ``recovery_timeout`` elapses.
* ``HALF_OPEN`` -- a limited number of trial calls are allowed; enough
                   successes close the breaker again, any failure re-opens it.

This prevents the Gateway from hammering a failing Account Service and lets it
return a fast, meaningful error instead of piling up slow/blocked requests.
"""

from __future__ import annotations

import threading
import time
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the breaker is OPEN."""


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 10.0,
        half_open_max_calls: int = 1,
        success_threshold: int = 1,
        clock=time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold
        self._clock = clock

        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._half_open_calls = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def _transition_to(self, state: CircuitState) -> None:
        self._state = state
        self._failures = 0
        self._successes = 0
        self._half_open_calls = 0
        if state == CircuitState.OPEN:
            self._opened_at = self._clock()

    def allow_request(self) -> bool:
        """Check (and possibly advance) state before attempting a call."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._clock() - self._opened_at >= self.recovery_timeout:
                    # Cooldown elapsed: allow a trial request.
                    self._transition_to(CircuitState.HALF_OPEN)
                else:
                    return False
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    return False
                self._half_open_calls += 1
            return True

    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._successes += 1
                if self._successes >= self.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
            else:
                self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # A trial failed: trip immediately back to OPEN.
                self._transition_to(CircuitState.OPEN)
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._transition_to(CircuitState.OPEN)
