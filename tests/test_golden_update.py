"""Unit tests for the config-driven ``scripts/golden_update.py``.

``main()`` loads ``golden_config.json``, runs every ``(setup, parameter_set)``
pair via an injectable ``run_fn``, and writes a ``manifest.json`` into
``results/golden_references/``. All tests inject a fake ``run_fn`` that returns
synthetic :class:`RunResult` lists — no HiSim simulation is executed and no
real file I/O happens beyond ``tmp_path``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.golden_update import main
from scripts.runner import ArtifactEntry, GoldenConfig, RunResult, load_manifest

pytestmark = pytest.mark.base


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _minimal_config_dict() -> dict:
    """A minimal valid config dict with one setup and one parameter set."""
    return {
        "results_root": "results/",
        "golden_subdir": "golden_references",
        "check_subdir": "golden-ref-check",
        "setups": [
            {"id": "setup_a", "path": "system_setups/simple_system_setup_one.py"},
        ],
        "parameter_sets": [
            {
                "id": "ps_a",
                "factory": "one_day_only",
                "year": 2021,
                "seconds_per_timestep": 60,
                "post_processing_options": ["COMPUTE_KPIS", "EXPORT_TO_CSV"],
            },
        ],
    }


def _write_config(tmp_path: Path, data: dict | None = None) -> Path:
    """Write a config JSON to ``tmp_path/config.json`` and return the path."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data if data is not None else _minimal_config_dict()))
    return p


def _fake_run_result(
    setup_id: str = "setup_a",
    param_id: str = "ps_a",
    n_artifacts: int = 2,
    error: str | None = None,
) -> RunResult:
    """Build a synthetic :class:`RunResult` with ``n_artifacts`` dummy entries."""
    artifacts = [
        ArtifactEntry(
            relative_path=f"file_{i}.csv",
            sha256="0" * 64,
            size=100 + i,
            kind="csv",
        )
        for i in range(n_artifacts)
    ]
    return RunResult(
        setup_id=setup_id,
        parameter_set_id=param_id,
        result_directory=f"results/golden_references/{setup_id}/{param_id}",
        artifacts=artifacts,
        error=error,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_main_returns_zero_with_valid_config(tmp_path: Path) -> None:
    """``main`` returns ``0`` and writes a manifest when the config is valid."""
    config_path = _write_config(tmp_path)

    def fake_run_fn(
        config: GoldenConfig, results_root: Path, repo_root: Path, subdir: str
    ) -> list[RunResult]:
        return [_fake_run_result()]

    rc = main(
        config_path=config_path,
        results_root=tmp_path,
        repo_root=tmp_path,
        run_fn=fake_run_fn,
    )
    assert rc == 0


def test_main_writes_manifest_json(tmp_path: Path) -> None:
    """``main`` writes ``manifest.json`` under ``results_root/golden_subdir/``."""
    config_path = _write_config(tmp_path)
    injected = [_fake_run_result(n_artifacts=2)]

    def fake_run_fn(
        config: GoldenConfig, results_root: Path, repo_root: Path, subdir: str
    ) -> list[RunResult]:
        return injected

    rc = main(
        config_path=config_path,
        results_root=tmp_path,
        repo_root=tmp_path,
        run_fn=fake_run_fn,
    )
    assert rc == 0

    manifest_path = tmp_path / "golden_references" / "manifest.json"
    assert manifest_path.is_file()

    manifest = load_manifest(manifest_path)
    assert len(manifest.pairs) == 1
    pair = manifest.pairs[0]
    assert pair.setup_id == "setup_a"
    assert pair.parameter_set_id == "ps_a"
    assert pair.error is None
    assert len(pair.artifacts) == 2
    assert pair.artifacts[0].relative_path == "file_0.csv"
    assert pair.artifacts[1].relative_path == "file_1.csv"


def test_main_fails_hard_on_missing_config(tmp_path: Path) -> None:
    """A nonexistent ``config_path`` raises :exc:`FileNotFoundError`."""
    missing = tmp_path / "nonexistent.json"
    assert not missing.exists()

    def fake_run_fn(
        config: GoldenConfig, results_root: Path, repo_root: Path, subdir: str
    ) -> list[RunResult]:  # pragma: no cover - should never be called
        raise AssertionError("run_fn must not be called when config is missing")

    with pytest.raises(FileNotFoundError):
        main(
            config_path=missing,
            results_root=tmp_path,
            repo_root=tmp_path,
            run_fn=fake_run_fn,
        )


def test_main_overwrites_stale_manifest(tmp_path: Path) -> None:
    """``main`` never reads a previous snapshot — it overwrites a stale manifest."""
    config_path = _write_config(tmp_path)
    golden_root = tmp_path / "golden_references"
    golden_root.mkdir(parents=True)
    stale_path = golden_root / "manifest.json"
    stale_content = json.dumps({"stale": "this should be gone"})
    stale_path.write_text(stale_content)
    assert stale_path.read_text() == stale_content

    def fake_run_fn(
        config: GoldenConfig, results_root: Path, repo_root: Path, subdir: str
    ) -> list[RunResult]:
        return [_fake_run_result()]

    rc = main(
        config_path=config_path,
        results_root=tmp_path,
        repo_root=tmp_path,
        run_fn=fake_run_fn,
    )
    assert rc == 0

    # The stale content is gone — the file is now a valid manifest.
    text = stale_path.read_text()
    assert "stale" not in text
    manifest = load_manifest(stale_path)
    assert len(manifest.pairs) == 1
    assert manifest.pairs[0].setup_id == "setup_a"


def test_main_passes_golden_subdir_to_run_fn(tmp_path: Path) -> None:
    """``main`` forwards ``config.golden_subdir`` as the ``subdir`` argument."""
    config_path = _write_config(tmp_path)
    recorded: dict[str, str] = {}

    def recording_run_fn(
        config: GoldenConfig, results_root: Path, repo_root: Path, subdir: str
    ) -> list[RunResult]:
        recorded["subdir"] = subdir
        return [_fake_run_result()]

    rc = main(
        config_path=config_path,
        results_root=tmp_path,
        repo_root=tmp_path,
        run_fn=recording_run_fn,
    )
    assert rc == 0
    assert recorded["subdir"] == "golden_references"


def test_main_passes_results_root_and_repo_root_to_run_fn(tmp_path: Path) -> None:
    """``main`` forwards ``results_root`` and ``repo_root`` unchanged to ``run_fn``."""
    config_path = _write_config(tmp_path)
    results_root = tmp_path / "outputs"
    repo_root = tmp_path / "repo"
    results_root.mkdir()
    repo_root.mkdir()
    recorded: dict[str, Path] = {}

    def recording_run_fn(
        config: GoldenConfig, results_root: Path, repo_root: Path, subdir: str
    ) -> list[RunResult]:
        recorded["results_root"] = results_root
        recorded["repo_root"] = repo_root
        return [_fake_run_result()]

    rc = main(
        config_path=config_path,
        results_root=results_root,
        repo_root=repo_root,
        run_fn=recording_run_fn,
    )
    assert rc == 0
    assert recorded["results_root"] == results_root
    assert recorded["repo_root"] == repo_root

    # The manifest lands under results_root/golden_subdir.
    assert (results_root / "golden_references" / "manifest.json").is_file()


def test_main_handles_run_result_with_error(tmp_path: Path) -> None:
    """A ``RunResult`` with ``error`` set does not crash ``main`` and is recorded."""
    config_path = _write_config(tmp_path)
    errored = _fake_run_result(error="Traceback (most recent call last):\n  boom")

    def fake_run_fn(
        config: GoldenConfig, results_root: Path, repo_root: Path, subdir: str
    ) -> list[RunResult]:
        return [errored]

    rc = main(
        config_path=config_path,
        results_root=tmp_path,
        repo_root=tmp_path,
        run_fn=fake_run_fn,
    )
    assert rc == 0

    manifest = load_manifest(tmp_path / "golden_references" / "manifest.json")
    assert len(manifest.pairs) == 1
    assert manifest.pairs[0].error is not None
    assert "boom" in manifest.pairs[0].error


def test_main_prints_summary_with_pair_count_and_manifest_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main`` prints a summary mentioning the pair count and manifest path."""
    config_path = _write_config(tmp_path)

    def fake_run_fn(
        config: GoldenConfig, results_root: Path, repo_root: Path, subdir: str
    ) -> list[RunResult]:
        return [_fake_run_result(), _fake_run_result(setup_id="setup_b", param_id="ps_b")]

    rc = main(
        config_path=config_path,
        results_root=tmp_path,
        repo_root=tmp_path,
        run_fn=fake_run_fn,
    )
    assert rc == 0

    out = capsys.readouterr().out
    assert "2 pair(s)" in out
    manifest_path = tmp_path / "golden_references" / "manifest.json"
    assert str(manifest_path) in out
    assert "succeeded" in out


def test_main_default_run_fn_is_run_all() -> None:
    """The default ``run_fn`` is :func:`scripts.runner.run_all`."""
    from scripts.runner import run_all as real_run_all

    defaults = main.__defaults__
    assert defaults is not None
    assert defaults[3] is real_run_all
