"""Unit tests for the circuit breaker state machine (no I/O)."""

import pytest

from gateway.circuit_breaker import CircuitBreaker, CircuitState


def test_starts_closed():
    cb = CircuitBreaker()
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True


def test_opens_after_threshold_failures():
    cb = CircuitBreaker(failure_threshold=3)
    for _ in range(2):
        cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_success_resets_failure_count_when_closed():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()  # streak broken
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED


def test_open_blocks_requests_until_timeout():
    now = {"t": 0.0}
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=5.0, clock=lambda: now["t"])
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False
    now["t"] = 4.9
    assert cb.allow_request() is False
    now["t"] = 5.0
    assert cb.allow_request() is True
    assert cb.state == CircuitState.HALF_OPEN


def test_half_open_success_closes_circuit():
    now = {"t": 0.0}
    cb = CircuitBreaker(
        failure_threshold=1, recovery_timeout=1.0, success_threshold=2,
        half_open_max_calls=2, clock=lambda: now["t"],
    )
    cb.record_failure()
    now["t"] = 2.0
    assert cb.allow_request() is True  # trial 1
    cb.record_success()
    assert cb.state == CircuitState.HALF_OPEN  # needs 2 successes
    assert cb.allow_request() is True  # trial 2
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_half_open_failure_reopens():
    now = {"t": 0.0}
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=1.0, clock=lambda: now["t"])
    cb.record_failure()
    now["t"] = 2.0
    assert cb.allow_request() is True
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_half_open_limits_concurrent_trials():
    now = {"t": 0.0}
    cb = CircuitBreaker(
        failure_threshold=1, recovery_timeout=1.0, half_open_max_calls=1,
        clock=lambda: now["t"],
    )
    cb.record_failure()
    now["t"] = 2.0
    assert cb.allow_request() is True   # consumes the single trial slot
    assert cb.allow_request() is False  # no more trials until resolved
