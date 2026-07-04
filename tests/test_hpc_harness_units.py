"""Unit tests for HPC-harness pieces that need no server: autoscaler law, circuit
breaker, memory budget, ETA, config parsing, console ring, slot sizing, run_one seams.
"""

import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from hpc_harness.config import (  # noqa: E402
    CircuitBreakerConfig,
    ServerConfig,
    WorkerConfig,
)
from hpc_harness.server.autoscaler import compute_to_submit, parse_sinfo_cpus  # noqa: E402
from hpc_harness.server.circuit import CircuitBreaker  # noqa: E402
from hpc_harness.server.eta import ThroughputTracker  # noqa: E402
from hpc_harness.server.memcheck import MemBudget  # noqa: E402
from hpc_harness.worker.logbuffer import ConsoleRing, ErrorReporter  # noqa: E402
from hpc_harness.worker.warm_pool import compute_max_slots  # noqa: E402
from hpc_harness import run_one  # noqa: E402

pytestmark = pytest.mark.base


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
    assert compute_to_submit(work, current, available, queued, 10, 2000) == expected


def test_autoscaler_respects_max_workers():
    assert compute_to_submit(10_000, 1990, 500, 0, 10, 2000) == 10
    assert compute_to_submit(10_000, 2000, 500, 0, 10, 2000) == 0


def test_parse_sinfo_cpus_sums_idle_field():
    text = "4523/1234/89/5846\n0/56/0/56\nnot-a-line\n1/2/3\n"
    assert parse_sinfo_cpus(text) == 1234 + 56  # A/I/O/T format, idle is field 2


def test_default_sbatch_missing_script_raises_clear_error(tmp_path):
    from hpc_harness.server.autoscaler import default_sbatch

    with pytest.raises(RuntimeError, match="does not exist"):
        default_sbatch(str(tmp_path / "nope.sbatch"), 1)
    with pytest.raises(RuntimeError, match="not set"):
        default_sbatch("", 1)


# --------------------------------------------------------------- circuit breaker


def _cb(**kwargs):
    return CircuitBreaker(CircuitBreakerConfig(**{"window": 10, "min_samples": 4,
                                                  "failure_rate": 0.5, "consecutive": 3,
                                                  **kwargs}))


def test_circuit_trips_on_consecutive_failures():
    breaker = _cb()
    assert not breaker.record(False, "err a")
    assert not breaker.record(False, "err a")
    assert breaker.record(False, "err a")
    assert "consecutive" in breaker.tripped
    assert breaker.top_error() == "err a"


def test_circuit_trips_on_failure_rate_after_min_samples():
    breaker = _cb(consecutive=100)
    breaker.record(False)
    breaker.record(True)
    assert not breaker.tripped  # only 2 samples < min_samples
    breaker.record(False)
    tripped_now = breaker.record(False)
    assert tripped_now and "rate" in breaker.tripped


def test_circuit_success_resets_consecutive_and_reset_clears():
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
    breaker = _cb(enabled=False, consecutive=1)
    assert not breaker.record(False)
    assert breaker.tripped is None


# ------------------------------------------------------------------ memory budget


def _mem_cfg(**kwargs):
    cfg = ServerConfig(db_path="x", result_root="y", per_job_mem_gb=10.0,
                       mem_min_samples=3, mem_autoraise_margin_gb=1.0)
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


def test_membudget_autoraise_only_after_min_samples():
    budget = MemBudget(_mem_cfg())
    assert not budget.observe(12 * 1024)
    assert not budget.observe(12 * 1024)
    assert budget.effective == 10.0
    assert budget.observe(12 * 1024)  # third sample: p99=12 -> raise to 13
    assert budget.effective == pytest.approx(13.0)
    assert budget.warning()["kind"] == "auto_raised"


def test_membudget_never_lowers_automatically_and_warns_when_too_high():
    budget = MemBudget(_mem_cfg())
    for _ in range(5):
        budget.observe(2 * 1024)  # jobs use 2 GB against a 10 GB budget
    assert budget.effective == 10.0
    warning = budget.warning()
    assert warning["kind"] == "too_high"


def test_membudget_manual_set_lowers():
    persisted = []
    budget = MemBudget(_mem_cfg(), persist_fn=persisted.append)
    budget.set_manual(4.0)
    assert budget.effective == 4.0
    assert persisted == [4.0]


def test_membudget_autoraise_disabled():
    budget = MemBudget(_mem_cfg(mem_autoraise=False))
    for _ in range(5):
        budget.observe(20 * 1024)
    assert budget.effective == 10.0


# ---------------------------------------------------------------------------- eta


def test_throughput_and_eta():
    tracker = ThroughputTracker(window_s=600)
    now = time.time()
    for i in range(10):
        tracker.record(now - 60 + i * 6)
    assert tracker.throughput_per_min() > 0
    assert tracker.eta_seconds(100) > 0
    assert tracker.eta_seconds(0) is None


# --------------------------------------------------------------------------- config


def test_server_config_rejects_unknown_keys_and_parses_nested():
    with pytest.raises(ValueError, match="Unknown keys"):
        ServerConfig.from_dict({"db_pathh": "x"})
    cfg = ServerConfig.from_dict(
        {"db_path": "t.db", "result_root": "r", "circuit_breaker": {"consecutive": 7},
         "autoscale": {"enabled": True, "standby_floor": 5}}
    )
    assert cfg.circuit_breaker.consecutive == 7
    assert cfg.autoscale.standby_floor == 5
    with pytest.raises(ValueError, match="Unknown keys"):
        ServerConfig.from_dict({"db_path": "x", "result_root": "r", "autoscale": {"floor": 1}})


def test_server_config_finalize_derives_logs_db(tmp_path):
    cfg = ServerConfig(db_path=str(tmp_path / "core" / "tasks.db"),
                       result_root=str(tmp_path / "res")).finalize()
    assert cfg.logs_db_path == str((tmp_path / "core" / "logs.db").resolve())
    assert cfg.max_attempts == 4


def test_worker_config_single_core_forces_one_slot(tmp_path):
    cfg = WorkerConfig(server_url="http://x", result_root=str(tmp_path),
                       mode="single_core", max_slots=32).finalize()
    assert cfg.max_slots == 1
    with pytest.raises(ValueError, match="node_gate"):
        WorkerConfig(server_url="http://x", result_root=str(tmp_path),
                     node_gate="bogus").finalize()


# --------------------------------------------------------------------- console ring


def test_error_reporter_captures_logged_exceptions_and_explicit_adds():
    import logging

    reporter = ErrorReporter("worker")
    logger = logging.getLogger("test.errorreporter")
    logger.addHandler(reporter)
    logger.setLevel(logging.DEBUG)
    try:
        logger.warning("just a warning")  # below ERROR: ignored
        try:
            raise ValueError("kaboom")
        except ValueError:
            logger.exception("caught it")
    finally:
        logger.removeHandler(reporter)

    reporter.add(message="job 7 failed", error_type="JobFailure",
                 traceback_text="Traceback...\nBoom", job_id=7)

    records = reporter.drain()
    assert len(records) == 2  # the warning was not captured
    logged, explicit = records
    assert logged["error_type"] == "ValueError"
    assert "kaboom" in logged["traceback"] and logged["source"] == "worker"
    assert explicit["job_id"] == 7 and explicit["error_type"] == "JobFailure"
    assert reporter.drain() == []  # drained


def test_console_ring_tail_and_incremental_offsets():
    ring = ConsoleRing(max_chars=20)
    ring.append("aaaaa")
    text, offset = ring.since(0)
    assert text == "aaaaa" and offset == 5
    ring.append("bbbbb")
    text, offset = ring.since(offset)
    assert text == "bbbbb" and offset == 10
    ring.append("c" * 30)  # overflows the ring
    assert ring.tail() == "c" * 20
    text, _ = ring.since(offset)
    assert set(text) == {"c"}


# ---------------------------------------------------------------------- slot sizing


def test_compute_max_slots_is_min_of_memory_and_cores():
    # 256 GB node, 10 GB/job, 12 GB headroom -> 24 memory slots; 128 cores -> min = 24.
    assert compute_max_slots(10.0, 12.0, cores=128, cores_per_job=1, reserved_cores=0,
                             total_mem_gb=256.0) == 24
    # 4-core node with plenty of memory: cores bind.
    assert compute_max_slots(1.0, 1.0, cores=4, cores_per_job=1, reserved_cores=1,
                             total_mem_gb=256.0) == 3
    # Configured cap wins when smaller; floor of 1 always holds.
    assert compute_max_slots(10.0, 12.0, cores=128, cores_per_job=1, reserved_cores=0,
                             configured=8, total_mem_gb=256.0) == 8
    assert compute_max_slots(500.0, 12.0, cores=1, cores_per_job=1, reserved_cores=0,
                             total_mem_gb=16.0) == 1


# ------------------------------------------------- system-setup runner & submit script


def test_setup_runner_builds_one_week_parameters():
    from hpc_harness.runners.hisim_setup_runner import _build_parameters

    params = _build_parameters({"duration": "one_week", "year": 2021, "seconds_per_timestep": 60})
    assert (params.end_date - params.start_date).days == 7
    assert params.seconds_per_timestep == 60
    with pytest.raises(ValueError, match="Unknown duration"):
        _build_parameters({"duration": "two_fortnights"})


def test_setup_runner_is_registered():
    from hpc_harness.runners import get_runner

    assert get_runner("hisim_setup").name == "hisim_setup"


def test_find_setups_skips_init_and_excludes(tmp_path):
    sys.path.insert(0, str(SCRIPTS / "hpc_harness"))
    from submit_system_setups import find_setups

    for name in ("__init__.py", "a_setup.py", "b_setup.py", "notes.txt"):
        (tmp_path / name).write_text("", encoding="utf-8")
    found = find_setups(tmp_path, exclude=["b_setup"])
    assert [p.name for p in found] == ["a_setup.py"]


# ------------------------------------------------------------------ run_one (moved)


class _FakeSimParams:
    def __init__(self):
        self.result_directory = ""


class _FakeSimulator:
    def __init__(self):
        self.params = _FakeSimParams()

    def get_simulation_parameters(self):
        return self.params


def test_run_single_overrides_result_dir_before_running():
    order = []
    simulator = _FakeSimulator()

    def fake_init(scenario, simulation_parameters, path_to_module, delta):
        order.append("init")
        assert scenario == "scn.json" and simulation_parameters == "sim.json"
        return simulator

    def fake_run(sim, path_to_module):
        order.append("run")
        # The harness contract: result_directory is set BEFORE run_fn is invoked.
        assert sim.get_simulation_parameters().result_directory == "/results/000001"
        assert path_to_module == "scn.json"

    returned = run_one.run_single(
        "scn.json", "sim.json", "/results/000001", init_fn=fake_init, run_fn=fake_run
    )
    assert order == ["init", "run"]
    assert returned is simulator
