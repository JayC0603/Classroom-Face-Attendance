"""Bounded latency statistics; never stores images, embeddings or identities."""

from collections import deque
from math import ceil, isfinite
from statistics import mean, median

from fr_contracts.runtime_status import StageLatencySummary


class BoundedLatency:
    def __init__(self, capacity: int = 256) -> None:
        if not 1 <= capacity <= 256:
            raise ValueError("latency capacity must be between 1 and 256")
        self._samples: deque[float] = deque(maxlen=capacity)
        self._attempts = 0
        self._failures = 0

    def add(self, duration_ms: float, *, failed: bool = False) -> None:
        if not isfinite(duration_ms) or duration_ms < 0:
            raise ValueError("latency must be finite and nonnegative")
        self._samples.append(duration_ms)
        self._attempts += 1
        self._failures += int(failed)

    def summary(self) -> StageLatencySummary:
        samples = sorted(self._samples)
        return StageLatencySummary(
            sample_count=len(samples),
            attempt_count=self._attempts,
            failure_count=self._failures,
            mean_ms=mean(samples) if samples else None,
            median_ms=median(samples) if samples else None,
            p95_ms=samples[ceil(len(samples) * 0.95) - 1] if samples else None,
        )
