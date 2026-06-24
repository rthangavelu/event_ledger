"""A tiny, dependency-free metrics registry with Prometheus text exposition.

Supports counters and histograms. It is intentionally minimal -- just enough to
satisfy the "at least one custom metric" requirement and the Prometheus-endpoint
bonus -- while remaining thread-safe for FastAPI's threadpool execution model.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterable, List, Tuple

# Label sets are stored as a sorted tuple of (key, value) pairs so they are
# hashable and order-independent.
LabelKey = Tuple[Tuple[str, str], ...]

_DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def _labels_key(labels: Dict[str, str]) -> LabelKey:
    return tuple(sorted(labels.items()))


def _render_labels(key: LabelKey, extra: Tuple[Tuple[str, str], ...] = ()) -> str:
    items = list(key) + list(extra)
    if not items:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in items)
    return "{" + inner + "}"


class Counter:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self._values: Dict[LabelKey, float] = {}
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = _labels_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def snapshot(self) -> Dict[LabelKey, float]:
        with self._lock:
            return dict(self._values)

    def expose(self) -> List[str]:
        lines = [f"# HELP {self.name} {self.description}", f"# TYPE {self.name} counter"]
        for key, value in self.snapshot().items():
            lines.append(f"{self.name}{_render_labels(key)} {value}")
        return lines


class Histogram:
    def __init__(
        self, name: str, description: str, buckets: Iterable[float] = _DEFAULT_BUCKETS
    ) -> None:
        self.name = name
        self.description = description
        self.buckets = tuple(sorted(buckets))
        self._counts: Dict[LabelKey, List[int]] = {}
        self._sums: Dict[LabelKey, float] = {}
        self._totals: Dict[LabelKey, int] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        key = _labels_key(labels)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * len(self.buckets))
            for i, upper in enumerate(self.buckets):
                if value <= upper:
                    counts[i] += 1
            self._sums[key] = self._sums.get(key, 0.0) + value
            self._totals[key] = self._totals.get(key, 0) + 1

    @contextmanager
    def time(self, **labels: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.observe(time.perf_counter() - start, **labels)

    def expose(self) -> List[str]:
        lines = [f"# HELP {self.name} {self.description}", f"# TYPE {self.name} histogram"]
        with self._lock:
            for key in self._counts:
                cumulative = 0
                counts = self._counts[key]
                for i, upper in enumerate(self.buckets):
                    cumulative += counts[i]
                    le = ("+Inf" if upper == float("inf") else str(upper))
                    lines.append(
                        f"{self.name}_bucket"
                        f"{_render_labels(key, (('le', le),))} {cumulative}"
                    )
                lines.append(
                    f"{self.name}_bucket"
                    f"{_render_labels(key, (('le', '+Inf'),))} {self._totals[key]}"
                )
                lines.append(f"{self.name}_sum{_render_labels(key)} {self._sums[key]}")
                lines.append(f"{self.name}_count{_render_labels(key)} {self._totals[key]}")
        return lines


class MetricsRegistry:
    def __init__(self) -> None:
        self.requests_total = Counter(
            "http_requests_total", "Total HTTP requests by method, path and status."
        )
        self.errors_total = Counter(
            "http_errors_total", "Total HTTP responses with status >= 500."
        )
        self.request_latency = Histogram(
            "http_request_duration_seconds", "HTTP request latency in seconds."
        )

    def render(self) -> str:
        lines: List[str] = []
        lines.extend(self.requests_total.expose())
        lines.extend(self.errors_total.expose())
        lines.extend(self.request_latency.expose())
        return "\n".join(lines) + "\n"

    def snapshot(self) -> Dict[str, object]:
        """Compact JSON-serialisable view, handy for health diagnostics."""
        return {
            "http_requests_total": sum(self.requests_total.snapshot().values()),
            "http_errors_total": sum(self.errors_total.snapshot().values()),
        }
