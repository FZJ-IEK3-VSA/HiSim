"""Unit tests for ``scripts/runner.py``.

Cover the config schema, SimulationParameters construction, setup-path
resolution, artifact inventory, manifest round-trip, and the ``run_all``
orchestrator with an injected fake ``run_one``.

All tests are tagged ``pytest.mark.base`` and run without executing any HiSim
simulation. Filesystem tests use the ``tmp_path`` fixture so nothing leaks into
the repository tree.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

import pytest

from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters
from scripts.runner import (
    ArtifactEntry,
    GoldenConfig,
    Manifest,
    ParameterSetConfig,
    RunResult,
    SetupConfig,
    build_simulation_parameters,
    classify_artifact,
    collect_manifest,
    compute_sha256,
    config_hash,
    inventory_directory,
    load_config,
    load_manifest,
    resolve_setup_path,
    run_all,
    run_one,
    write_manifest,
)

pytestmark = pytest.mark.base

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = REPO_ROOT / "scripts" / "golden_config.json"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _minimal_config_dict() -> dict:
    """A minimal valid config dict for round-trip tests."""
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


def _write_config(tmp_path: Path, data: dict) -> Path:
    """Write ``data`` as JSON to ``tmp_path/config.json`` and return the path."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return p


# --------------------------------------------------------------------------- #
# load_config
# --------------------------------------------------------------------------- #
def test_load_config_happy_path_round_trips_all_fields(tmp_path: Path) -> None:
    """A minimal valid config parses into the expected dataclasses."""
    cfg = load_config(_write_config(tmp_path, _minimal_config_dict()))
    assert isinstance(cfg, GoldenConfig)
    assert cfg.results_root == "results/"
    assert cfg.golden_subdir == "golden_references"
    assert cfg.check_subdir == "golden-ref-check"
    assert len(cfg.setups) == 1
    assert cfg.setups[0].id == "setup_a"
    assert cfg.setups[0].path == "system_setups/simple_system_setup_one.py"
    assert len(cfg.parameter_sets) == 1
    ps = cfg.parameter_sets[0]
    assert ps.id == "ps_a"
    assert ps.factory == "one_day_only"
    assert ps.year == 2021
    assert ps.seconds_per_timestep == 60
    assert ps.post_processing_options == ["COMPUTE_KPIS", "EXPORT_TO_CSV"]
    assert ps.nondeterministic is False


def test_load_config_nondeterministic_defaults_to_false(tmp_path: Path) -> None:
    """``nondeterministic`` is optional and defaults to ``False``."""
    data = _minimal_config_dict()
    data["parameter_sets"][0]["nondeterministic"] = True
    cfg = load_config(_write_config(tmp_path, data))
    assert cfg.parameter_sets[0].nondeterministic is True


def test_load_config_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    """A missing config path raises ``FileNotFoundError``."""
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does_not_exist.json")


def test_load_config_missing_setups_key_raises_valueerror(tmp_path: Path) -> None:
    """Missing top-level ``setups`` key is a schema violation."""
    data = _minimal_config_dict()
    del data["setups"]
    with pytest.raises(ValueError, match="setups"):
        load_config(_write_config(tmp_path, data))


def test_load_config_empty_setups_raises_valueerror(tmp_path: Path) -> None:
    """An empty ``setups`` list is a schema violation."""
    data = _minimal_config_dict()
    data["setups"] = []
    with pytest.raises(ValueError, match="setups"):
        load_config(_write_config(tmp_path, data))


def test_load_config_empty_parameter_sets_raises_valueerror(tmp_path: Path) -> None:
    """An empty ``parameter_sets`` list is a schema violation."""
    data = _minimal_config_dict()
    data["parameter_sets"] = []
    with pytest.raises(ValueError, match="parameter_sets"):
        load_config(_write_config(tmp_path, data))


def test_load_config_unknown_factory_raises_valueerror(tmp_path: Path) -> None:
    """An unknown ``factory`` name is a schema violation."""
    data = _minimal_config_dict()
    data["parameter_sets"][0]["factory"] = "nonexistent_factory"
    with pytest.raises(ValueError, match="nonexistent_factory"):
        load_config(_write_config(tmp_path, data))


def test_load_config_unknown_option_raises_valueerror(tmp_path: Path) -> None:
    """An unknown ``PostProcessingOptions`` member name is a schema violation."""
    data = _minimal_config_dict()
    data["parameter_sets"][0]["post_processing_options"] = ["FAKE_OPTION"]
    with pytest.raises(ValueError, match="FAKE_OPTION"):
        load_config(_write_config(tmp_path, data))


def test_load_config_real_file_has_three_setups_two_param_sets() -> None:
    """The committed ``scripts/golden_config.json`` parses with 3 setups and 2 parameter sets."""
    assert REAL_CONFIG.exists(), f"Expected golden_config.json at {REAL_CONFIG}"
    cfg = load_config(REAL_CONFIG)
    assert len(cfg.setups) == 3
    assert len(cfg.parameter_sets) == 2
    for ps in cfg.parameter_sets:
        assert hasattr(SimulationParameters, ps.factory)
        assert callable(getattr(SimulationParameters, ps.factory))
        for name in ps.post_processing_options:
            assert name in PostProcessingOptions.__members__


def test_load_config_real_file_setups_exist_on_disk() -> None:
    """Every setup path in the real config resolves to an existing ``.py`` file."""
    cfg = load_config(REAL_CONFIG)
    for setup in cfg.setups:
        resolved = resolve_setup_path(setup, REPO_ROOT)
        assert resolved.exists()
        assert resolved.suffix == ".py"


# --------------------------------------------------------------------------- #
# build_simulation_parameters
# --------------------------------------------------------------------------- #
def test_build_simulation_parameters_sets_fields_correctly() -> None:
    """A valid ``ParameterSetConfig`` produces a correctly configured ``SimulationParameters``."""
    ps = ParameterSetConfig(
        id="test_ps",
        factory="one_day_only",
        year=2021,
        seconds_per_timestep=60,
        post_processing_options=["COMPUTE_KPIS", "EXPORT_TO_CSV"],
    )
    params = build_simulation_parameters(ps, "/tmp/test_result_dir")
    assert params.result_directory == "/tmp/test_result_dir"
    assert params.seconds_per_timestep == 60
    assert params.year == 2021
    assert params.start_date == datetime.datetime(2021, 1, 1)
    assert params.end_date == datetime.datetime(2021, 1, 2)
    assert PostProcessingOptions.COMPUTE_KPIS in params.post_processing_options
    assert PostProcessingOptions.EXPORT_TO_CSV in params.post_processing_options
    # IntEnum members compare equal to their int values.
    assert 20 in params.post_processing_options
    assert 7 in params.post_processing_options
    # Does not enable all options — only the two listed.
    assert len(params.post_processing_options) == 2


def test_build_simulation_parameters_invalid_factory_raises_valueerror() -> None:
    """An unknown factory name raises ``ValueError``."""
    ps = ParameterSetConfig(
        id="test_ps",
        factory="nonexistent",
        year=2021,
        seconds_per_timestep=60,
        post_processing_options=["COMPUTE_KPIS"],
    )
    with pytest.raises(ValueError, match="nonexistent"):
        build_simulation_parameters(ps, "/tmp/test")


def test_build_simulation_parameters_invalid_option_raises_valueerror() -> None:
    """An unknown ``PostProcessingOptions`` member name raises ``ValueError``."""
    ps = ParameterSetConfig(
        id="test_ps",
        factory="one_day_only",
        year=2021,
        seconds_per_timestep=60,
        post_processing_options=["FAKE_OPTION"],
    )
    with pytest.raises(ValueError, match="FAKE_OPTION"):
        build_simulation_parameters(ps, "/tmp/test")


# --------------------------------------------------------------------------- #
# resolve_setup_path
# --------------------------------------------------------------------------- #
def test_resolve_setup_path_finds_existing_file() -> None:
    """``resolve_setup_path`` resolves a real setup relative to the repo root."""
    setup = SetupConfig(id="simple", path="system_setups/simple_system_setup_one.py")
    resolved = resolve_setup_path(setup, REPO_ROOT)
    assert resolved.exists()
    assert resolved.suffix == ".py"
    assert resolved.name == "simple_system_setup_one.py"


def test_resolve_setup_path_missing_file_raises_filenotfound() -> None:
    """A nonexistent setup path raises ``FileNotFoundError``."""
    setup = SetupConfig(id="ghost", path="system_setups/does_not_exist.py")
    with pytest.raises(FileNotFoundError):
        resolve_setup_path(setup, REPO_ROOT)


# --------------------------------------------------------------------------- #
# classify_artifact
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "relative_path, expected",
    [
        ("foo/bar.csv", "csv"),
        ("all_kpis.json", "json"),
        ("plots/foo.png", "image"),
        ("plots/foo.jpg", "image"),
        ("plots/foo.jpeg", "image"),
        ("finished.flag", "other"),
        ("log.pkl", "other"),
        ("results/all_results.CSV", "csv"),
        ("DATA.JSON", "json"),
        ("photo.PNG", "image"),
    ],
)
def test_classify_artifact_by_extension(relative_path: str, expected: str) -> None:
    """``classify_artifact`` maps file extensions to kinds (case-insensitive)."""
    assert classify_artifact(relative_path) == expected


# --------------------------------------------------------------------------- #
# compute_sha256
# --------------------------------------------------------------------------- #
def test_compute_sha256_matches_hashlib(tmp_path: Path) -> None:
    """``compute_sha256`` returns the same digest as ``hashlib.sha256``."""
    data = b"golden-reference-test-data"
    p = tmp_path / "data.bin"
    p.write_bytes(data)
    assert compute_sha256(p) == hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# inventory_directory
# --------------------------------------------------------------------------- #
def test_inventory_directory_lists_files_sorted_with_kind_and_hash(tmp_path: Path) -> None:
    """``inventory_directory`` returns sorted ``ArtifactEntry`` objects with correct kinds."""
    (tmp_path / "a.csv").write_text("col\n1\n")
    (tmp_path / "c.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.json").write_text('{"k": 1}')
    entries = inventory_directory(tmp_path)
    assert len(entries) == 3
    assert [e.relative_path for e in entries] == ["a.csv", "c.png", "sub/b.json"]
    assert [e.kind for e in entries] == ["csv", "image", "json"]
    for e in entries:
        assert len(e.sha256) == 64
        assert e.size > 0


def test_inventory_directory_nonexistent_returns_empty_list(tmp_path: Path) -> None:
    """A nonexistent directory yields an empty list (no error)."""
    assert not inventory_directory(tmp_path / "nope")


# --------------------------------------------------------------------------- #
# config_hash
# --------------------------------------------------------------------------- #
def test_config_hash_matches_hashlib(tmp_path: Path) -> None:
    """``config_hash`` returns the SHA-256 of the file's bytes."""
    data = b'{"setups": []}'
    p = tmp_path / "config.json"
    p.write_bytes(data)
    assert config_hash(p) == hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# write_manifest / load_manifest round-trip
# --------------------------------------------------------------------------- #
def test_manifest_round_trip(tmp_path: Path) -> None:
    """A manifest with nested RunResults and ArtifactEntries round-trips exactly."""
    manifest = Manifest(
        hisim_commit="abc123",
        python_version="3.11.0",
        platform="Linux-6.0",
        config_sha256="deadbeef",
        generated_at="2024-01-01T00:00:00+00:00",
        pairs=[
            RunResult(
                setup_id="setup_a",
                parameter_set_id="ps_a",
                result_directory="/results/golden/setup_a/ps_a",
                artifacts=[
                    ArtifactEntry("all_results.csv", "sha_a1", 100, "csv"),
                    ArtifactEntry("all_kpis.json", "sha_a2", 50, "json"),
                ],
            ),
            RunResult(
                setup_id="setup_b",
                parameter_set_id="ps_b",
                result_directory="/results/golden/setup_b/ps_b",
                artifacts=[
                    ArtifactEntry("plot.png", "sha_b1", 2000, "image"),
                ],
                error="something went wrong\ntraceback here",
            ),
        ],
    )
    path = tmp_path / "manifest.json"
    write_manifest(manifest, path)
    assert path.exists()
    loaded = load_manifest(path)
    assert loaded.hisim_commit == "abc123"
    assert loaded.python_version == "3.11.0"
    assert loaded.platform == "Linux-6.0"
    assert loaded.config_sha256 == "deadbeef"
    assert loaded.generated_at == "2024-01-01T00:00:00+00:00"
    assert len(loaded.pairs) == 2
    assert loaded.pairs[0].setup_id == "setup_a"
    assert loaded.pairs[0].parameter_set_id == "ps_a"
    assert loaded.pairs[0].result_directory == "/results/golden/setup_a/ps_a"
    assert loaded.pairs[0].error is None
    assert len(loaded.pairs[0].artifacts) == 2
    assert loaded.pairs[0].artifacts[0].relative_path == "all_results.csv"
    assert loaded.pairs[0].artifacts[0].sha256 == "sha_a1"
    assert loaded.pairs[0].artifacts[0].size == 100
    assert loaded.pairs[0].artifacts[0].kind == "csv"
    assert loaded.pairs[1].setup_id == "setup_b"
    assert loaded.pairs[1].error == "something went wrong\ntraceback here"
    assert len(loaded.pairs[1].artifacts) == 1
    assert loaded.pairs[1].artifacts[0].kind == "image"


def test_load_manifest_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    """Loading a nonexistent manifest raises ``FileNotFoundError``."""
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "nope.json")


def test_write_manifest_creates_parent_dirs(tmp_path: Path) -> None:
    """``write_manifest`` creates parent directories if they don't exist."""
    manifest = Manifest(
        hisim_commit="x",
        python_version="x",
        platform="x",
        config_sha256="x",
        generated_at="x",
        pairs=[],
    )
    path = tmp_path / "nested" / "deep" / "manifest.json"
    write_manifest(manifest, path)
    assert path.exists()


# --------------------------------------------------------------------------- #
# collect_manifest
# --------------------------------------------------------------------------- #
def test_collect_manifest_assembles_fields(tmp_path: Path) -> None:
    """``collect_manifest`` populates environment metadata and the config hash."""
    config_path = _write_config(tmp_path, _minimal_config_dict())
    cfg = load_config(config_path)
    results = [
        RunResult("s1", "p1", "/tmp/r", [ArtifactEntry("a.csv", "h", 10, "csv")]),
    ]
    manifest = collect_manifest(cfg, results, config_path)
    assert manifest.python_version == __import__("platform").python_version()
    assert manifest.config_sha256 == config_hash(config_path)
    assert manifest.pairs is results
    assert manifest.generated_at  # non-empty ISO string
    # hisim_commit is best-effort; just assert it's a string.
    assert isinstance(manifest.hisim_commit, str)


# --------------------------------------------------------------------------- #
# run_all (with monkeypatched run_one — no HiSim execution)
# --------------------------------------------------------------------------- #
def test_run_all_produces_one_result_per_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``run_all`` calls ``run_one`` once per (setup, parameter_set) pair with correct paths."""
    calls: list[tuple[str, str, str]] = []

    def fake_run_one(
        setup: SetupConfig,
        parameter_set: ParameterSetConfig,
        result_directory: str,
        _repo_root: Path,
    ) -> RunResult:
        calls.append((setup.id, parameter_set.id, result_directory))
        return RunResult(setup.id, parameter_set.id, result_directory, [])

    monkeypatch.setattr("scripts.runner.run_one", fake_run_one)

    cfg = GoldenConfig(
        results_root="results/",
        golden_subdir="golden_references",
        check_subdir="golden-ref-check",
        setups=[
            SetupConfig(id="s1", path="system_setups/a.py"),
            SetupConfig(id="s2", path="system_setups/b.py"),
        ],
        parameter_sets=[
            ParameterSetConfig(
                id="p1", factory="one_day_only", year=2021, seconds_per_timestep=60,
                post_processing_options=["COMPUTE_KPIS"],
            ),
            ParameterSetConfig(
                id="p2", factory="one_day_only", year=2021, seconds_per_timestep=60,
                post_processing_options=["EXPORT_TO_CSV"],
            ),
        ],
    )
    base_root = tmp_path
    subdir = "golden_references"
    results = run_all(cfg, base_root, REPO_ROOT, subdir)

    assert len(results) == 4  # 2 setups × 2 parameter_sets
    expected = [
        ("s1", "p1", str(base_root / subdir / "s1" / "p1")),
        ("s1", "p2", str(base_root / subdir / "s1" / "p2")),
        ("s2", "p1", str(base_root / subdir / "s2" / "p1")),
        ("s2", "p2", str(base_root / subdir / "s2" / "p2")),
    ]
    assert calls == expected
    for r, (_, _, rd) in zip(results, expected):
        assert r.result_directory == rd
        assert r.artifacts == []
        assert r.error is None
    # Parent directories were created.
    for _, _, rd in expected:
        assert Path(rd).is_dir()


def test_run_all_real_config_six_pairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real ``golden_config.json`` produces 3×2 = 6 pairs."""
    monkeypatch.setattr(
        "scripts.runner.run_one",
        lambda setup, ps, rd, rr: RunResult(setup.id, ps.id, rd, []),
    )
    cfg = load_config(REAL_CONFIG)
    results = run_all(cfg, tmp_path, REPO_ROOT, cfg.golden_subdir)
    assert len(results) == 6
    setup_ids = {r.setup_id for r in results}
    assert setup_ids == {s.id for s in cfg.setups}
    param_ids = {r.parameter_set_id for r in results}
    assert param_ids == {p.id for p in cfg.parameter_sets}


# --------------------------------------------------------------------------- #
# run_one error capture (no HiSim execution — bad path fails in resolve_setup_path)
# --------------------------------------------------------------------------- #
def test_run_one_captures_error_for_nonexistent_setup(tmp_path: Path) -> None:
    """``run_one`` captures a ``FileNotFoundError`` traceback into ``RunResult.error``."""
    setup = SetupConfig(id="ghost", path="system_setups/does_not_exist.py")
    ps = ParameterSetConfig(
        id="ps", factory="one_day_only", year=2021, seconds_per_timestep=60,
        post_processing_options=["COMPUTE_KPIS"],
    )
    result = run_one(setup, ps, str(tmp_path / "out"), REPO_ROOT)
    assert result.setup_id == "ghost"
    assert result.parameter_set_id == "ps"
    assert result.error is not None
    assert "FileNotFoundError" in result.error
    assert not result.artifacts
