"""Unit tests for the HPC-harness worker idle-timeout decision and config."""

import pytest

from hpc_harness.config import WorkerConfig

pytestmark = pytest.mark.hpcharness


# ------------------------------------------------------------- worker idle timeout


def test_worker_idle_timeout_decision():
    """The worker releases its allocation only after idle_timeout_s with nothing running/draining."""
    from hpc_harness.worker.worker import Worker

    expired = Worker._idle_timed_out  # pylint: disable=protected-access
    assert expired(1000.0, 100.0, 300.0, False, False) is True    # idle 900s > 300s
    assert expired(1000.0, 900.0, 300.0, False, False) is False   # idle 100s < 300s
    assert expired(1000.0, 100.0, 300.0, True, False) is False    # a job is running
    assert expired(1000.0, 100.0, 300.0, False, True) is False    # already draining
    assert expired(1000.0, 0.0, 0.0, False, False) is False       # disabled (idle_timeout_s=0)


def test_worker_config_idle_timeout_default_five_minutes(tmp_path):
    """idle_timeout_s defaults to 300 s (5 minutes) and is overridable from JSON."""
    cfg = WorkerConfig(server_url="http://x", result_root=str(tmp_path)).finalize()
    assert cfg.idle_timeout_s == 300.0
    cfg2 = WorkerConfig.from_dict(
        {"server_url": "http://x", "result_root": str(tmp_path), "idle_timeout_s": 120}
    ).finalize()
    assert cfg2.idle_timeout_s == 120
