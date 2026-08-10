"""Unit tests for the HPC-harness ETA/throughput tracker."""

import time

import pytest

from hpc_harness.server.eta import ThroughputTracker

pytestmark = pytest.mark.hpcharness


# ---------------------------------------------------------------------------- eta


def test_throughput_and_eta():
    """The tracker reports a positive throughput and a finite ETA for remaining work."""
    tracker = ThroughputTracker(window_s=600)
    now = time.time()
    for i in range(10):
        tracker.record(now - 60 + i * 6)
    assert tracker.throughput_per_min() > 0
    assert tracker.eta_seconds(100) > 0
    assert tracker.eta_seconds(0) is None


def test_throughput_tracker_injected_clock_is_deterministic():
    """An injected clock keeps the window/throughput deterministic without monkeypatching.

    Records timestamps via ``ts`` injection and drives the rolling window purely
    through the injected ``clock`` callable, so ``_trim`` and
    ``throughput_per_min`` never touch the wall clock.
    """
    # A controllable clock advancing in fixed steps.
    current = [0.0]

    def clock() -> float:
        return current[0]

    tracker = ThroughputTracker(window_s=600, clock=clock)

    # Record 10 completions between t=0 and t=54 (one every 6s).
    for i in range(10):
        current[0] = i * 6.0
        tracker.record()

    # Advance the clock to the last completion; window spans 54s.
    current[0] = 54.0
    assert tracker.throughput_per_min() == 10 * 60.0 / 54.0

    # ETA scales with remaining work at the measured rate.
    rate_per_sec = (10 * 60.0 / 54.0) / 60.0
    assert tracker.eta_seconds(100) == 100 / rate_per_sec

    # Advance past the window: every recorded completion drops out, throughput -> 0.
    current[0] = 10 * 6.0 + 600.0 + 1.0
    assert tracker.throughput_per_min() == 0.0
    assert tracker.eta_seconds(100) is None
