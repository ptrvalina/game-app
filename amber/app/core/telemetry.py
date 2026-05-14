"""In-memory lightweight operational telemetry for Amber."""
from __future__ import annotations

from collections import Counter
from threading import Lock


class TelemetryStore:
    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._latency: Counter[str] = Counter()
        self._malformed: Counter[str] = Counter()
        self._lock = Lock()

    def incr(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._counts[key] += amount

    def observe_latency_ms(self, namespace: str, latency_ms: int) -> None:
        bucket = "gt_20000"
        if latency_ms < 1000:
            bucket = "lt_1000"
        elif latency_ms < 3000:
            bucket = "lt_3000"
        elif latency_ms < 10000:
            bucket = "lt_10000"
        elif latency_ms < 20000:
            bucket = "lt_20000"
        with self._lock:
            self._latency[f"{namespace}:{bucket}"] += 1

    def observe_malformed_ratio(self, ratio: float) -> None:
        bucket = "gt_35pct"
        if ratio < 0.05:
            bucket = "lt_5pct"
        elif ratio < 0.20:
            bucket = "lt_20pct"
        elif ratio < 0.35:
            bucket = "lt_35pct"
        with self._lock:
            self._malformed[bucket] += 1

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                "counts": dict(self._counts),
                "latency_buckets": dict(self._latency),
                "malformed_csv_ratio_buckets": dict(self._malformed),
            }
