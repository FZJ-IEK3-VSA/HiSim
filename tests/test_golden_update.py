"""Unit tests for ``scripts/golden_update.py``.

``main`` runs the (filtered) pairs via an injected ``run_fn`` and writes one golden
file per pair plus an informational manifest. No HiSim simulation runs and no I/O
happens outside ``tmp_path``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.golden_update import golden_filename, main
from scripts.runner import GoldenConfig, RunResult

pytestmark = pytest.mark.base


def _config_dict() -> dict:
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
            }
        ],
    }


def _write_config(tmp_path: Path) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(_config_dict()))
    return p


def _run_fn_returning(results: list[RunResult]):
    def fake(_config: GoldenConfig, _results_root: Path, _repo_root: Path, _subdir: str) -> list[RunResult]:
        return results
    return fake


def test_main_writes_golden_file_and_manifest(tmp_path: Path) -> None:
    """A successful run writes one golden file per pair and a manifest with the commit."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    kpis = {"BUI1.General.x": 1.0, "BUI1.Battery.y": 2.5}
    run_fn = _run_fn_returning(
        [RunResult("setup_a", "one_week_60s", "rd", kpis=kpis)]
    )

    rc = main(
        config_path=config_path,
        golden_dir=golden_dir,
        results_root=tmp_path,
        repo_root=tmp_path,
        run_fn=run_fn,
    )
    assert rc == 0

    golden_path = golden_dir / golden_filename("setup_a", "one_week_60s")
    assert golden_path.is_file()
    assert json.loads(golden_path.read_text()) == kpis

    manifest = json.loads((golden_dir / "manifest.json").read_text())
    assert manifest["golden_files"] == ["setup_a__one_week_60s.json"]
    assert "hisim_commit" in manifest


def test_main_errored_pair_returns_1_and_writes_no_golden(tmp_path: Path) -> None:
    """An errored pair returns rc 1 and writes no golden, but still writes a manifest."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    run_fn = _run_fn_returning(
        [RunResult("setup_a", "one_week_60s", "rd", kpis={}, error="Traceback: boom")]
    )

    rc = main(
        config_path=config_path,
        golden_dir=golden_dir,
        results_root=tmp_path,
        repo_root=tmp_path,
        run_fn=run_fn,
    )
    assert rc == 1
    assert not (golden_dir / golden_filename("setup_a", "one_week_60s")).exists()
    # Manifest is still written (lists zero goldens).
    assert (golden_dir / "manifest.json").is_file()


def test_manifest_only_mode_scans_existing_goldens(tmp_path: Path) -> None:
    """Manifest-only mode lists existing goldens on disk without running simulations."""
    config_path = _write_config(tmp_path)
    golden_dir = tmp_path / "golden_references"
    golden_dir.mkdir()
    (golden_dir / "setup_a__one_week_60s.json").write_text("{}")
    (golden_dir / "setup_b__full_year_60s.json").write_text("{}")

    def run_fn_must_not_run(*_a, **_k):  # pragma: no cover
        raise AssertionError("manifest-only must not run simulations")

    rc = main(
        config_path=config_path,
        golden_dir=golden_dir,
        results_root=tmp_path,
        repo_root=tmp_path,
        manifest_only=True,
        run_fn=run_fn_must_not_run,
    )
    assert rc == 0
    manifest = json.loads((golden_dir / "manifest.json").read_text())
    assert manifest["golden_files"] == [
        "setup_a__one_week_60s.json",
        "setup_b__full_year_60s.json",
    ]


def test_main_missing_config_raises(tmp_path: Path) -> None:
    """A missing config path raises ``FileNotFoundError``."""
    with pytest.raises(FileNotFoundError):
        main(
            config_path=tmp_path / "nope.json",
            golden_dir=tmp_path / "g",
            results_root=tmp_path,
            repo_root=tmp_path,
            run_fn=_run_fn_returning([]),
        )
