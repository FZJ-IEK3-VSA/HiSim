"""Unit tests for ``scripts/golden_check.py``.

``main`` is config-driven and compares fresh KPIs (from an injected ``run_fn``)
against committed golden files under a ``tmp_path`` golden dir. No HiSim runs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.golden_check import golden_filename, main
from scripts.runner import GoldenConfig, RunResult

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


def _read_report(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "golden-ref-check" / "report.json").read_text())


def test_pass_when_kpis_match(tmp_path: Path) -> None:
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


def test_missing_golden_bails_before_running(tmp_path: Path) -> None:
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


def test_setup_param_filter_narrows_to_one_pair(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    _write_golden(golden_dir, {"a": 1.0})

    rc = main(
        config_path=config_path, golden_dir=golden_dir, results_root=tmp_path,
        repo_root=tmp_path, setup_id="setup_a", param_id="one_week_60s",
        run_fn=_run_fn({"a": 1.0}),
    )
    assert rc == 0
