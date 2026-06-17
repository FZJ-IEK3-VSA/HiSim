"""Tests for the HiSim MPI HPC harness (hisim.hpc_harness).

These cover the parts that carry the logic and are easy to get wrong: the SQLite
task state machine, config loading/overrides, and the node-local subprocess pool
(launch, reap, retry-relevant status, timeout kill, peak-memory sampling).

They do not require mpi4py or a real HiSim run: the pool is driven with a custom
command builder that launches trivial python subprocesses.
"""

import sys
import time

import pytest

from hisim.hpc_harness import db
from hisim.hpc_harness.config import HarnessConfig
from hisim.hpc_harness.pool import LocalPool, compute_max_slots


# --------------------------------------------------------------------------- db
@pytest.mark.base
def test_import_is_idempotent(tmp_path):
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    for i in range(3):
        (scen_dir / f"case_{i}.scenario.json").write_text("{}", encoding="utf-8")

    conn = db.connect(str(tmp_path / "tasks.db"))
    first = db.import_scenarios(conn, str(scen_dir), "*.json")
    second = db.import_scenarios(conn, str(scen_dir), "*.json")

    assert first == {"found": 3, "inserted": 3}
    assert second == {"found": 3, "inserted": 0}
    assert db.counts(conn)["total"] == 3
    conn.close()


@pytest.mark.base
def test_lease_report_retry_and_dead(tmp_path):
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    (scen_dir / "a.json").write_text("{}", encoding="utf-8")
    (scen_dir / "b.json").write_text("{}", encoding="utf-8")
    conn = db.connect(str(tmp_path / "tasks.db"))
    db.import_scenarios(conn, str(scen_dir), "*.json")

    leased = db.lease_tasks(conn, 5, "rank1@node1")
    conn.commit()
    assert len(leased) == 2
    assert db.counts(conn).get(db.LEASED) == 2
    assert not db.is_drained(conn)

    # First task succeeds.
    done_id = leased[0]["id"]
    db.record_report(conn, {"id": done_id, "status": db.DONE, "exit_code": 0,
                            "duration_s": 1.0, "peak_mem_mb": 100.0,
                            "result_dir": "/tmp/x"}, max_attempts=2)
    conn.commit()
    assert db.counts(conn).get(db.DONE) == 1

    # Second task fails once -> back to pending (attempts 1 < 2).
    fail_id = leased[1]["id"]
    db.record_report(conn, {"id": fail_id, "status": db.FAILED, "exit_code": 1,
                            "error": "boom"}, max_attempts=2)
    conn.commit()
    assert db.counts(conn).get(db.PENDING) == 1

    # Re-lease and fail again -> attempts 2 >= 2 -> dead.
    re_leased = db.lease_tasks(conn, 5, "rank1@node1")
    conn.commit()
    assert re_leased[0]["id"] == fail_id
    db.record_report(conn, {"id": fail_id, "status": db.FAILED, "exit_code": 1,
                            "error": "boom again"}, max_attempts=2)
    conn.commit()
    assert db.counts(conn).get(db.DEAD) == 1
    assert db.is_drained(conn)

    # The attempts table recorded every try (1 done + 2 failed = 3).
    n_attempts = conn.execute("SELECT COUNT(*) AS c FROM attempts").fetchone()["c"]
    assert n_attempts == 3
    conn.close()


@pytest.mark.base
def test_startup_recovery_requeues_leases(tmp_path):
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    (scen_dir / "a.json").write_text("{}", encoding="utf-8")
    conn = db.connect(str(tmp_path / "tasks.db"))
    db.import_scenarios(conn, str(scen_dir), "*.json")
    db.lease_tasks(conn, 1, "rank1@node1")
    conn.commit()
    assert db.counts(conn).get(db.LEASED) == 1

    recovered = db.startup_recovery(conn, max_attempts=3)
    assert recovered == 1
    assert db.counts(conn).get(db.PENDING) == 1
    assert db.counts(conn).get(db.LEASED, 0) == 0
    conn.close()


@pytest.mark.base
def test_reset_stale_leases(tmp_path):
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    (scen_dir / "a.json").write_text("{}", encoding="utf-8")
    conn = db.connect(str(tmp_path / "tasks.db"))
    db.import_scenarios(conn, str(scen_dir), "*.json")
    db.lease_tasks(conn, 1, "rank1@node1")
    conn.commit()

    # Backdate the lease so it looks stale.
    conn.execute("UPDATE tasks SET leased_at = leased_at - 100")
    conn.commit()
    reclaimed = db.reset_stale_leases(conn, lease_timeout_s=10.0, max_attempts=3)
    conn.commit()
    assert reclaimed == 1
    assert db.counts(conn).get(db.PENDING) == 1
    conn.close()


# ----------------------------------------------------------------------- config
@pytest.mark.base
def test_config_from_file_and_overrides(tmp_path):
    cfg_path = tmp_path / "harness.json"
    cfg_path.write_text(
        '{"db": "t.db", "sim_params": "s.json", "result_root": "out", "timeout_s": 100}',
        encoding="utf-8",
    )
    cfg = HarnessConfig.from_file(str(cfg_path))
    cfg.apply_overrides(max_attempts=5, per_sim_mem_gb=None)  # None is ignored
    cfg.finalize()

    assert cfg.max_attempts == 5
    assert cfg.per_sim_mem_gb == 10.0          # default kept (override was None)
    assert cfg.lease_timeout_s == 200.0         # derived: 2 * timeout_s


@pytest.mark.base
def test_config_rejects_unknown_keys(tmp_path):
    cfg_path = tmp_path / "harness.json"
    cfg_path.write_text('{"db": "t.db", "bogus": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        HarnessConfig.from_file(str(cfg_path))


@pytest.mark.base
def test_config_missing_required_raises():
    with pytest.raises(ValueError):
        HarnessConfig(db="only.db").finalize()


@pytest.mark.base
def test_compute_max_slots():
    assert compute_max_slots(per_sim_mem_gb=10, min_headroom_gb=12, configured=7) == 7
    # Derived value is positive and leaves headroom on a real machine.
    assert compute_max_slots(per_sim_mem_gb=10, min_headroom_gb=12, configured=None) >= 1


# ------------------------------------------------------------------------- pool
def _drive(pool, max_seconds=15.0):
    """Tick a pool until it is idle, collecting all reports."""
    reports = []
    deadline = time.time() + max_seconds
    while not pool.is_idle() and time.time() < deadline:
        reports.extend(pool.tick())
        time.sleep(0.05)
    reports.extend(pool.tick())
    return reports


def _make_pool(tmp_path, builder, timeout_s=60.0, max_slots=4):
    # per_sim_mem/headroom = 0 so the memory gate never blocks the test.
    return LocalPool(
        host="test", sim_params="ignored.json", result_root=str(tmp_path / "results"),
        per_sim_mem_gb=0.0, min_headroom_gb=0.0, timeout_s=timeout_s,
        max_slots=max_slots, command_builder=builder,
    )


@pytest.mark.base
def test_pool_success_and_failure(tmp_path):
    def builder(task, result_dir, sim_params):
        if task["scenario_path"].endswith("fail.json"):
            return [sys.executable, "-c", "import sys; sys.exit(3)"]
        return [sys.executable, "-c", "import time; time.sleep(0.3)"]

    pool = _make_pool(tmp_path, builder)
    pool.add_tasks([
        {"id": 1, "scenario_path": "/x/ok.json"},
        {"id": 2, "scenario_path": "/x/fail.json"},
    ])
    reports = {r["id"]: r for r in _drive(pool)}

    assert reports[1]["status"] == db.DONE
    assert reports[1]["exit_code"] == 0
    assert reports[1]["peak_mem_mb"] > 0          # sampled while it slept
    assert reports[1]["duration_s"] >= 0.0
    assert reports[2]["status"] == db.FAILED
    assert reports[2]["exit_code"] == 3
    assert pool.is_idle()


@pytest.mark.base
def test_pool_timeout_kills_subprocess(tmp_path):
    def builder(task, result_dir, sim_params):
        return [sys.executable, "-c", "import time; time.sleep(60)"]

    pool = _make_pool(tmp_path, builder, timeout_s=0.5)
    pool.add_tasks([{"id": 1, "scenario_path": "/x/hang.json"}])
    reports = _drive(pool, max_seconds=20.0)

    assert len(reports) == 1
    assert reports[0]["status"] == db.FAILED
    assert reports[0]["error"] == "timeout"
    assert pool.is_idle()
