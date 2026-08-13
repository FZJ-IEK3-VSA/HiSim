"""Unit tests for the HPC-harness config schema (ServerConfig, WorkerConfig, profiles)."""

import pytest

from hpc_harness.config import ServerConfig, WorkerConfig

pytestmark = pytest.mark.hpcharness


# --------------------------------------------------------------------------- config


def test_server_config_rejects_unknown_keys_and_parses_nested():
    """Unknown keys (top-level and nested) are rejected while valid nested blocks parse."""
    with pytest.raises(ValueError, match="Unknown keys"):
        ServerConfig.from_dict({"db_pathh": "x"})
    cfg = ServerConfig.from_dict(
        {"db_path": "t.db", "result_root": "r", "circuit_breaker": {"consecutive": 7},
         "autoscale": {"enabled": True, "standby_floor": 5}}
    )
    assert cfg.circuit_breaker.consecutive == 7
    assert cfg.autoscale.standby_floor == 5
    with pytest.raises(ValueError, match="Unknown keys"):
        ServerConfig.from_dict({"db_path": "x", "result_root": "r", "autoscale": {"floor": 1}})


def test_server_config_finalize_derives_logs_db(tmp_path):
    """finalize() derives the logs DB next to the core DB and computes max_attempts."""
    cfg = ServerConfig(db_path=str(tmp_path / "core" / "tasks.db"),
                       result_root=str(tmp_path / "res")).finalize()
    assert cfg.logs_db_path == str((tmp_path / "core" / "logs.db").resolve())
    assert cfg.max_attempts == 4


def test_worker_config_single_core_forces_one_slot(tmp_path):
    """single_core mode forces max_slots to 1, and an unknown node_gate is rejected."""
    cfg = WorkerConfig(server_url="http://x", result_root=str(tmp_path),
                       mode="single_core", max_slots=32).finalize()
    assert cfg.max_slots == 1
    with pytest.raises(ValueError, match="node_gate"):
        WorkerConfig(server_url="http://x", result_root=str(tmp_path),
                     node_gate="bogus").finalize()


def test_autoscale_profiles_parse_inherit_and_validate(tmp_path):
    """Profiles parse from JSON, inherit top-level defaults, and reject duplicate/blank runners."""
    cfg = ServerConfig.from_dict({
        "db_path": str(tmp_path / "t.db"), "result_root": str(tmp_path / "r"),
        "autoscale": {"enabled": True, "worker_script": "w.sbatch", "max_workers": 7,
                      "profiles": [{"name": "a", "runner": "hisim"},
                                   {"name": "b", "runner": "hisim_setup", "max_workers": 3}]},
    })
    cfg.finalize()
    profiles = {p.name: p for p in cfg.autoscale.resolved_profiles()}
    assert profiles["a"].runner == "hisim" and profiles["a"].max_workers == 7  # inherited
    assert profiles["b"].max_workers == 3  # per-profile override
    assert profiles["a"].worker_script is not None  # inherited from top-level

    with pytest.raises(ValueError, match="distinct runners"):
        ServerConfig.from_dict({
            "db_path": "x", "result_root": "y",
            "autoscale": {"enabled": True, "worker_script": "w",
                          "profiles": [{"name": "a", "runner": "r"}, {"name": "b", "runner": "r"}]},
        }).finalize()
    with pytest.raises(ValueError, match="must set a 'runner'"):
        ServerConfig.from_dict({
            "db_path": "x", "result_root": "y",
            "autoscale": {"enabled": True, "worker_script": "w", "profiles": [{"name": "a"}]},
        }).finalize()
