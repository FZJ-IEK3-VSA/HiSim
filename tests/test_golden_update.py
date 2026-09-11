"""Unit tests for ``scripts/golden_update.py``.

``main`` runs the (filtered) pairs via an injected ``run_fn`` and writes one golden
file per pair plus an informational manifest, keeping every stored value the gate
would still accept and never dropping a key. No HiSim simulation runs and no I/O
happens outside ``tmp_path``.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import pytest

from scripts.golden_update import (
    MAX_NAMED_KEYS,
    MergeRecord,
    golden_filename,
    main,
    merge_into_golden,
    summarize_record,
)
from scripts.runner import GoldenConfig, RunResult

pytestmark = pytest.mark.base


def _config_dict() -> dict[str, Any]:
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


def _run_fn_returning(results: list[RunResult]) -> Callable[[GoldenConfig, Path, Path, str], list[RunResult]]:
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

    def run_fn_must_not_run(*_a: Any, **_k: Any) -> NoReturn:  # pragma: no cover
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


# --------------------------------------------------------------------------- #
# Sticky bless: values the gate would accept stay exactly as committed
# --------------------------------------------------------------------------- #
def _write_existing_golden(golden_dir: Path, kpis: dict[str, Any]) -> Path:
    """Write a golden for the ``setup_a``/``one_week_60s`` pair the way the script does."""
    golden_dir.mkdir(parents=True, exist_ok=True)
    path = golden_dir / golden_filename("setup_a", "one_week_60s")
    path.write_text(json.dumps(kpis, indent=2, sort_keys=True))
    return path


def _bless(tmp_path: Path, golden_dir: Path, kpis: dict[str, Any], force_rewrite: bool = False) -> int:
    """Run one fake pair through ``main`` and return its exit code."""
    return main(
        config_path=_write_config(tmp_path),
        golden_dir=golden_dir,
        results_root=tmp_path,
        repo_root=tmp_path,
        force_rewrite=force_rewrite,
        run_fn=_run_fn_returning([RunResult("setup_a", "one_week_60s", "rd", kpis=kpis)]),
    )


def test_value_within_tolerance_keeps_the_stored_one_and_the_file_is_not_rewritten(tmp_path: Path) -> None:
    """Container float noise (1e-13 relative) never reaches the golden or its mtime."""
    golden_dir = tmp_path / "golden_references"
    stored = {"BUI1.Battery.Energy": 2589.664650339977}
    path = _write_existing_golden(golden_dir, stored)
    before_text, before_mtime = path.read_text(), path.stat().st_mtime_ns

    assert _bless(tmp_path, golden_dir, {"BUI1.Battery.Energy": 2589.664650339977 * (1 + 1e-13)}) == 0

    assert path.read_text() == before_text
    assert path.stat().st_mtime_ns == before_mtime
    assert json.loads(path.read_text()) == stored


def test_value_beyond_tolerance_is_replaced_and_the_file_is_rewritten(tmp_path: Path) -> None:
    """A KPI that genuinely moved takes the fresh value; one within tolerance is kept."""
    golden_dir = tmp_path / "golden_references"
    path = _write_existing_golden(golden_dir, {"BUI1.Battery.Energy": 1000.0, "BUI1.General.x": 1.0})

    assert _bless(tmp_path, golden_dir, {"BUI1.Battery.Energy": 1001.0, "BUI1.General.x": 1.0}) == 0

    assert json.loads(path.read_text()) == {"BUI1.Battery.Energy": 1001.0, "BUI1.General.x": 1.0}


def test_a_new_key_is_added_and_an_absent_key_is_kept(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A KPI the run gained is stored; one it no longer produces stays and is named."""
    golden_dir = tmp_path / "golden_references"
    path = _write_existing_golden(golden_dir, {"BUI1.General.x": 1.0, "BUI1.Retired.y": 2.0})

    assert _bless(tmp_path, golden_dir, {"BUI1.General.x": 1.0, "BUI1.Fresh.z": 3.0}) == 0

    assert json.loads(path.read_text()) == {
        "BUI1.General.x": 1.0,
        "BUI1.Fresh.z": 3.0,
        "BUI1.Retired.y": 2.0,  # kept: only --force-rewrite retires a KPI
    }
    assert "BUI1.Retired.y" in capsys.readouterr().out


def test_force_rewrite_writes_the_fresh_values_verbatim(tmp_path: Path) -> None:
    """``--force-rewrite`` clears the stored noise, and drops what the run no longer produces."""
    golden_dir = tmp_path / "golden_references"
    path = _write_existing_golden(golden_dir, {"BUI1.Battery.Energy": 2589.664650339977, "BUI1.Retired.y": 2.0})
    fresh = {"BUI1.Battery.Energy": 2589.664650339977 * (1 + 1e-13)}

    assert _bless(tmp_path, golden_dir, fresh, force_rewrite=True) == 0

    assert json.loads(path.read_text()) == fresh


def test_a_value_identical_but_differently_formatted_golden_is_left_alone(tmp_path: Path) -> None:
    """Stickiness is decided on values, so hand formatting survives a bless untouched."""
    golden_dir = tmp_path / "golden_references"
    golden_dir.mkdir()
    path = golden_dir / golden_filename("setup_a", "one_week_60s")
    path.write_text('{"BUI1.General.x": 1.0,\n  "BUI1.Battery.Energy":    2.5}')
    before_text, before_mtime = path.read_text(), path.stat().st_mtime_ns

    assert _bless(tmp_path, golden_dir, {"BUI1.General.x": 1.0, "BUI1.Battery.Energy": 2.5}) == 0

    assert path.read_text() == before_text
    assert path.stat().st_mtime_ns == before_mtime


def test_a_string_kpi_is_kept_when_equal_and_moves_when_it_differs(tmp_path: Path) -> None:
    """Non-numeric KPIs are compared exactly, like the gate compares them."""
    golden_dir = tmp_path / "golden_references"
    path = _write_existing_golden(golden_dir, {"BUI1.General.code": "DE.N.SFH.05"})

    assert _bless(tmp_path, golden_dir, {"BUI1.General.code": "DE.N.SFH.05"}) == 0
    assert json.loads(path.read_text()) == {"BUI1.General.code": "DE.N.SFH.05"}

    assert _bless(tmp_path, golden_dir, {"BUI1.General.code": "DE.N.SFH.06"}) == 0
    assert json.loads(path.read_text()) == {"BUI1.General.code": "DE.N.SFH.06"}


def test_a_near_zero_kpi_moves_because_the_gate_allows_no_absolute_slack(tmp_path: Path) -> None:
    """``ABS_TOL`` is 0.0, so 0.0 -> 1e-15 is a move the gate would fail on."""
    golden_dir = tmp_path / "golden_references"
    path = _write_existing_golden(golden_dir, {"BUI1.General.x": 0.0})

    assert _bless(tmp_path, golden_dir, {"BUI1.General.x": 1e-15}) == 0

    assert json.loads(path.read_text()) == {"BUI1.General.x": 1e-15}


# --------------------------------------------------------------------------- #
# The merge record itself
# --------------------------------------------------------------------------- #
def test_merge_record_names_what_moved_appeared_and_went_absent() -> None:
    """One merge, one record: moved, new, absent (kept) and the within-tolerance count."""
    record = merge_into_golden(
        {"kept": 1.0, "moved": 10.0, "gone": 5.0},
        {"kept": 1.0 * (1 + 1e-13), "moved": 11.0, "fresh": 3.0},
    )

    assert record.merged == {"kept": 1.0, "moved": 11.0, "fresh": 3.0, "gone": 5.0}
    assert record.moved == ("moved",)
    assert record.new == ("fresh",)
    assert record.absent == ("gone",)
    assert record.kept == 1
    assert record.changed is True


def test_a_merge_that_only_kept_values_is_unchanged() -> None:
    """Nothing moved, nothing appeared, nothing absent — one word."""
    record = merge_into_golden({"kept": 1.0}, {"kept": 1.0 * (1 + 1e-13)})

    assert record.merged == {"kept": 1.0}
    assert (record.moved, record.new, record.absent, record.kept) == ((), (), (), 1)
    assert record.changed is False
    assert summarize_record(record) == "unchanged"


def test_a_summary_names_every_absent_key_even_past_the_naming_cap() -> None:
    """Moved keys stop being listed when there are many; absent ones never do."""
    many = tuple(f"moved_{i}" for i in range(MAX_NAMED_KEYS + 1))
    record = MergeRecord(merged={}, moved=many, new=(), absent=("gone",), kept=7)

    line = summarize_record(record)

    assert line.startswith(f"{len(many)} moved, 0 new, 1 absent (kept), 7 within tolerance kept")
    assert "absent: gone" in line
    assert "moved_0" not in line


# --------------------------------------------------------------------------- #
# A golden that cannot be merged onto fails its pair
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "stored",
    [
        pytest.param('{"BUI1.General.x": 1.0', id="truncated_json"),
        pytest.param("[1, 2, 3]", id="json_list"),
        pytest.param('{"BUI1": {"General": {"x": 1.0}}}', id="nested_not_flat"),
    ],
)
def test_an_unusable_golden_errors_the_pair_and_leaves_the_file_alone(tmp_path: Path, stored: str) -> None:
    """Merging onto nonsense would produce nonsense, so the pair fails like a failed run."""
    golden_dir = tmp_path / "golden_references"
    golden_dir.mkdir()
    path = golden_dir / golden_filename("setup_a", "one_week_60s")
    path.write_text(stored)

    assert _bless(tmp_path, golden_dir, {"BUI1.General.x": 1.0}) == 1

    assert path.read_text() == stored


def test_force_rewrite_repairs_an_unusable_golden(tmp_path: Path) -> None:
    """The sanctioned repair path: ``--force-rewrite`` never reads the broken file."""
    golden_dir = tmp_path / "golden_references"
    golden_dir.mkdir()
    path = golden_dir / golden_filename("setup_a", "one_week_60s")
    path.write_text('{"BUI1.General.x": 1.0')

    assert _bless(tmp_path, golden_dir, {"BUI1.General.x": 1.0}, force_rewrite=True) == 0

    assert json.loads(path.read_text()) == {"BUI1.General.x": 1.0}
