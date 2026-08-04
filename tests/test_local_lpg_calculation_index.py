"""Tests for the local-LPG calculation-index allocation.

pylpg's ``LPGExecutor`` derives its working directory purely from the calculation
index — ``<site-packages>/pylpg/C<index>`` — a path shared by every process on the
machine. Two runs picking the same index share one working directory and one SQLite
results database, which surfaces as "database is locked". HiSim then catches that
failure and downgrades ``USE_LOCAL_LPG`` to ``USE_PREDEFINED_PROFILE``, so the run
completes on a *different household load profile* instead of failing — silently wrong
numbers. Allocating a process-private index block is what keeps runs off each other.

These are pure allocation tests: no LPG binary is invoked.
"""

import os

import pytest

from hisim.components.loadprofilegenerator_utsp_connector import (
    LOCAL_LPG_INDICES_PER_PROCESS,
    STRICT_LPG_ENV_VAR,
    default_local_lpg_calculation_index,
    resolve_local_lpg_calculation_index,
    strict_lpg_enabled,
)

pytestmark = pytest.mark.base

ENV_VAR = "HISIM_LOCAL_LPG_CALC_INDEX"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run each test against a known-clean environment."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.delenv(STRICT_LPG_ENV_VAR, raising=False)


def test_default_index_is_process_private() -> None:
    """The default index is derived from this process's PID, not a shared constant."""
    assert default_local_lpg_calculation_index() == os.getpid() * LOCAL_LPG_INDICES_PER_PROCESS


def test_default_index_is_not_the_old_shared_constant() -> None:
    """Regression: the default used to be a hard-coded 1 that every run collided on."""
    assert default_local_lpg_calculation_index() != 1
    assert resolve_local_lpg_calculation_index(None) != 1


def test_distinct_pids_get_non_overlapping_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neighbouring PIDs get blocks far enough apart for the multi-household walk.

    The multi-household path uses ``base, base+1, ... base+n``, so adjacent PIDs must be
    separated by more than the number of households a run can plausibly have.
    """
    monkeypatch.setattr(os, "getpid", lambda: 1000)
    first = default_local_lpg_calculation_index()
    monkeypatch.setattr(os, "getpid", lambda: 1001)
    second = default_local_lpg_calculation_index()
    assert second - first == LOCAL_LPG_INDICES_PER_PROCESS
    assert LOCAL_LPG_INDICES_PER_PROCESS > 1


def test_explicit_config_value_wins() -> None:
    """A configured index is honoured verbatim, so deliberate partitioning still works."""
    assert resolve_local_lpg_calculation_index(7) == 7


def test_env_override_wins_over_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Batch tooling sets the env var per worker; it must beat the process default."""
    monkeypatch.setenv(ENV_VAR, "42")
    assert resolve_local_lpg_calculation_index(None) == 42


def test_config_value_wins_over_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit config value is the most specific signal and outranks the env var."""
    monkeypatch.setenv(ENV_VAR, "42")
    assert resolve_local_lpg_calculation_index(7) == 7


def test_empty_env_override_falls_through_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty env var must not become ``int("")``; it falls through to the default."""
    monkeypatch.setenv(ENV_VAR, "")
    assert resolve_local_lpg_calculation_index(None) == default_local_lpg_calculation_index()


# --------------------------------------------------------------------------- #
# strict mode: refuse the silent downgrade to USE_PREDEFINED_PROFILE
# --------------------------------------------------------------------------- #
def test_strict_mode_is_off_by_default() -> None:
    """Interactive runs keep the forgiving fallback; only opt-in makes failures fatal."""
    assert strict_lpg_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "anything"])
def test_strict_mode_enabled_for_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Any non-falsey value turns strict mode on, so a typo errs toward failing loudly."""
    monkeypatch.setenv(STRICT_LPG_ENV_VAR, value)
    assert strict_lpg_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "FALSE", "no", "off", "  "])
def test_strict_mode_disabled_for_falsey_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Explicit off-switches and whitespace are honoured as 'not strict'."""
    monkeypatch.setenv(STRICT_LPG_ENV_VAR, value)
    assert strict_lpg_enabled() is False


def test_golden_runner_turns_strict_mode_on() -> None:
    """The golden runner opts in, so the gate can never compare silently substituted inputs.

    Asserted against the source rather than by running a simulation: the point is that the
    opt-in exists at the one entry point every golden check and bless goes through.
    """
    from pathlib import Path

    runner_source = (Path(__file__).resolve().parent.parent / "scripts" / "runner.py").read_text(
        encoding="utf-8"
    )
    assert STRICT_LPG_ENV_VAR in runner_source
