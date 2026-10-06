"""Unit tests for ``scripts/golden_check.py``.

``main`` is config-driven and compares fresh KPIs (from an injected ``run_fn``)
against committed golden files under a ``tmp_path`` golden dir. No HiSim runs.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

import pytest

from scripts.golden_check import PORT_NAMED_KPIS, _parse_args, check_modes, golden_filename, main
from scripts.runner import GoldenConfig, RunResult, run_all, select_pairs
from tests.golden_leaf_factory import derived, general, leaf_map

pytestmark = pytest.mark.base

REPO_ROOT = Path(__file__).resolve().parent.parent


def _config_dict(nondeterministic: bool = False) -> dict:
    return {
        "check_subdir": "golden-ref-check",
        "setups": [{"id": "setup_a", "path": "system_setups/simple_system_setup_one.py"}],
        "parameter_sets": [
            {
                "id": "one_week_60s",
                "factory": "one_week_only",
                "year": 2021,
                "seconds_per_timestep": 60,
                "post_processing_options": ["COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"],
                "nondeterministic": nondeterministic,
            }
        ],
    }


def _write_config(tmp_path: Path, nondeterministic: bool = False) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(_config_dict(nondeterministic)))
    return p


def _write_golden(golden_dir: Path, kpis: dict) -> None:
    golden_dir.mkdir(parents=True, exist_ok=True)
    (golden_dir / golden_filename("setup_a", "one_week_60s")).write_text(json.dumps(kpis))


def _run_fn(kpis: dict, error: str | None = None):
    def fake(_config: GoldenConfig, _results_root: Path, _repo_root: Path, _subdir: str) -> list[RunResult]:
        return [RunResult("setup_a", "one_week_60s", "rd", kpis=kpis, error=error)]
    return fake


def _config_aware_run_fn(kpis: dict, error: str | None = None):
    """A ``run_fn`` returning a ``RunResult`` for every pair in ``select_pairs(config)``.

    Unlike ``_run_fn`` (which ignores its ``config`` argument and always returns one
    hardcoded pair), this honors the config's filtered pair set so that
    setup/param narrowing is observable in the report.
    """
    def fake(config: GoldenConfig, _results_root: Path, _repo_root: Path, _subdir: str) -> list[RunResult]:
        return [
            RunResult(setup.id, param.id, "rd", kpis=kpis, error=error)
            for setup, param in select_pairs(config)
        ]
    return fake


def _read_report(tmp_path: Path) -> dict:
    report: dict = json.loads((tmp_path / "golden-ref-check" / "report.json").read_text())
    return report


def test_pass_when_kpis_match(tmp_path: Path) -> None:
    """Matching fresh and golden KPIs yield rc 0 and a passing report."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, general({"a": 1.0, "b": 2.0}))

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=_run_fn(general({"a": 1.0, "b": 2.0})),
    )
    assert rc == 0
    report = _read_report(tmp_path)
    assert report["passed"] is True
    assert report["pairs"][0]["status"] == "pass"


def test_fail_when_kpi_diverges(tmp_path: Path) -> None:
    """A diverged KPI yields rc 1, a failing report, and recorded deviations."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, general({"a": 1.0}))

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=_run_fn(general({"a": 2.0})),
    )
    assert rc == 1
    report = _read_report(tmp_path)
    assert report["passed"] is False
    assert report["pairs"][0]["status"] == "fail"
    assert report["pairs"][0]["deviations"]


def test_port_named_kpis_are_excluded_from_both_sides_in_yaml_mode(tmp_path: Path) -> None:
    """The declared port-named KPI family is dropped from run and reference alike.

    The legacy path names the EMS priority KPI after ``Input_<source>_<field>_<n>``, the
    declarative path after the aggregator input's own name (C-P3.2), so the two differ by
    name while their values agree. With the exclusion the pair passes; without it, the
    same data fails on the missing and new names -- which pins that the exclusion is
    load-bearing and exactly as wide as the family.
    """
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    ems = "Energy Management System"
    _write_golden(
        golden_dir,
        leaf_map(derived("a", 1.0), derived("Priority for Input_Battery_AcBatteryPowerUsed_4", 2.0, tag=ems)),
    )
    run_fn = _run_fn(leaf_map(derived("a", 1.0), derived("Priority for battery_power", 2.0, tag=ems)))

    without_exclusion = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=run_fn,
    )
    with_exclusion = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=run_fn, ignore_kpis=PORT_NAMED_KPIS,
    )

    assert without_exclusion == 1
    assert with_exclusion == 0


def test_missing_golden_bails_before_running(tmp_path: Path) -> None:
    """A missing golden file fails fast without invoking the run function."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"  # nothing written

    def run_fn_must_not_run(*_a, **_k):  # pragma: no cover
        raise AssertionError("must not run simulations when a golden is missing")

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=run_fn_must_not_run,
    )
    assert rc == 1
    report = _read_report(tmp_path)
    assert report["pairs"][0]["status"] == "missing_golden"


def test_run_error_is_failure(tmp_path: Path) -> None:
    """A run that reports an error is surfaced as a ``run_error`` failure."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, general({"a": 1.0}))

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=_run_fn({}, error="Traceback: boom"),
    )
    assert rc == 1
    report = _read_report(tmp_path)
    assert report["pairs"][0]["status"] == "run_error"


def test_nondeterministic_mismatch_is_advisory_not_failure(tmp_path: Path) -> None:
    """A mismatch on a nondeterministic pair is advisory, still passing overall."""
    config_path = _write_config(tmp_path, nondeterministic=True)
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, general({"a": 1.0}))

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=_run_fn(general({"a": 999.0})),
    )
    assert rc == 0
    report = _read_report(tmp_path)
    assert report["passed"] is True
    assert report["pairs"][0]["status"] == "advisory"
    assert report["pairs"][0]["deviations"]  # still recorded


def test_advisory_divergence_returns_zero_but_reports_failure(tmp_path: Path) -> None:
    """In advisory mode a real divergence is recorded but the exit code is forced to 0."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, general({"a": 1.0}))

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=_run_fn(general({"a": 2.0})), advisory=True,
    )
    assert rc == 0
    report = _read_report(tmp_path)
    assert report["passed"] is False  # the report still tells the truth
    assert report["pairs"][0]["status"] == "fail"


def test_advisory_missing_golden_returns_zero(tmp_path: Path) -> None:
    """In advisory mode a missing golden is reported without blocking (rc 0)."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"  # nothing written

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=_run_fn(general({"a": 1.0})), advisory=True,
    )
    assert rc == 0
    report = _read_report(tmp_path)
    assert report["pairs"][0]["status"] == "missing_golden"


def test_cli_mode_and_advisory_flags() -> None:
    """``--mode yaml`` and ``--advisory`` parse; defaults stay python/blocking."""
    default = _parse_args([])
    assert default.mode == "python"
    assert default.advisory is False
    parsed = _parse_args(["--mode", "yaml", "--advisory"])
    assert parsed.mode == "yaml"
    assert parsed.advisory is True
    # 'json' was the third mode until the v1 scenario files retired; it is refused now.
    with pytest.raises(SystemExit):
        _parse_args(["--mode", "json"])


def test_setup_param_filter_narrows_to_one_pair(tmp_path: Path) -> None:
    """The setup/param filter narrows the run to the single selected pair.

    Uses a config with two parameter sets and a ``run_fn`` that honors
    ``select_pairs(config)`` so narrowing is observable: unfiltered, both pairs
    appear in the report; filtered to ``setup_a``/``one_week_60s``, only that
    pair survives and the other is absent.
    """
    config = _config_dict()
    config["parameter_sets"].append({
        "id": "one_day_60s",
        "factory": "one_day_only",
        "year": 2021,
        "seconds_per_timestep": 60,
        "post_processing_options": ["COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"],
        "nondeterministic": False,
    })
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))

    golden_dir = tmp_path / "golden_references"
    kpis = general({"a": 1.0})
    _write_golden(golden_dir, kpis)
    (golden_dir / golden_filename("setup_a", "one_day_60s")).write_text(json.dumps(kpis))
    run_fn = _config_aware_run_fn(kpis)

    # Without the filter, both pairs run and appear in the report — this confirms
    # the config genuinely holds two pairs and run_fn returns both, so the filter
    # is the only thing that could narrow the result below.
    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=run_fn,
    )
    assert rc == 0
    report = _read_report(tmp_path)
    assert len(report["pairs"]) == 2
    pair_ids = {(p["setup_id"], p["parameter_set_id"]) for p in report["pairs"]}
    assert pair_ids == {("setup_a", "one_week_60s"), ("setup_a", "one_day_60s")}

    # With the filter, only the selected pair survives.
    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, setup_id="setup_a", param_id="one_week_60s",
        run_fn=run_fn,
    )
    assert rc == 0
    report = _read_report(tmp_path)
    assert len(report["pairs"]) == 1
    assert report["pairs"][0]["setup_id"] == "setup_a"
    assert report["pairs"][0]["parameter_set_id"] == "one_week_60s"
    # The non-selected pair is absent, pinning the narrowing semantics.
    pair_ids = {(p["setup_id"], p["parameter_set_id"]) for p in report["pairs"]}
    assert ("setup_a", "one_day_60s") not in pair_ids


# --------------------------------------------------------------------------- #
# Shards: --pairs, --jobs, and the YAML mode's own directory
# --------------------------------------------------------------------------- #
def test_cli_pairs_and_jobs() -> None:
    """A CI shard's command line: its pairs as setup:param tokens and how many run at once."""
    parsed = _parse_args(["--pairs", "setup_a:one_week_60s", "setup_b:one_week_60s", "--jobs", "4"])
    assert parsed.pairs == [("setup_a", "one_week_60s"), ("setup_b", "one_week_60s")]
    assert parsed.jobs == 4
    assert _parse_args([]).jobs == 1 and _parse_args([]).pairs is None
    for wrong in (["--jobs", "0"], ["--pairs", "no_colon"]):
        with pytest.raises(SystemExit):
            _parse_args(wrong)


def test_pairs_that_fail_in_a_parallel_shard_fail_the_check_each_with_its_own_verdict(tmp_path: Path) -> None:
    """Catches a shard losing a pair's verdict: one report carries every pair, in shard order, and fails.

    ``run_all`` with ``jobs=2`` runs real child processes. The setups do not exist, so each child
    reports a run error without simulating anything; the divergence verdicts themselves are the same
    code as in a sequential run and are covered by the tests above.
    """
    config = _config_dict()
    config["setups"].append({"id": "setup_b", "path": "system_setups/does_not_exist.py"})
    config["setups"][0]["path"] = "system_setups/does_not_exist_either.py"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, general({"a": 1.0}))
    (golden_dir / golden_filename("setup_b", "one_week_60s")).write_text(json.dumps(general({"a": 1.0})))

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path, repo_root=REPO_ROOT,
        run_fn=functools.partial(run_all, jobs=2),
        pairs=[("setup_b", "one_week_60s"), ("setup_a", "one_week_60s")],
    )

    assert rc == 1
    report = _read_report(tmp_path)
    assert [(p["setup_id"], p["status"]) for p in report["pairs"]] == [
        ("setup_b", "run_error"), ("setup_a", "run_error"),
    ]
    assert all(p["duration_s"] is not None for p in report["pairs"])
    assert "[RUN_ERROR] setup_b / one_week_60s" in (tmp_path / "golden-ref-check" / "report.txt").read_text()


def test_the_yaml_mode_writes_its_own_report_beside_the_python_one(tmp_path: Path) -> None:
    """A job running both modes keeps both reports: the YAML run goes to ``<check_subdir>-yaml``."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, general({"a": 1.0}))
    seen: list[str] = []

    def run_fn(_config: GoldenConfig, results_root: Path, _repo_root: Path, subdir: str) -> list[RunResult]:
        seen.append(subdir)
        return [RunResult("setup_a", "one_week_60s", str(results_root / subdir), kpis=general({"a": 1.0}))]

    main(config_path=config_path, golden_dir=golden_dir, results_root=tmp_path, repo_root=tmp_path, run_fn=run_fn)
    main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path, repo_root=tmp_path, run_fn=run_fn,
        subdir_suffix="-yaml",
    )

    assert seen == ["golden-ref-check", "golden-ref-check-yaml"]
    assert (tmp_path / "golden-ref-check" / "report.json").exists()
    assert (tmp_path / "golden-ref-check-yaml" / "report.json").exists()


def _modes_run_fn(kpis_by_mode: dict[str, dict], seen: list):
    """A ``run_modes`` stand-in: every pair of the config, per requested mode, with that mode's KPIs."""
    def fake(config: GoldenConfig, _results_root: Path, _repo_root: Path, subdirs: dict, jobs: int) -> dict:
        seen.append((dict(subdirs), jobs))
        return {
            mode: [RunResult(s.id, p.id, "rd", kpis=kpis_by_mode[mode]) for s, p in select_pairs(config)]
            for mode in subdirs
        }
    return fake


@pytest.mark.parametrize("diverging", ["python", "yaml"])
def test_a_divergence_in_either_mode_fails_both_modes_check_and_reports_both(tmp_path: Path, diverging: str) -> None:
    """``--mode both``: one pool, one report per mode, and a divergence in either mode fails the check.

    Catches the pooled run judging the modes together (one mode's divergence landing in the other's
    report) or only one mode's verdict reaching the exit code.
    """
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, general({"a": 1.0}))
    kpis = {"python": general({"a": 1.0}), "yaml": general({"a": 1.0})}
    kpis[diverging] = general({"a": 2.0})
    seen: list = []

    rc = check_modes(
        ["python", "yaml"], config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, jobs=4, run_modes_fn=_modes_run_fn(kpis, seen),
    )

    assert rc == 1
    assert seen == [({"python": "golden-ref-check", "yaml": "golden-ref-check-yaml"}, 4)]
    python_report = json.loads((tmp_path / "golden-ref-check" / "report.json").read_text())
    yaml_report = json.loads((tmp_path / "golden-ref-check-yaml" / "report.json").read_text())
    assert python_report["passed"] is (diverging != "python")
    assert yaml_report["passed"] is (diverging != "yaml")


def test_both_modes_pass_together(tmp_path: Path) -> None:
    """Both modes matching is exit 0, with both reports written."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, general({"a": 1.0}))
    rc = check_modes(
        ["python", "yaml"], config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path,
        run_modes_fn=_modes_run_fn({"python": general({"a": 1.0}), "yaml": general({"a": 1.0})}, []),
    )
    assert rc == 0
    assert (tmp_path / "golden-ref-check-yaml" / "report.txt").read_text().startswith("GOLDEN CHECK OK")


def test_a_missing_golden_fails_both_modes_before_running(tmp_path: Path) -> None:
    """Both reports name the missing golden, and nothing runs."""
    config_path = _write_config(tmp_path)
    seen: list = []
    rc = check_modes(
        ["python", "yaml"], config_path=config_path, golden_dir=tmp_path / "none", results_root=tmp_path,
        repo_root=tmp_path, run_modes_fn=_modes_run_fn({}, seen),
    )
    assert rc == 1 and not seen
    for subdir in ("golden-ref-check", "golden-ref-check-yaml"):
        assert json.loads((tmp_path / subdir / "report.json").read_text())["pairs"][0]["status"] == "missing_golden"


def test_cli_mode_both() -> None:
    """``--mode both`` is the golden-check shard's mode."""
    assert _parse_args(["--mode", "both"]).mode == "both"


# --------------------------------------------------------------------------- #
# The leaf form: units, and goldens the gate refuses to read
# --------------------------------------------------------------------------- #
def test_a_changed_unit_fails_as_a_unit_change_of_its_own(tmp_path: Path) -> None:
    """Catches a unit change passing the gate, or hiding among the value divergences.

    The value is the same on both sides; only the unit moved. The pair fails, the deviation is
    recorded under ``unit_changes`` (not ``deviations``), and both ``report.txt`` and the summary
    name it as a unit change.
    """
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, leaf_map(derived("a", 1.0, unit="kWh")))

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=_run_fn(leaf_map(derived("a", 1.0, unit="MWh"))),
    )

    assert rc == 1
    pair = _read_report(tmp_path)["pairs"][0]
    assert pair["status"] == "fail"
    assert not pair["deviations"]
    assert pair["unit_changes"] == [
        "setup_a/one_week_60s: KPI 'BUI1.General.a' changed its unit: ref='kWh' got='MWh'"
    ]
    text = (tmp_path / "golden-ref-check" / "report.txt").read_text()
    assert text.splitlines()[0] == "GOLDEN CHECK FAILED (1 pair(s)): 1 with KPI unit changes"
    assert "    - [unit] setup_a/one_week_60s: KPI 'BUI1.General.a' changed its unit" in text


def test_a_golden_in_the_old_flat_form_is_refused_before_running(tmp_path: Path) -> None:
    """Catches the gate reading a flat ``dotted key -> value`` golden, which has no unit and no source."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, {"BUI1.General.a": 1.0})

    def run_fn_must_not_run(*_a, **_k):  # pragma: no cover
        raise AssertionError("must not run simulations when a golden is unusable")

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=run_fn_must_not_run,
    )

    assert rc == 1
    pair = _read_report(tmp_path)["pairs"][0]
    assert pair["status"] == "unusable_golden"
    assert "old flat form" in pair["deviations"][0]
    assert "golden-update workflow with force_rewrite" in pair["deviations"][0]
    assert "1 with an unusable golden reference" in (tmp_path / "golden-ref-check" / "report.txt").read_text()


def test_a_golden_whose_key_disagrees_with_its_fields_is_refused(tmp_path: Path) -> None:
    """Catches a hand-edited key (or a hand-edited field) being compared under the wrong address."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    _, leaf = derived("a", 1.0)
    _write_golden(golden_dir, {"BUI1.General.b": leaf})

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=_run_fn(general({"a": 1.0})),
    )

    assert rc == 1
    pair = _read_report(tmp_path)["pairs"][0]
    assert pair["status"] == "unusable_golden"
    assert "which address it as 'BUI1.General.a'" in pair["deviations"][0]
