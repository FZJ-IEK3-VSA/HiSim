"""Unit tests for server-free HPC-harness pieces (autoscaler, circuit breaker, memory, ETA, config, console ring, slots, run_one)."""

import sys
import time
from pathlib import Path

import pytest

from hpc_harness import run_one
from hpc_harness.config import CircuitBreakerConfig, ServerConfig, WorkerConfig
from hpc_harness.server.autoscaler import (
    compute_to_submit,
    default_sbatch,
    parse_sinfo_cpus,
    read_log_tail,
)
from hpc_harness.server.circuit import CircuitBreaker
from hpc_harness.server.eta import ThroughputTracker
from hpc_harness.server.memcheck import MemBudget
from hpc_harness.worker.logbuffer import ConsoleRing, ErrorReporter
from hpc_harness.worker.warm_pool import compute_max_slots

pytestmark = pytest.mark.base

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


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


def test_default_sbatch_routes_logs_into_log_dir(tmp_path, monkeypatch):
    """With a log_dir, sbatch is invoked with --output/--error into it, and the dir is created."""
    from hpc_harness.server import autoscaler as autoscaler_mod

    script = tmp_path / "worker.sbatch"
    script.write_text("#!/bin/bash\n")
    log_dir = tmp_path / "logs"  # deliberately absent up front
    captured = {}

    class _Result:
        returncode = 0
        stdout = "12345"
        stderr = ""

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(autoscaler_mod.subprocess, "run", fake_run)
    job_ids = default_sbatch(str(script), 1, str(log_dir))

    assert job_ids == ["12345"]
    assert log_dir.is_dir()  # created for Slurm to write into
    joined = " ".join(captured["cmd"])
    assert f"--output={log_dir / 'worker-%j.out'}" in joined
    assert f"--error={log_dir / 'worker-%j.err'}" in joined


def test_default_sbatch_exports_worker_config(tmp_path, monkeypatch):
    """A worker_config is passed to the job as HARNESS_WORKER_CONFIG (with --export=ALL)."""
    from hpc_harness.server import autoscaler as autoscaler_mod

    script = tmp_path / "worker.sbatch"
    script.write_text("#!/bin/bash\n")
    worker_cfg = tmp_path / "worker.json"
    worker_cfg.write_text("{}")
    captured = {}

    class _Result:
        returncode = 0
        stdout = "77"
        stderr = ""

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(autoscaler_mod.subprocess, "run", fake_run)
    assert default_sbatch(str(script), 1, None, str(worker_cfg)) == ["77"]
    assert f"--export=ALL,HARNESS_WORKER_CONFIG={worker_cfg}" in captured["cmd"]


def test_default_sbatch_missing_worker_config_raises(tmp_path):
    """A worker_config that does not exist fails fast with a clear error, before any sbatch."""
    script = tmp_path / "worker.sbatch"
    script.write_text("#!/bin/bash\n")
    with pytest.raises(RuntimeError, match="worker_config does not exist"):
        default_sbatch(str(script), 1, None, str(tmp_path / "absent.json"))


def test_default_sbatch_exports_worker_runner(tmp_path, monkeypatch):
    """A worker_runner is passed to the job as HARNESS_WORKER_RUNNER (pinning the fleet's runner)."""
    from hpc_harness.server import autoscaler as autoscaler_mod

    script = tmp_path / "worker.sbatch"
    script.write_text("#!/bin/bash\n")
    captured = {}

    class _Result:
        returncode = 0
        stdout = "9"
        stderr = ""

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(autoscaler_mod.subprocess, "run", fake_run)
    assert default_sbatch(str(script), 1, None, None, "hisim_setup") == ["9"]
    assert "--export=ALL,HARNESS_WORKER_RUNNER=hisim_setup" in captured["cmd"]


def test_read_log_tail_returns_last_lines_or_none(tmp_path):
    """read_log_tail returns None for a missing file and the last N lines otherwise."""
    assert read_log_tail(str(tmp_path / "absent.out")) is None
    log = tmp_path / "worker.out"
    log.write_text("\n".join(f"line{i}" for i in range(100)) + "\n")
    tail = read_log_tail(str(log), max_lines=5)
    assert tail is not None
    assert tail.splitlines() == ["line95", "line96", "line97", "line98", "line99"]


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


# --------------------------------------------------------------------------- config


def test_server_config_rejects_unknown_keys_and_parses_nested():
    """Unknown keys (top-level and nested) are rejected while valid nested blocks parse."""
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
    """finalize() derives the logs DB next to the core DB and computes max_attempts."""
    cfg = ServerConfig(db_path=str(tmp_path / "core" / "tasks.db"),
                       result_root=str(tmp_path / "res")).finalize()
    assert cfg.logs_db_path == str((tmp_path / "core" / "logs.db").resolve())
    assert cfg.max_attempts == 4


def test_worker_config_single_core_forces_one_slot(tmp_path):
    """single_core mode forces max_slots to 1, and an unknown node_gate is rejected."""
    cfg = WorkerConfig(server_url="http://x", result_root=str(tmp_path),
                       mode="single_core", max_slots=32).finalize()
    assert cfg.max_slots == 1
    with pytest.raises(ValueError, match="node_gate"):
        WorkerConfig(server_url="http://x", result_root=str(tmp_path),
                     node_gate="bogus").finalize()


def test_autoscale_profiles_parse_inherit_and_validate(tmp_path):
    """Profiles parse from JSON, inherit top-level defaults, and reject duplicate/blank runners."""
    cfg = ServerConfig.from_dict({
        "db_path": str(tmp_path / "t.db"), "result_root": str(tmp_path / "r"),
        "autoscale": {"enabled": True, "worker_script": "w.sbatch", "max_workers": 7,
                      "profiles": [{"name": "a", "runner": "hisim"},
                                   {"name": "b", "runner": "hisim_setup", "max_workers": 3}]},
    })
    cfg.finalize()
    profiles = {p.name: p for p in cfg.autoscale.resolved_profiles()}
    assert profiles["a"].runner == "hisim" and profiles["a"].max_workers == 7  # inherited
    assert profiles["b"].max_workers == 3  # per-profile override
    assert profiles["a"].worker_script is not None  # inherited from top-level

    with pytest.raises(ValueError, match="distinct runners"):
        ServerConfig.from_dict({
            "db_path": "x", "result_root": "y",
            "autoscale": {"enabled": True, "worker_script": "w",
                          "profiles": [{"name": "a", "runner": "r"}, {"name": "b", "runner": "r"}]},
        }).finalize()
    with pytest.raises(ValueError, match="must set a 'runner'"):
        ServerConfig.from_dict({
            "db_path": "x", "result_root": "y",
            "autoscale": {"enabled": True, "worker_script": "w", "profiles": [{"name": "a"}]},
        }).finalize()


# --------------------------------------------------------------------- console ring


def test_error_reporter_captures_logged_exceptions_and_explicit_adds():
    """The reporter captures ERROR-level log exceptions and explicit adds, then drains once."""
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
    assert not reporter.drain()  # drained


def test_console_ring_tail_and_incremental_offsets():
    """The console ring serves incremental slices by offset and a bounded tail on overflow."""
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
    """Slot count is the min of memory- and core-derived limits, capped and floored at 1."""
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
    """The setup runner builds one-week SimulationParameters and rejects unknown durations."""
    from hpc_harness.runners.hisim_setup_runner import _build_parameters

    params = _build_parameters({"duration": "one_week", "year": 2021, "seconds_per_timestep": 60})
    assert (params.end_date - params.start_date).days == 7
    assert params.seconds_per_timestep == 60
    with pytest.raises(ValueError, match="Unknown duration"):
        _build_parameters({"duration": "two_fortnights"})


def test_setup_runner_applies_post_processing_options():
    """Payload post_processing_options are appended (deduped); unknown names are rejected."""
    from hisim.postprocessingoptions import PostProcessingOptions
    from hpc_harness.runners.hisim_setup_runner import _build_parameters

    params = _build_parameters({"duration": "one_day", "post_processing_options": ["PLOT_LINE", "COMPUTE_KPIS"]})
    assert PostProcessingOptions.PLOT_LINE in params.post_processing_options
    assert PostProcessingOptions.COMPUTE_KPIS in params.post_processing_options
    assert params.post_processing_options.count(PostProcessingOptions.PLOT_LINE) == 1  # no dupes
    with pytest.raises(ValueError, match="Unknown PostProcessingOptions"):
        _build_parameters({"duration": "one_day", "post_processing_options": ["NOT_A_REAL_OPTION"]})


def test_setup_runner_is_registered():
    """The hisim_setup runner is discoverable through the runner registry."""
    from hpc_harness.runners import get_runner

    assert get_runner("hisim_setup").name == "hisim_setup"


def test_find_setups_skips_init_and_excludes(tmp_path):
    """find_setups returns *_setup.py files, skipping __init__.py, non-py, and excludes."""
    sys.path.insert(0, str(SCRIPTS / "hpc_harness"))
    from submit_system_setups import find_setups  # pylint: disable=import-error

    for name in ("__init__.py", "a_setup.py", "b_setup.py", "notes.txt"):
        (tmp_path / name).write_text("", encoding="utf-8")
    found = find_setups(tmp_path, exclude=["b_setup"])
    assert [p.name for p in found] == ["a_setup.py"]


def test_find_json_setups_filters_by_name(tmp_path):
    """find_json_setups keeps only *.scenario.json whose name contains the (case-insensitive) filter."""
    sys.path.insert(0, str(SCRIPTS / "hpc_harness"))
    from submit_json_setups import find_json_setups  # pylint: disable=import-error

    for name in ("household_gas_building_sizer.scenario.json", "Household_HP_Building_Sizer.scenario.json",
                 "basic_household.scenario.json", "household_gas_building_sizer.py",
                 "notes.txt"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    found = {p.name for p in find_json_setups(tmp_path, "building_sizer")}
    assert found == {"Household_HP_Building_Sizer.scenario.json",
                     "household_gas_building_sizer.scenario.json"}  # case-insensitive; .py/non-matching excluded


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


# ------------------------------------------------------------------ run_one (moved)


class _FakeSimParams:
    """Minimal stand-in for SimulationParameters exposing a settable result_directory."""

    def __init__(self):
        self.result_directory = ""


class _FakeSimulator:
    """Minimal stand-in for the Simulator that just hands back its parameters."""

    def __init__(self):
        self.params = _FakeSimParams()

    def get_simulation_parameters(self):
        """Return the fake simulation parameters."""
        return self.params


def test_run_single_overrides_result_dir_before_running():
    """run_single sets result_directory before invoking run_fn, in init-then-run order."""
    order = []
    simulator = _FakeSimulator()

    def fake_init(scenario, simulation_parameters, path_to_module, delta):  # pylint: disable=unused-argument
        """Fake init_fn: assert the scenario/param args and return the fake simulator."""
        order.append("init")
        assert scenario == "scn.json" and simulation_parameters == "sim.json"
        return simulator

    def fake_run(sim, path_to_module):
        """Fake run_fn: assert result_directory was set and the module path forwarded."""
        order.append("run")
        # The harness contract: result_directory is set BEFORE run_fn is invoked.
        assert sim.get_simulation_parameters().result_directory == "/results/000001"
        assert path_to_module == "scn.json"

    returned = run_one.run_single(
        "scn.json", "sim.json", "/results/000001", init_fn=fake_init, run_fn=fake_run
    )
    assert order == ["init", "run"]
    assert returned is simulator
