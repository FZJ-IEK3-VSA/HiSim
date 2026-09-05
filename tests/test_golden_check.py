"""Unit tests for ``scripts/golden_check.py``.

``main`` is config-driven and compares fresh KPIs (from an injected ``run_fn``)
against committed golden files under a ``tmp_path`` golden dir. No HiSim runs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.golden_check import PORT_NAMED_KPIS, _parse_args, golden_filename, main
from scripts.runner import GoldenConfig, RunResult, select_pairs

pytestmark = pytest.mark.base


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
    _write_golden(golden_dir, {"a": 1.0, "b": 2.0})

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=_run_fn({"a": 1.0, "b": 2.0}),
    )
    assert rc == 0
    report = _read_report(tmp_path)
    assert report["passed"] is True
    assert report["pairs"][0]["status"] == "pass"


def test_fail_when_kpi_diverges(tmp_path: Path) -> None:
    """A diverged KPI yields rc 1, a failing report, and recorded deviations."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, {"a": 1.0})

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=_run_fn({"a": 2.0}),
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
    _write_golden(golden_dir, {"a": 1.0, "EMS.Priority for Input_Battery_AcBatteryPowerUsed_4": 2.0})
    run_fn = _run_fn({"a": 1.0, "EMS.Priority for battery_power": 2.0})

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
    _write_golden(golden_dir, {"a": 1.0})

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
    _write_golden(golden_dir, {"a": 1.0})

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=_run_fn({"a": 999.0}),
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
    _write_golden(golden_dir, {"a": 1.0})

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, run_fn=_run_fn({"a": 2.0}), advisory=True,
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
        repo_root=tmp_path, run_fn=_run_fn({"a": 1.0}), advisory=True,
    )
    assert rc == 0
    report = _read_report(tmp_path)
    assert report["pairs"][0]["status"] == "missing_golden"


def test_cli_mode_and_advisory_flags() -> None:
    """``--mode json`` and ``--advisory`` parse; defaults stay python/blocking."""
    default = _parse_args([])
    assert default.mode == "python"
    assert default.advisory is False
    parsed = _parse_args(["--mode", "json", "--advisory"])
    assert parsed.mode == "json"
    assert parsed.advisory is True
    assert _parse_args(["--mode", "yaml"]).mode == "yaml"


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
    kpis = {"a": 1.0}
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
