"""Tests for the HiSim MPI HPC harness (hisim.hpc_harness).

These cover the parts that carry the logic and are easy to get wrong: the SQLite
task state machine, config loading/overrides, and the node-local subprocess pool
(launch, reap, retry-relevant status, timeout kill, peak-memory sampling).

They do not require mpi4py or a real HiSim run: the pool is driven with a custom
command builder that launches trivial python subprocesses.
"""

import sys
import time
from pathlib import Path

import pytest

from hisim.hpc_harness import db
from hisim.hpc_harness.config import HarnessConfig, _normalize_path
from hisim.hpc_harness.pool import LocalPool, compute_max_slots

# Polling interval for pool tick loop in _drive().
TICK_INTERVAL = 0.05


# --------------------------------------------------------------------------- db
@pytest.mark.base
def test_import_is_idempotent(tmp_path):
    """Test that importing the same scenario directory twice does not duplicate tasks."""
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
    """Test task leasing, reporting, retry, and dead-letter state transitions."""
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
    """Test that startup recovery requeues tasks left in leased state."""
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
    """Test that stale task leases are reclaimed as pending work."""
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
    """Test loading harness config from JSON and applying runtime overrides."""
    cfg_path = tmp_path / "harness.json"
    cfg_path.write_text(
        '{"db_path": "t.db", "sim_params_path": "s.json", "result_root": "out", "timeout_s": 100}',
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
    """Test that harness config loading rejects unknown JSON keys."""
    cfg_path = tmp_path / "harness.json"
    cfg_path.write_text('{"db_path": "t.db", "bogus": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        HarnessConfig.from_file(str(cfg_path))


@pytest.mark.base
def test_config_missing_required_raises():
    """Test that incomplete harness configuration fails finalization."""
    with pytest.raises(ValueError):
        HarnessConfig(db_path="only.db").finalize()


@pytest.mark.base
def test_normalize_path_pins_cwd_and_home():
    """_normalize_path resolves relative/~/paths against injected cwd/home.

    The whole point of the seam (issue #668) is that path normalisation must
    not silently depend on the process's CWD or HOME. With explicit cwd/home
    the output is a pure function of the inputs and can be asserted exactly.
    """
    cwd = Path("/srv/harness_proj")
    home = Path("/srv/harness_home")
    # Relative path is joined onto cwd.
    assert _normalize_path("tasks.db", cwd=cwd, home=home) == "/srv/harness_proj/tasks.db"
    # A nested relative path is resolved under cwd.
    assert _normalize_path("sub/runs.json", cwd=cwd, home=home) == "/srv/harness_proj/sub/runs.json"
    # ".." segments are collapsed against cwd.
    assert _normalize_path("../sibling/x", cwd=Path("/srv/harness_proj/inner"), home=home) == "/srv/harness_proj/sibling/x"
    # Bare "~" expands to home.
    assert _normalize_path("~", cwd=cwd, home=home) == "/srv/harness_home"
    # "~/..." expands under home.
    assert _normalize_path("~/configs/runs.json", cwd=cwd, home=home) == "/srv/harness_home/configs/runs.json"
    # Absolute paths are returned resolved, cwd/home are not consulted.
    assert _normalize_path("/abs/elsewhere/out", cwd=cwd, home=home) == "/abs/elsewhere/out"


@pytest.mark.base
def test_normalize_path_defaults_match_expanduser_resolve(tmp_path, monkeypatch):
    """With no injection, _normalize_path reproduces Path.expanduser().resolve().

    This guards the "behaviour is unchanged for production" contract: the
    default fast path must equal the old inline str(Path(p).expanduser().resolve()).
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Relative path -> resolved against the (monkeypatched) cwd.
    rel = "data/tasks.db"
    assert _normalize_path(rel) == str(Path(rel).expanduser().resolve())
    # "~/..." -> expanded against the (monkeypatched) HOME then resolved.
    tilde = "~/configs/runs.json"
    assert _normalize_path(tilde) == str(Path(tilde).expanduser().resolve())
    # Absolute path -> resolved as-is.
    abs_path = str(tmp_path / "abs" / "out")
    assert _normalize_path(abs_path) == str(Path(abs_path).expanduser().resolve())


@pytest.mark.base
def test_normalize_path_only_cwd_or_only_home_injected():
    """Injecting just one of cwd/home still yields deterministic output."""
    cwd = Path("/srv/harness_proj")
    home = Path("/srv/harness_home")
    # Only cwd injected: home falls back to Path.home(), but a non-"~" path
    # does not touch home, so the result is still deterministic.
    assert _normalize_path("tasks.db", cwd=cwd) == "/srv/harness_proj/tasks.db"
    # Only home injected: a "~/..." path is deterministic; cwd is unused for
    # absolute results.
    assert _normalize_path("~/x", home=home) == "/srv/harness_home/x"


@pytest.mark.base
def test_finalize_paths_independent_of_cwd_and_home():
    """finalize(cwd=..., home=...) normalises paths without touching CWD/HOME.

    This is the user-facing payoff of issue #668: a test can pin cwd/home and
    assert exact normalised strings on db_path/sim_params_path/result_root,
    with no monkeypatch.chdir / monkeypatch.setenv and no cwd-dependent output.
    """
    cfg = HarnessConfig(
        db_path="tasks.db",
        sim_params_path="~/configs/runs.json",
        result_root="out",
    )
    cfg.finalize(cwd=Path("/srv/harness_proj"), home=Path("/srv/harness_home"))
    assert cfg.db_path == "/srv/harness_proj/tasks.db"
    assert cfg.sim_params_path == "/srv/harness_home/configs/runs.json"
    assert cfg.result_root == "/srv/harness_proj/out"
    # Derived defaults are unaffected by the new kwargs.
    assert cfg.lease_timeout_s == 2.0 * cfg.timeout_s


@pytest.mark.base
def test_finalize_default_uses_process_cwd_and_home(tmp_path, monkeypatch):
    """finalize() with no kwargs still resolves against the real CWD/HOME."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = HarnessConfig(db_path="tasks.db", sim_params_path="~/runs.json", result_root="out")
    cfg.finalize()
    assert cfg.db_path == str((tmp_path / "tasks.db").resolve())
    assert cfg.sim_params_path == str((tmp_path / "runs.json").resolve())
    assert cfg.result_root == str((tmp_path / "out").resolve())


@pytest.mark.base
def test_apply_derived_defaults_is_pure_no_filesystem():
    """_apply_derived_defaults sets lease_timeout_s with no filesystem access.

    Issue #698: the derived-default rule (lease_timeout_s = 2 * timeout_s when
    unset) must be exercisable in isolation, i.e. without supplying the three
    real, resolvable paths that finalize/required_paths demand. It must also
    leave an explicit lease_timeout_s untouched and be idempotent.
    """
    # No db_path/sim_params_path/result_root set: finalize would raise, but
    # _apply_derived_defaults must succeed because it does no path I/O.
    cfg = HarnessConfig(timeout_s=42.0)
    lease_before = cfg.lease_timeout_s
    assert lease_before is None
    cfg._apply_derived_defaults()  # pylint: disable=protected-access
    assert cfg.lease_timeout_s == 84.0

    # An explicit value is preserved (no clobbering).
    cfg2 = HarnessConfig(timeout_s=42.0, lease_timeout_s=7.0)
    cfg2._apply_derived_defaults()  # pylint: disable=protected-access
    assert cfg2.lease_timeout_s == 7.0

    # Idempotent: a second call does not double the timeout.
    cfg._apply_derived_defaults()  # pylint: disable=protected-access
    assert cfg.lease_timeout_s == 84.0
    # The path fields are still untouched (no I/O happened).
    assert cfg.db_path is None
    assert cfg.sim_params_path is None
    assert cfg.result_root is None


@pytest.mark.base
def test_resolve_paths_normalises_in_place():
    """_resolve_paths rewrites the three required paths in place via _normalize_path."""
    cfg = HarnessConfig(
        db_path="tasks.db",
        sim_params_path="~/configs/runs.json",
        result_root="out",
    )
    cfg._resolve_paths(cwd=Path("/srv/harness_proj"), home=Path("/srv/harness_home"))  # pylint: disable=protected-access
    assert cfg.db_path == "/srv/harness_proj/tasks.db"
    assert cfg.sim_params_path == "/srv/harness_home/configs/runs.json"
    assert cfg.result_root == "/srv/harness_proj/out"


@pytest.mark.base
def test_resolve_paths_validates_required_paths():
    """_resolve_paths raises when a required path is missing, just like finalize."""
    with pytest.raises(ValueError):
        HarnessConfig(db_path="only.db")._resolve_paths()  # pylint: disable=protected-access


@pytest.mark.base
def test_config_from_file_accepts_deprecated_db_key(tmp_path):
    """Test that the pre-rename JSON key 'db' is accepted with a deprecation warning."""
    cfg_path = tmp_path / "harness.json"
    cfg_path.write_text(
        '{"db": "t.db", "sim_params_path": "s.json", "result_root": "out"}',
        encoding="utf-8",
    )
    with pytest.warns(DeprecationWarning, match="'db' is deprecated"):
        cfg = HarnessConfig.from_file(str(cfg_path))
    cfg.finalize()
    assert cfg.db_path is not None
    assert cfg.sim_params_path is not None
    assert cfg.db_path.endswith("t.db")
    assert cfg.sim_params_path.endswith("s.json")


@pytest.mark.base
def test_config_from_file_accepts_deprecated_sim_params_key(tmp_path):
    """Test that the pre-rename JSON key 'sim_params' is accepted with a deprecation warning."""
    cfg_path = tmp_path / "harness.json"
    cfg_path.write_text(
        '{"db_path": "t.db", "sim_params": "s.json", "result_root": "out"}',
        encoding="utf-8",
    )
    with pytest.warns(DeprecationWarning, match="'sim_params' is deprecated"):
        cfg = HarnessConfig.from_file(str(cfg_path))
    cfg.finalize()
    assert cfg.db_path is not None
    assert cfg.sim_params_path is not None
    assert cfg.db_path.endswith("t.db")
    assert cfg.sim_params_path.endswith("s.json")


@pytest.mark.base
def test_config_from_file_rejects_both_old_and_new_key(tmp_path):
    """Test that specifying both a deprecated key and its new name is an error."""
    cfg_path = tmp_path / "harness.json"
    cfg_path.write_text(
        '{"db": "a.db", "db_path": "b.db", "result_root": "out"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="both 'db' and its renamed form 'db_path'"):
        HarnessConfig.from_file(str(cfg_path))


@pytest.mark.base
def test_config_from_dict_builds_from_plain_dict_no_filesystem():
    """`from_dict` is the pure seam: parsing rules are testable with a plain dict."""
    cfg = HarnessConfig.from_dict(
        {"db_path": "t.db", "sim_params_path": "s.json", "result_root": "out", "timeout_s": 100}
    )
    assert cfg.db_path == "t.db"
    assert cfg.sim_params_path == "s.json"
    assert cfg.result_root == "out"
    assert cfg.timeout_s == 100


@pytest.mark.base
def test_config_from_dict_rejects_unknown_keys_and_uses_source_label():
    """`from_dict` rejects unknown keys and surfaces the `source` label in the error."""
    with pytest.raises(ValueError, match=r"Unknown keys in harness config <mem>: \['bogus'\]"):
        HarnessConfig.from_dict({"db_path": "t.db", "bogus": 1}, source="<mem>")


@pytest.mark.base
def test_config_from_dict_accepts_deprecated_db_key():
    """`from_dict` remaps the deprecated `db` key with a DeprecationWarning."""
    with pytest.warns(DeprecationWarning, match="'db' is deprecated"):
        cfg = HarnessConfig.from_dict({"db": "t.db", "sim_params_path": "s.json", "result_root": "out"})
    assert cfg.db_path == "t.db"
    assert cfg.sim_params_path == "s.json"
    assert cfg.result_root == "out"


@pytest.mark.base
def test_config_from_dict_accepts_deprecated_sim_params_key():
    """`from_dict` remaps the deprecated `sim_params` key with a DeprecationWarning."""
    with pytest.warns(DeprecationWarning, match="'sim_params' is deprecated"):
        cfg = HarnessConfig.from_dict({"db_path": "t.db", "sim_params": "s.json", "result_root": "out"})
    assert cfg.db_path == "t.db"
    assert cfg.sim_params_path == "s.json"
    assert cfg.result_root == "out"


@pytest.mark.base
def test_config_from_dict_rejects_both_old_and_new_key_with_source_label():
    """`from_dict` rejects conflicting deprecated/new keys and names `source` in the error."""
    with pytest.raises(ValueError, match=r"Harness config <mem> sets both 'db' and its renamed form 'db_path'"):
        HarnessConfig.from_dict({"db": "a.db", "db_path": "b.db"}, source="<mem>")


@pytest.mark.base
def test_config_from_dict_defaults_source_label_in_error():
    """`from_dict` falls back to the `<dict>` source label when none is supplied."""
    with pytest.raises(ValueError, match=r"Unknown keys in harness config <dict>: \['bogus'\]"):
        HarnessConfig.from_dict({"db_path": "t.db", "bogus": 1})


@pytest.mark.base
def test_config_from_file_delegates_to_from_dict(tmp_path):
    """`from_file` and `from_dict` agree for identical inputs (same warnings/errors)."""
    cfg_path = tmp_path / "harness.json"
    cfg_path.write_text(
        '{"db_path": "t.db", "sim_params_path": "s.json", "result_root": "out", "timeout_s": 100}',
        encoding="utf-8",
    )
    from_file_cfg = HarnessConfig.from_file(str(cfg_path))
    from_dict_cfg = HarnessConfig.from_dict(
        {"db_path": "t.db", "sim_params_path": "s.json", "result_root": "out", "timeout_s": 100}
    )
    assert from_file_cfg == from_dict_cfg


@pytest.mark.base
def test_apply_overrides_accepts_deprecated_names():
    """Test that apply_overrides remaps deprecated kwarg names with a warning."""
    cfg = HarnessConfig(db_path="t.db", sim_params_path="s.json", result_root="out")
    with pytest.warns(DeprecationWarning, match="'db' is deprecated"):
        cfg.apply_overrides(db="other.db")
    assert cfg.db_path == "other.db"
    with pytest.warns(DeprecationWarning, match="'sim_params' is deprecated"):
        cfg.apply_overrides(sim_params="other.json")
    assert cfg.sim_params_path == "other.json"


@pytest.mark.base
def test_apply_overrides_rejects_both_old_and_new_name():
    """Test that passing both a deprecated name and its new name to apply_overrides errors."""
    cfg = HarnessConfig(db_path="t.db", sim_params_path="s.json", result_root="out")
    with pytest.raises(ValueError, match="both 'db' and its renamed form 'db_path'"):
        cfg.apply_overrides(db="a.db", db_path="b.db")


@pytest.mark.base
def test_apply_overrides_none_old_name_does_not_conflict():
    """A deprecated kwarg passed as None is a no-op, not a conflict."""
    cfg = HarnessConfig(db_path="t.db", sim_params_path="s.json", result_root="out")
    # db=None means "no override"; db_path should still apply without error.
    cfg.apply_overrides(db=None, db_path="new.db")
    assert cfg.db_path == "new.db"
    # Same for sim_params.
    cfg.apply_overrides(sim_params=None, sim_params_path="new.json")
    assert cfg.sim_params_path == "new.json"


@pytest.mark.base
def test_deprecated_property_aliases():
    """Direct attribute access via the old names still works with a warning."""
    cfg = HarnessConfig(db_path="t.db", sim_params_path="s.json", result_root="out")
    # Getter aliases.
    with pytest.warns(DeprecationWarning, match="HarnessConfig.db is deprecated"):
        assert cfg.db == "t.db"
    with pytest.warns(DeprecationWarning, match="HarnessConfig.sim_params is deprecated"):
        assert cfg.sim_params == "s.json"
    # Setter aliases.
    with pytest.warns(DeprecationWarning, match="HarnessConfig.db is deprecated"):
        cfg.db = "other.db"
    assert cfg.db_path == "other.db"
    with pytest.warns(DeprecationWarning, match="HarnessConfig.sim_params is deprecated"):
        cfg.sim_params = "other.json"
    assert cfg.sim_params_path == "other.json"


@pytest.mark.base
def test_compute_max_slots():
    """Test explicit and derived subprocess slot calculation."""
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
        time.sleep(TICK_INTERVAL)
    reports.extend(pool.tick())
    return reports


def _make_pool(tmp_path, builder, timeout_s=60.0, max_slots=4):
    """Create a LocalPool for testing with zero memory gate."""
    # per_sim_mem/headroom = 0 so the memory gate never blocks the test.
    return LocalPool(
        host="test", sim_params="ignored.json", result_root=str(tmp_path / "results"),
        per_sim_mem_gb=0.0, min_headroom_gb=0.0, timeout_s=timeout_s,
        max_slots=max_slots, command_builder=builder,
    )


@pytest.mark.base
def test_pool_success_and_failure(tmp_path):
    """Test that the local pool reports successful and failed subprocesses."""

    def builder(task, _result_dir, _sim_params):
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
    """Test that timed-out subprocesses are killed and reported as failures."""

    def builder(_task, _result_dir, _sim_params):
        return [sys.executable, "-c", "import time; time.sleep(60)"]

    pool = _make_pool(tmp_path, builder, timeout_s=0.5)
    pool.add_tasks([{"id": 1, "scenario_path": "/x/hang.json"}])
    reports = _drive(pool, max_seconds=20.0)

    assert len(reports) == 1
    assert reports[0]["status"] == db.FAILED
    assert reports[0]["error"] == "timeout"
    assert pool.is_idle()


# ---------------------------------------------------------------------- run_one
class _FakeSimParams:
    """Minimal stand-in for SimulationParameters exposing ``result_directory``."""

    def __init__(self) -> None:
        self.result_directory = ""  # set by run_one before run_fn is called


class _FakeSim:
    """Minimal stand-in for the Simulator object returned by initialize_from_json."""

    def __init__(self) -> None:
        self.params = _FakeSimParams()
        # Record the call order so a test can assert init -> override -> run.
        self.events: list[str] = []

    def get_simulation_parameters(self) -> _FakeSimParams:
        """Return the stored fake simulation parameters."""
        return self.params


@pytest.mark.base
def test_run_one_overrides_result_directory_before_run(tmp_path):
    """run_one sets result_directory to the harness value before calling run_fn.

    This is the only real contract in run_one.py: the harness-assigned
    ``--result-dir`` must override the JSON-derived path *before* the simulation
    is executed. Using the ``init_fn``/``run_fn`` seam avoids importing
    ``hisim.hisim_main`` (no disk I/O, no heavy computation).
    """
    from hisim.hpc_harness import run_one

    sim = _FakeSim()

    def fake_init(scenario, simulation_parameters, path_to_module, delta):
        sim.events.append("init")
        assert scenario == str(scen_path)
        assert simulation_parameters == str(sim_params_path)
        assert path_to_module == str(scen_path)
        assert delta is None
        return sim

    def fake_run(my_sim, path_to_module):
        # By the time run_fn is invoked, the override must already be in place.
        sim.events.append("run")
        assert my_sim is sim
        assert path_to_module == str(scen_path)
        assert my_sim.get_simulation_parameters().result_directory == str(result_dir)

    scen_path = tmp_path / "case.scenario.json"
    sim_params_path = tmp_path / "case.simulation.json"
    result_dir = tmp_path / "results" / "task-42"
    scen_path.write_text("{}", encoding="utf-8")
    sim_params_path.write_text("{}", encoding="utf-8")

    run_one.main(
        argv=[
            "--scenario", str(scen_path),
            "--sim-params", str(sim_params_path),
            "--result-dir", str(result_dir),
        ],
        init_fn=fake_init,
        run_fn=fake_run,
    )

    # The override happened between init and run, and run was called exactly once.
    assert sim.events == ["init", "run"]
    assert sim.get_simulation_parameters().result_directory == str(result_dir)


@pytest.mark.base
def test_run_one_does_not_import_hisim_main_when_seams_injected(tmp_path):
    """When both seams are injected, run_one never touches the real hisim_main.

    This guards the lazy-import contract: a test (or alternate runtime) that
    supplies its own init/run functions must not pay the cost of importing the
    full simulator, and must not be coupled to itsim_main's I/O.
    """
    sim = _FakeSim()

    def fake_init(scenario, simulation_parameters, path_to_module, delta):  # pylint: disable=unused-argument
        return sim

    def fake_run(my_sim, path_to_module):  # pylint: disable=unused-argument
        return None

    scen_path = tmp_path / "case.scenario.json"
    sim_params_path = tmp_path / "case.simulation.json"
    result_dir = tmp_path / "out"
    scen_path.write_text("{}", encoding="utf-8")
    sim_params_path.write_text("{}", encoding="utf-8")

    hisim_main_before = sys.modules.get("hisim.hisim_main")
    from hisim.hpc_harness import run_one
    run_one.main(
        argv=[
            "--scenario", str(scen_path),
            "--sim-params", str(sim_params_path),
            "--result-dir", str(result_dir),
        ],
        init_fn=fake_init,
        run_fn=fake_run,
    )
    hisim_main_after = sys.modules.get("hisim.hisim_main")

    # If hisim_main was already imported in this process (e.g. by another test),
    # that's fine — what matters is that run_one did not *newly* import it for a
    # fully-injected call. We assert the module dict reference is unchanged.
    assert hisim_main_after is hisim_main_before


@pytest.mark.base
def test_run_one_requires_scenario_and_result_dir():
    """run_one still enforces its CLI contract when seams are injected."""
    from hisim.hpc_harness import run_one

    sim = _FakeSim()

    def fake_init(scenario, simulation_parameters, path_to_module, delta):  # pylint: disable=unused-argument
        return sim

    def fake_run(my_sim, path_to_module):  # pylint: disable=unused-argument
        return None

    with pytest.raises(SystemExit):
        run_one.main(argv=["--result-dir", "/tmp/out"], init_fn=fake_init, run_fn=fake_run)


@pytest.mark.base
def test_run_single_overrides_result_directory_before_run_and_returns_sim(tmp_path):
    """run_single forces result_directory before run_fn and returns the sim.

    Tests the orchestration seam directly — no argv parsing, no JSON files, no
    real simulation, no disk writes. Pass fakes for init_fn/run_fn and assert
    that the returned simulator's result_directory equals the harness value and
    that run_fn was invoked after the override (init -> override -> run).
    """
    from hisim.hpc_harness import run_one

    sim = _FakeSim()

    def fake_init(scenario, simulation_parameters, path_to_module, delta):
        sim.events.append("init")
        assert scenario == str(scen_path)
        assert simulation_parameters == str(sim_params_path)
        assert path_to_module == str(scen_path)
        assert delta is None
        return sim

    def fake_run(my_sim, path_to_module):
        # By the time run_fn is invoked, the override must already be in place.
        sim.events.append("run")
        assert my_sim is sim
        assert path_to_module == str(scen_path)
        assert my_sim.get_simulation_parameters().result_directory == str(result_dir)

    # Deliberately do NOT write the JSON files: run_single with injected seams
    # must touch no disk I/O at all.
    scen_path = tmp_path / "case.scenario.json"
    sim_params_path = tmp_path / "case.simulation.json"
    result_dir = tmp_path / "results" / "task-42"

    returned = run_one.run_single(
        scenario_path=str(scen_path),
        sim_params_path=str(sim_params_path),
        result_dir=str(result_dir),
        init_fn=fake_init,
        run_fn=fake_run,
    )

    # run_single returns the simulator built by init_fn (post-override, post-run)
    # so callers can assert the harness contract without argv or a real run.
    assert returned is sim
    assert sim.events == ["init", "run"]
    assert sim.get_simulation_parameters().result_directory == str(result_dir)


@pytest.mark.base
def test_run_single_does_not_import_hisim_main_when_seams_injected():
    """run_single never touches the real hisim_main when both seams are injected.

    Guards the lazy-import contract at the orchestration seam: a caller that
    supplies its own init/run functions must not pay the cost of importing the
    full simulator, and must not be coupled to hisim_main's I/O.
    """
    sim = _FakeSim()

    def fake_init(scenario, simulation_parameters, path_to_module, delta):  # pylint: disable=unused-argument
        return sim

    def fake_run(my_sim, path_to_module):  # pylint: disable=unused-argument
        return None

    hisim_main_before = sys.modules.get("hisim.hisim_main")
    from hisim.hpc_harness import run_one
    run_one.run_single(
        scenario_path="/tmp/case.scenario.json",
        sim_params_path="/tmp/case.simulation.json",
        result_dir="/tmp/out",
        init_fn=fake_init,
        run_fn=fake_run,
    )
    hisim_main_after = sys.modules.get("hisim.hisim_main")

    # If hisim_main was already imported in this process (e.g. by another test),
    # that's fine — what matters is that run_single did not *newly* import it for
    # a fully-injected call. We assert the module dict reference is unchanged.
    assert hisim_main_after is hisim_main_before
