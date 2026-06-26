"""Unit tests for the orchestration in ``scripts/golden_update.py``.

``main()`` regenerates golden reference files by calling a run function once per
setup and writing the result as JSON. These tests inject a fake run function, a
temporary directory, and a one-element (or small) setup list so the orchestration
can be exercised without running real HiSim simulations and without writing to the
committed ``scripts/golden_refs/`` directory.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import golden_check
from scripts.golden_update import main

pytestmark = pytest.mark.base


def _fake_run_setup(name: str) -> dict[str, float]:
    """Deterministic stand-in for ``golden_check.run_setup`` keyed on the name."""
    return {f"{name}_kpi": float(len(name))}


def test_main_returns_zero(tmp_path: Path) -> None:
    """``main`` reports success with return code 0."""
    rc = main(run_setup_fn=_fake_run_setup, ref_dir=tmp_path, setups=["only"])
    assert rc == 0


def test_main_writes_one_file_per_setup_with_name_pattern(tmp_path: Path) -> None:
    """Each setup produces exactly one ``<name>.json`` file, named after the setup."""
    setups = ["alpha", "beta_two"]
    seen: list[str] = []

    def recording_run_setup(name: str) -> dict[str, float]:
        seen.append(name)
        return _fake_run_setup(name)

    rc = main(run_setup_fn=recording_run_setup, ref_dir=tmp_path, setups=setups)

    assert rc == 0
    # called once per setup, in order
    assert seen == setups
    # exact filename pattern f"{name}.json" and nothing else
    assert sorted(p.name for p in tmp_path.iterdir()) == ["alpha.json", "beta_two.json"]


def test_main_json_format_is_indent2_sort_keys(tmp_path: Path) -> None:
    """The reference is JSON with ``indent=2`` and alphabetically sorted keys."""
    result = {"zeta": 1.0, "alpha": 2.0, "mid": 3.0}

    def run_setup_returning_unordered(name: str) -> dict[str, float]:
        assert name == "fmt"
        return result

    rc = main(run_setup_fn=run_setup_returning_unordered, ref_dir=tmp_path, setups=["fmt"])

    assert rc == 0
    text = (tmp_path / "fmt.json").read_text()
    # round-trips to the same dict
    assert json.loads(text) == result
    # matches the exact serialization golden_update uses
    assert text == json.dumps(result, indent=2, sort_keys=True)
    # keys are sorted: alpha before mid before zeta
    assert text.index('"alpha"') < text.index('"mid"') < text.index('"zeta"')
    # indent=2 -> two-space indentation on the first key line
    assert "\n  \"alpha\"" in text


def test_main_creates_ref_dir_when_missing(tmp_path: Path) -> None:
    """``main`` creates the target directory (and parents) if it does not yet exist."""
    target = tmp_path / "nested" / "deep" / "refs"
    assert not target.exists()

    rc = main(run_setup_fn=_fake_run_setup, ref_dir=target, setups=["only"])

    assert rc == 0
    assert target.is_dir()
    assert (target / "only.json").exists()


def test_main_uses_injected_run_setup_fn(tmp_path: Path) -> None:
    """The injected ``run_setup_fn`` is the one whose output lands on disk."""
    marker = {"injected": 123.0}

    def run_setup_fn(name: str) -> dict[str, float]:
        assert name == "s"
        return marker

    rc = main(run_setup_fn=run_setup_fn, ref_dir=tmp_path, setups=["s"])

    assert rc == 0
    assert json.loads((tmp_path / "s.json").read_text()) == marker


def test_main_empty_setups_writes_nothing(tmp_path: Path) -> None:
    """An empty ``setups`` list writes no files and still returns 0."""
    called: list[str] = []

    def run_setup_fn(name: str) -> dict[str, float]:  # pragma: no cover - shouldn't run
        called.append(name)
        raise AssertionError("should not be called for empty setups")

    rc = main(run_setup_fn=run_setup_fn, ref_dir=tmp_path, setups=[])

    assert rc == 0
    assert not called
    assert not list(tmp_path.iterdir())


def test_main_defaults_bind_to_golden_check_collaborators() -> None:
    """Defaults reproduce the previous behaviour: golden_check's run_setup/REF_DIR/SETUPS."""
    defaults = main.__defaults__
    assert defaults is not None
    assert len(defaults) == 3
    run_setup_fn, ref_dir, setups = defaults
    # value-equal to golden_check's module-level collaborators (identity may differ
    # when imported as a script vs a package, so compare by value)
    assert setups == golden_check.SETUPS == ["_dummy"]
    assert ref_dir == golden_check.REF_DIR
    assert callable(run_setup_fn)
