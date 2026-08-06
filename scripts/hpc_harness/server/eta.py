"""Throughput / ETA aggregation (spec §9), derived from core-DB completions only."""

import time
from collections import deque
from typing import Deque, Optional


class ThroughputTracker:
    """Rolling completions-per-minute and remaining-time estimate."""

    def __init__(self, window_s: float = 600.0) -> None:
        """Track completion timestamps within a rolling ``window_s`` window."""
        self.window_s = window_s
        self._completions: Deque[float] = deque()

    def record(self, ts: Optional[float] = None) -> None:
        """Register one finished job (done or dead — anything leaving the queue)."""
        self._completions.append(ts if ts is not None else time.time())
        self._trim()

    def _trim(self) -> None:
        cutoff = time.time() - self.window_s
        while self._completions and self._completions[0] < cutoff:
            self._completions.popleft()

    def throughput_per_min(self) -> float:
        """Completions per minute over the rolling window."""
        self._trim()
        if not self._completions:
            return 0.0
        span = max(time.time() - self._completions[0], 1.0)
        return len(self._completions) * 60.0 / span

    def eta_seconds(self, remaining: int) -> Optional[float]:
        """``remaining / throughput`` in seconds, or None when idle."""
        rate = self.throughput_per_min()
        if rate <= 0 or remaining <= 0:
            return None
        return remaining / (rate / 60.0)
