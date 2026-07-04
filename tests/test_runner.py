"""Unit tests for ``scripts/runner.py``.

Cover the config schema, filtering, SimulationParameters construction, setup-path
resolution, pair selection, environment metadata, and the ``run_all`` orchestrator
with an injected fake ``run_one``. All tests are tagged ``pytest.mark.base`` and
run without executing any HiSim simulation.
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
    GoldenConfig,
    ParameterSetConfig,
    RunResult,
    SetupConfig,
    build_simulation_parameters,
    config_hash,
    environment_metadata,
    filter_config,
    load_config,
    resolve_setup_path,
    run_all,
    run_one,
    select_pairs,
)

pytestmark = pytest.mark.base

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = REPO_ROOT / "scripts" / "golden_config.json"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _minimal_config_dict() -> dict:
    return {
        "check_subdir": "golden-ref-check",
        "setups": [
            {"id": "setup_a", "path": "system_setups/simple_system_setup_one.py"},
        ],
        "parameter_sets": [
            {
                "id": "one_week_60s",
                "factory": "one_week_only",
                "year": 2021,
                "seconds_per_timestep": 60,
                "post_processing_options": ["COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"],
            },
        ],
    }


def _write_config(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return p


# --------------------------------------------------------------------------- #
# load_config
# --------------------------------------------------------------------------- #
def test_load_config_happy_path(tmp_path: Path) -> None:
    cfg = load_config(_write_config(tmp_path, _minimal_config_dict()))
    assert isinstance(cfg, GoldenConfig)
    assert cfg.check_subdir == "golden-ref-check"
    assert cfg.setups[0].id == "setup_a"
    ps = cfg.parameter_sets[0]
    assert ps.factory == "one_week_only"
    assert ps.post_processing_options == ["COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"]
    assert ps.nondeterministic is False


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.json")


def test_load_config_empty_setups_raises(tmp_path: Path) -> None:
    data = _minimal_config_dict()
    data["setups"] = []
    with pytest.raises(ValueError, match="setups"):
        load_config(_write_config(tmp_path, data))


def test_load_config_unknown_factory_raises(tmp_path: Path) -> None:
    data = _minimal_config_dict()
    data["parameter_sets"][0]["factory"] = "nonexistent_factory"
    with pytest.raises(ValueError, match="nonexistent_factory"):
        load_config(_write_config(tmp_path, data))


def test_load_config_unknown_option_raises(tmp_path: Path) -> None:
    data = _minimal_config_dict()
    data["parameter_sets"][0]["post_processing_options"] = ["FAKE_OPTION"]
    with pytest.raises(ValueError, match="FAKE_OPTION"):
        load_config(_write_config(tmp_path, data))


def test_load_config_real_file_11_setups_2_params() -> None:
    cfg = load_config(REAL_CONFIG)
    assert len(cfg.setups) == 11
    assert len(cfg.parameter_sets) == 2
    factories = {p.factory for p in cfg.parameter_sets}
    assert factories == {"one_week_only", "full_year"}
    for ps in cfg.parameter_sets:
        assert set(ps.post_processing_options) == {"COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"}


def test_load_config_real_file_setups_exist_on_disk() -> None:
    cfg = load_config(REAL_CONFIG)
    for setup in cfg.setups:
        resolved = resolve_setup_path(setup, REPO_ROOT)
        assert resolved.exists() and resolved.suffix == ".py"


# --------------------------------------------------------------------------- #
# filter_config / select_pairs
# --------------------------------------------------------------------------- #
def _sample_config() -> GoldenConfig:
    return GoldenConfig(
        check_subdir="golden-ref-check",
        setups=[SetupConfig("s1", "a.py"), SetupConfig("s2", "b.py")],
        parameter_sets=[
            ParameterSetConfig("p1", "one_week_only", 2021, 60, ["COMPUTE_KPIS"]),
            ParameterSetConfig("p2", "full_year", 2021, 60, ["COMPUTE_KPIS"]),
        ],
    )


def test_select_pairs_cartesian() -> None:
    pairs = select_pairs(_sample_config())
    assert [(s.id, p.id) for s, p in pairs] == [
        ("s1", "p1"), ("s1", "p2"), ("s2", "p1"), ("s2", "p2"),
    ]


def test_filter_config_by_setup_and_param() -> None:
    cfg = filter_config(_sample_config(), setup_id="s2", param_id="p1")
    assert [s.id for s in cfg.setups] == ["s2"]
    assert [p.id for p in cfg.parameter_sets] == ["p1"]
    assert len(select_pairs(cfg)) == 1


def test_filter_config_unknown_setup_raises() -> None:
    with pytest.raises(ValueError, match="ghost"):
        filter_config(_sample_config(), setup_id="ghost")


def test_filter_config_unknown_param_raises() -> None:
    with pytest.raises(ValueError, match="pX"):
        filter_config(_sample_config(), param_id="pX")


# --------------------------------------------------------------------------- #
# build_simulation_parameters
# --------------------------------------------------------------------------- #
def test_build_simulation_parameters_sets_fields() -> None:
    ps = ParameterSetConfig("t", "one_day_only", 2021, 60, ["COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"])
    params = build_simulation_parameters(ps, "/tmp/rd")
    assert params.result_directory == "/tmp/rd"
    assert params.seconds_per_timestep == 60
    assert params.start_date == datetime.datetime(2021, 1, 1)
    assert params.end_date == datetime.datetime(2021, 1, 2)
    assert PostProcessingOptions.COMPUTE_KPIS in params.post_processing_options
    assert PostProcessingOptions.WRITE_KPIS_TO_JSON in params.post_processing_options
    assert len(params.post_processing_options) == 2


def test_build_simulation_parameters_invalid_factory_raises() -> None:
    ps = ParameterSetConfig("t", "nope", 2021, 60, ["COMPUTE_KPIS"])
    with pytest.raises(ValueError, match="nope"):
        build_simulation_parameters(ps, "/tmp/x")


# --------------------------------------------------------------------------- #
# resolve_setup_path
# --------------------------------------------------------------------------- #
def test_resolve_setup_path_finds_file() -> None:
    setup = SetupConfig("simple", "system_setups/simple_system_setup_one.py")
    resolved = resolve_setup_path(setup, REPO_ROOT)
    assert resolved.exists() and resolved.name == "simple_system_setup_one.py"


def test_resolve_setup_path_missing_raises() -> None:
    with pytest.raises(FileNotFoundError):
        resolve_setup_path(SetupConfig("ghost", "system_setups/nope.py"), REPO_ROOT)


# --------------------------------------------------------------------------- #
# environment metadata
# --------------------------------------------------------------------------- #
def test_config_hash_matches_hashlib(tmp_path: Path) -> None:
    data = b'{"setups": []}'
    p = tmp_path / "c.json"
    p.write_bytes(data)
    assert config_hash(p) == hashlib.sha256(data).hexdigest()


def test_environment_metadata_fields(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, _minimal_config_dict())
    meta = environment_metadata(cfg_path)
    assert set(meta) == {"hisim_commit", "python_version", "platform", "config_sha256", "generated_at"}
    assert meta["config_sha256"] == config_hash(cfg_path)
    assert meta["generated_at"]


# --------------------------------------------------------------------------- #
# run_all with fake run_one
# --------------------------------------------------------------------------- #
def test_run_all_one_result_per_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_run_one(setup, param, result_directory, _repo_root):
        calls.append((setup.id, param.id, result_directory))
        return RunResult(setup.id, param.id, result_directory, kpis={"k": 1.0})

    monkeypatch.setattr("scripts.runner.run_one", fake_run_one)
    results = run_all(_sample_config(), tmp_path, REPO_ROOT, "golden-ref-check")
    assert len(results) == 4
    for _, _, rd in calls:
        assert Path(rd).is_dir()
    assert results[0].kpis == {"k": 1.0}


def test_run_one_captures_error_for_missing_setup(tmp_path: Path) -> None:
    setup = SetupConfig("ghost", "system_setups/does_not_exist.py")
    ps = ParameterSetConfig("p", "one_day_only", 2021, 60, ["COMPUTE_KPIS"])
    result = run_one(setup, ps, str(tmp_path / "out"), REPO_ROOT)
    assert result.error is not None
    assert "FileNotFoundError" in result.error
    assert result.kpis == {}
