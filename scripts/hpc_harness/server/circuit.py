"""Failure-storm circuit breaker (spec §8.1).

Watches a rolling window of completed attempts and trips (auto-pausing leasing) when a
systematic failure would otherwise burn through the whole batch's retries unattended.
"""

from collections import Counter, deque
from typing import Deque, Optional

from hpc_harness.config import CircuitBreakerConfig


class CircuitBreaker:
    """Rolling failure-rate / consecutive-failure trip logic."""

    def __init__(self, cfg: CircuitBreakerConfig) -> None:
        """Initialize from the server's circuit-breaker settings."""
        self.cfg = cfg
        self._window: Deque[bool] = deque(maxlen=max(cfg.window, 1))
        self._errors: Deque[str] = deque(maxlen=50)
        self._consecutive = 0
        self.tripped: Optional[str] = None

    def record(self, ok: bool, error: Optional[str] = None) -> bool:
        """Register one attempt outcome; returns True when this outcome trips the breaker."""
        if not self.cfg.enabled:
            return False
        self._window.append(ok)
        if ok:
            self._consecutive = 0
        else:
            self._consecutive += 1
            if error:
                self._errors.append(error.strip().splitlines()[-1][:200])
        if self.tripped:
            return False
        if self._consecutive >= self.cfg.consecutive:
            self.tripped = f"{self._consecutive} consecutive failures"
            return True
        if len(self._window) >= self.cfg.min_samples:
            failures = sum(1 for outcome in self._window if not outcome)
            rate = failures / len(self._window)
            if rate >= self.cfg.failure_rate:
                self.tripped = f"failure rate {rate:.0%} over last {len(self._window)} attempts"
                return True
        return False

    def top_error(self) -> Optional[str]:
        """Most common recent failure message (dashboard banner)."""
        if not self._errors:
            return None
        return Counter(self._errors).most_common(1)[0][0]

    def reset(self) -> None:
        """Clear the trip (admin resume)."""
        self.tripped = None
        self._consecutive = 0
        self._window.clear()
