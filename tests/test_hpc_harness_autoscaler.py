"""Unit tests for the HPC-harness autoscaler (compute_to_submit, sbatch, sinfo, log tail)."""

from typing import Any, Callable, Dict

import pytest

from hpc_harness.server.autoscaler import (
    compute_to_submit,
    default_sbatch,
    parse_sinfo_cpus,
    read_log_tail,
)

pytestmark = pytest.mark.hpcharness


def _capturing_runner(captured: Dict[str, Any], stdout: str) -> Callable[..., Any]:
    """Build a fake ``sbatch`` runner: it records the command and returns ``stdout`` as the job id.

    Injected into ``default_sbatch`` via its ``runner`` parameter so the test never has to
    monkeypatch ``subprocess.run`` on the autoscaler module.
    """

    class _Result:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def runner(cmd, **_kwargs):
        captured["cmd"] = cmd
        return _Result()

    return runner


# ------------------------------------------------------------------ autoscaler law


@pytest.mark.parametrize(
    "work,current,available,queued,expected",
    [
        # Initial burst: plenty of work and cores.
        (10_000, 0, 1000, 0, 1000),
        # THE v1 bug case: fleet already large, 200 cores free later -> use them.
        (10_000, 1000, 200, 0, 200),
        # Backlog smaller than free cores: never submit more than the gap.
        (50, 40, 1000, 0, 10),
        # Cluster full: keep standby_floor workers queued in Slurm.
        (10_000, 500, 0, 0, 10),
        # Standby already queued: do NOT resubmit (the v1 grace-timeout loop).
        (10_000, 500, 0, 10, 0),
        # Partially queued standby tops up only the difference.
        (10_000, 500, 0, 6, 4),
        # Work already covered: nothing to submit even with idle cores.
        (100, 100, 500, 0, 0),
        (100, 150, 500, 0, 0),
        # Tiny batch never over-queues below the floor.
        (3, 0, 0, 0, 3),
    ],
)
def test_autoscaler_control_law(work, current, available, queued, expected):
    """The incremental control law sizes the submission step across the key scenarios."""
    assert compute_to_submit(work, current, available, queued, 10, 2000) == expected


def test_autoscaler_respects_max_workers():
    """The step never pushes the fleet past max_workers."""
    assert compute_to_submit(10_000, 1990, 500, 0, 10, 2000) == 10
    assert compute_to_submit(10_000, 2000, 500, 0, 10, 2000) == 0


def test_parse_sinfo_cpus_sums_idle_field():
    """parse_sinfo_cpus sums the idle field of each valid A/I/O/T line, ignoring junk."""
    text = "4523/1234/89/5846\n0/56/0/56\nnot-a-line\n1/2/3\n"
    assert parse_sinfo_cpus(text) == 1234 + 56  # A/I/O/T format, idle is field 2


def test_default_sbatch_missing_script_raises_clear_error(tmp_path):
    """A missing or unset worker script raises a clear RuntimeError before any sbatch call."""
    with pytest.raises(RuntimeError, match="does not exist"):
        default_sbatch(str(tmp_path / "nope.sbatch"), 1)
    with pytest.raises(RuntimeError, match="not set"):
        default_sbatch("", 1)


def test_default_sbatch_routes_logs_into_log_dir(tmp_path):
    """With a log_dir, sbatch is invoked with --output/--error into it, and the dir is created."""
    script = tmp_path / "worker.sbatch"
    script.write_text("#!/bin/bash\n")
    log_dir = tmp_path / "logs"  # deliberately absent up front
    captured: Dict[str, Any] = {}

    job_ids = default_sbatch(str(script), 1, str(log_dir), runner=_capturing_runner(captured, "12345"))

    assert job_ids == ["12345"]
    assert log_dir.is_dir()  # created for Slurm to write into
    joined = " ".join(captured["cmd"])
    assert f"--output={log_dir / 'worker-%j.out'}" in joined
    assert f"--error={log_dir / 'worker-%j.err'}" in joined


def test_default_sbatch_exports_worker_config(tmp_path):
    """A worker_config is passed to the job as HARNESS_WORKER_CONFIG (with --export=ALL)."""
    script = tmp_path / "worker.sbatch"
    script.write_text("#!/bin/bash\n")
    worker_cfg = tmp_path / "worker.json"
    worker_cfg.write_text("{}")
    captured: Dict[str, Any] = {}

    runner = _capturing_runner(captured, "77")
    assert default_sbatch(str(script), 1, None, str(worker_cfg), runner=runner) == ["77"]
    assert f"--export=ALL,HARNESS_WORKER_CONFIG={worker_cfg}" in captured["cmd"]


def test_default_sbatch_missing_worker_config_raises(tmp_path):
    """A worker_config that does not exist fails fast with a clear error, before any sbatch."""
    script = tmp_path / "worker.sbatch"
    script.write_text("#!/bin/bash\n")
    with pytest.raises(RuntimeError, match="worker_config does not exist"):
        default_sbatch(str(script), 1, None, str(tmp_path / "absent.json"))


def test_default_sbatch_exports_worker_runner(tmp_path):
    """A worker_runner is passed to the job as HARNESS_WORKER_RUNNER (pinning the fleet's runner)."""
    script = tmp_path / "worker.sbatch"
    script.write_text("#!/bin/bash\n")
    captured: Dict[str, Any] = {}

    runner = _capturing_runner(captured, "9")
    assert default_sbatch(str(script), 1, None, None, "hisim_setup", runner=runner) == ["9"]
    assert "--export=ALL,HARNESS_WORKER_RUNNER=hisim_setup" in captured["cmd"]


def test_read_log_tail_returns_last_lines_or_none(tmp_path):
    """read_log_tail returns None for a missing file and the last N lines otherwise."""
    assert read_log_tail(str(tmp_path / "absent.out")) is None
    log = tmp_path / "worker.out"
    log.write_text("\n".join(f"line{i}" for i in range(100)) + "\n")
    tail = read_log_tail(str(log), max_lines=5)
    assert tail is not None
    assert tail.splitlines() == ["line95", "line96", "line97", "line98", "line99"]
