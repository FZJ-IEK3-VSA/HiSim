"""Worker warm-pool integration tests (spec §15) — POSIX only (fork).

Skipped on Windows; run these under WSL / Linux CI. Uses a trivial in-process runner
(registered before the spawner forks, so children inherit it) — no HiSim import.
"""

import os
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.harness,
    pytest.mark.skipif(os.name != "posix", reason="warm pool needs POSIX fork"),
]


class EchoRunner:
    """Trivial runner: writes a marker file, or crashes/sleeps on request."""

    name = "echo-test"

    def warmup(self) -> None:
        """Nothing heavy."""

    def on_fork(self) -> None:
        """Nothing to re-init."""

    def run(self, payload: dict, result_dir: str) -> None:
        """Write ok.txt, raise, or sleep, depending on the payload."""
        if payload.get("sleep"):
            time.sleep(payload["sleep"])
        if payload.get("crash"):
            raise RuntimeError("intentional crash")
        if payload.get("hard_exit"):
            os._exit(9)  # pylint: disable=protected-access
        Path(result_dir, "ok.txt").write_text(payload.get("text", "ok"), encoding="utf-8")


@pytest.fixture(name="pool")
def pool_fixture(tmp_path):
    """A started 2-slot warm pool over the EchoRunner, torn down after the test."""
    from hpc_harness.runners.base import register_runner
    from hpc_harness.worker.spawner import Spawner
    from hpc_harness.worker.warm_pool import WarmPool

    register_runner(EchoRunner())
    spawner = Spawner("echo-test")
    pool = WarmPool(spawner, target_slots=2, timeout_s=5.0, max_jobs_per_child=2)
    pool.ensure()
    yield pool, tmp_path
    pool.shutdown(kill_running=True)
    spawner.shutdown()


def _job(tmp_path, job_id, attempt=1, **payload):
    """Build a lease-shaped job dict with staging/result dirs under ``tmp_path``."""
    staging = tmp_path / ".staging" / f"{job_id:06d}_t.attempt-{attempt}"
    return {"id": job_id, "attempt": attempt, "payload": payload,
            "staging_dir": str(staging), "result_dir": str(tmp_path / f"{job_id:06d}_t"),
            "success_file": "ok.txt"}


def _wait_results(pool, n, timeout=15.0):
    """Poll the pool until at least ``n`` results arrive (or fail on timeout)."""
    results: list = []
    deadline = time.time() + timeout
    while len(results) < n and time.time() < deadline:
        pool.sample()
        results.extend(pool.poll())
        time.sleep(0.05)
    assert len(results) >= n, f"only {len(results)}/{n} results before timeout"
    return results


def test_dispatch_success_and_console_capture(pool):
    """A dispatched job runs in a warm child, finishes, and writes its success file."""
    warm_pool, tmp_path = pool
    job = _job(tmp_path, 1, text="hello")
    assert warm_pool.dispatch(job)
    assert warm_pool.running() == [{"job_id": 1, "attempt": 1}]
    result = _wait_results(warm_pool, 1)[0]
    assert result["ok"] and result["exit_kind"] == "finished"
    assert (Path(job["staging_dir"]) / "ok.txt").read_text(encoding="utf-8") == "hello"


def test_crash_is_reported_with_traceback_and_child_survives_pool(pool):
    """A job crash is reported with its traceback while the pool stays intact."""
    warm_pool, tmp_path = pool
    warm_pool.dispatch(_job(tmp_path, 2, crash=True))
    result = _wait_results(warm_pool, 1)[0]
    assert not result["ok"] and "intentional crash" in result["error"]
    assert "RuntimeError" in (result["traceback"] or "")
    assert warm_pool.idle_count() == 2  # pool is intact


def test_hard_child_death_detected_and_replaced(pool):
    """A hard child exit is detected as 'died' and the child is replaced."""
    warm_pool, tmp_path = pool
    warm_pool.dispatch(_job(tmp_path, 3, hard_exit=True))
    result = _wait_results(warm_pool, 1)[0]
    assert not result["ok"] and result["exit_kind"] == "died"
    assert warm_pool.idle_count() == 2  # dead child replaced via the spawner


def test_timeout_kills_and_replaces(pool):
    """A job exceeding the timeout is killed, reported as 'timeout', and its slot replaced."""
    warm_pool, tmp_path = pool
    warm_pool.timeout_s = 0.5
    warm_pool.dispatch(_job(tmp_path, 4, sleep=30))
    result = _wait_results(warm_pool, 1, timeout=20.0)[0]
    assert not result["ok"] and result["error"] == "timeout"
    assert warm_pool.idle_count() == 2


def test_child_recycled_after_max_jobs(pool):
    """A child is recycled once it has run its max jobs, keeping the pool at target size."""
    warm_pool, tmp_path = pool
    pids_before = {c.pid for c in warm_pool.children}
    for job_id in (5, 6, 7, 8):  # max_jobs_per_child=2, two children
        warm_pool.dispatch(_job(tmp_path, job_id))
        _wait_results(warm_pool, 1)
    pids_after = {c.pid for c in warm_pool.children}
    assert pids_before != pids_after  # at least one child was recycled
    assert warm_pool.idle_count() == 2


def test_kill_job_directive(pool):
    """A kill_job directive stops the running job and frees its slot."""
    warm_pool, tmp_path = pool
    warm_pool.dispatch(_job(tmp_path, 9, sleep=30))
    assert warm_pool.kill_job(9, 1)
    assert warm_pool.running() == []
    assert warm_pool.idle_count() == 2
