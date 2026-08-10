"""Unit tests for the HPC-harness circuit breaker."""

import pytest

from hpc_harness.config import CircuitBreakerConfig
from hpc_harness.server.circuit import CircuitBreaker

pytestmark = pytest.mark.hpcharness


# --------------------------------------------------------------- circuit breaker


def _cb(**kwargs):
    return CircuitBreaker(CircuitBreakerConfig(**{"window": 10, "min_samples": 4,
                                                  "failure_rate": 0.5, "consecutive": 3,
                                                  **kwargs}))


def test_circuit_trips_on_consecutive_failures():
    """The breaker trips after enough consecutive failures and reports the top error."""
    breaker = _cb()
    assert not breaker.record(False, "err a")
    assert not breaker.record(False, "err a")
    assert breaker.record(False, "err a")
    assert "consecutive" in breaker.tripped
    assert breaker.top_error() == "err a"


def test_circuit_trips_on_failure_rate_after_min_samples():
    """The breaker trips on failure rate only once min_samples have accumulated."""
    breaker = _cb(consecutive=100)
    breaker.record(False)
    breaker.record(True)
    assert not breaker.tripped  # only 2 samples < min_samples
    breaker.record(False)
    tripped_now = breaker.record(False)
    assert tripped_now and "rate" in breaker.tripped


def test_circuit_success_resets_consecutive_and_reset_clears():
    """A success resets the consecutive counter, and reset() clears a tripped breaker."""
    breaker = _cb(failure_rate=1.1)  # rate trip disabled: isolate the consecutive logic
    breaker.record(False)
    breaker.record(False)
    breaker.record(True)
    breaker.record(False)
    breaker.record(False)
    assert not breaker.tripped
    breaker.record(False)
    assert breaker.tripped
    breaker.reset()
    assert breaker.tripped is None


def test_circuit_disabled_never_trips():
    """A disabled breaker records failures but never trips."""
    breaker = _cb(enabled=False, consecutive=1)
    assert not breaker.record(False)
    assert breaker.tripped is None
