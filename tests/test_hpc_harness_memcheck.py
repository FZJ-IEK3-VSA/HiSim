"""Unit tests for the HPC-harness memory budget (MemBudget)."""

import pytest

from hpc_harness.config import ServerConfig
from hpc_harness.server.memcheck import MemBudget

pytestmark = pytest.mark.hpcharness


# ------------------------------------------------------------------ memory budget


def _mem_cfg(**kwargs):
    cfg = ServerConfig(db_path="x", result_root="y", per_job_mem_gb=10.0,
                       mem_min_samples=3, mem_autoraise_margin_gb=1.0)
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


def test_membudget_autoraise_only_after_min_samples():
    """The budget auto-raises to the observed p99 only once min_samples are collected."""
    budget = MemBudget(_mem_cfg())
    assert not budget.observe(12 * 1024)
    assert not budget.observe(12 * 1024)
    assert budget.effective == 10.0
    assert budget.observe(12 * 1024)  # third sample: p99=12 -> raise to 13
    assert budget.effective == pytest.approx(13.0)
    assert budget.warning()["kind"] == "auto_raised"


def test_membudget_never_lowers_automatically_and_warns_when_too_high():
    """The budget never auto-lowers, but warns when the configured value is far too high."""
    budget = MemBudget(_mem_cfg())
    for _ in range(5):
        budget.observe(2 * 1024)  # jobs use 2 GB against a 10 GB budget
    assert budget.effective == 10.0
    warning = budget.warning()
    assert warning["kind"] == "too_high"


def test_membudget_manual_set_lowers():
    """A manual set lowers the effective budget and is persisted."""
    persisted: list = []
    budget = MemBudget(_mem_cfg(), persist_fn=persisted.append)
    budget.set_manual(4.0)
    assert budget.effective == 4.0
    assert persisted == [4.0]


def test_membudget_autoraise_disabled():
    """With auto-raise disabled the budget stays put regardless of observed peaks."""
    budget = MemBudget(_mem_cfg(mem_autoraise=False))
    for _ in range(5):
        budget.observe(20 * 1024)
    assert budget.effective == 10.0
