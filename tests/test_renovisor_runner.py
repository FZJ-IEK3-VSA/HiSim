"""Unit tests for ``hisim.renovisor.runner`` orchestration.

``run_simulation`` writes a module config and delegates the in-process simulation to
``hisim_main.main``. Because ``hisim_main.main`` runs a full simulation (heavy: pulls in the
entire component library and executes every time step), these tests inject a fake runner to
verify the orchestration — which arguments are forwarded (resolved setup path, simulation
parameters, module-config path) and what is returned — without executing any HiSim simulation.
All tests are tagged ``pytest.mark.base`` and run without network or heavy work.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from hisim.building_sizer_utils.interface_configs.modular_household_config import ModularHouseholdConfig
from hisim.renovisor.runner import (
    MODULE_CONFIG_FILENAME,
    build_simulation_parameters,
    resolve_setup_path,
    run_simulation,
    write_module_config,
)
from hisim.renovisor.schema import SimulationOverrides

pytestmark = pytest.mark.base

# A real building-sizer setup shipped with a source checkout; ``resolve_setup_path`` looks it up
# under ``system_setups/``. Paired with the gas household config below.
SETUP_FILENAME = "household_gas_building_sizer.py"


def _config() -> ModularHouseholdConfig:
    """A default gas-household config — realistic input for ``run_simulation``."""
    return ModularHouseholdConfig.get_default_config_for_household_gas()


def test_run_simulation_forwards_arguments_to_runner_and_returns_its_path(tmp_path: Path) -> None:
    """The injected runner receives the resolved setup path and simulation parameters.

    The runner also receives the written module-config path, and ``run_simulation``
    returns the runner's directory as a ``Path``.
    """
    config = _config()
    simulation_parameters = build_simulation_parameters(SimulationOverrides(), tmp_path)
    expected_result_dir = tmp_path / "sim-output"
    expected_result_dir.mkdir()

    recorded: dict[str, Any] = {}

    def fake_runner(
        path_to_module: str,
        my_simulation_parameters: Any = None,
        my_module_config: Optional[str] = None,
    ) -> str:
        recorded["path_to_module"] = path_to_module
        recorded["my_simulation_parameters"] = my_simulation_parameters
        recorded["my_module_config"] = my_module_config
        return str(expected_result_dir)

    returned = run_simulation(SETUP_FILENAME, config, simulation_parameters, tmp_path, runner=fake_runner)

    # The runner is forwarded the resolved setup path ...
    assert recorded["path_to_module"] == str(resolve_setup_path(SETUP_FILENAME))
    # ... the exact simulation-parameters object (no copy/rebuild) ...
    assert recorded["my_simulation_parameters"] is simulation_parameters
    # ... and the module-config path that was just written next to the results.
    expected_config_path = tmp_path / MODULE_CONFIG_FILENAME
    assert recorded["my_module_config"] == str(expected_config_path)
    # The config file is actually written and contains the serialized config.
    assert expected_config_path.is_file()
    written = json.loads(expected_config_path.read_text(encoding="utf-8"))
    assert written == json.loads(config.to_json())
    # The return value is the runner's directory wrapped in a Path.
    assert returned == expected_result_dir
    assert isinstance(returned, Path)


def test_run_simulation_creates_result_directory_if_missing(tmp_path: Path) -> None:
    """``run_simulation`` creates the (possibly nested) result directory before writing the config."""
    result_directory = tmp_path / "nested" / "results"
    assert not result_directory.exists()

    def fake_runner(*_args: Any, **_kwargs: Any) -> str:
        return str(result_directory)

    run_simulation(
        SETUP_FILENAME,
        _config(),
        build_simulation_parameters(SimulationOverrides(), result_directory),
        result_directory,
        runner=fake_runner,
    )

    assert result_directory.is_dir()
    assert (result_directory / MODULE_CONFIG_FILENAME).is_file()


def test_run_simulation_default_runner_is_hisim_main_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no runner is injected, ``run_simulation`` lazily resolves to ``hisim_main.main``.

    Monkeypatching ``hisim_main.main`` verifies the default code path forwards to it without
    running a real simulation. The lazy import keeps the heavy component library out of module
    load for production callers that never call ``run_simulation``.
    """
    from hisim import hisim_main

    recorded: dict[str, Any] = {}

    def fake_main(path_to_module: str, my_simulation_parameters: Any, my_module_config: str) -> str:
        recorded["path_to_module"] = path_to_module
        recorded["my_simulation_parameters"] = my_simulation_parameters
        recorded["my_module_config"] = my_module_config
        return str(tmp_path)

    monkeypatch.setattr(hisim_main, "main", fake_main)

    simulation_parameters = build_simulation_parameters(SimulationOverrides(), tmp_path)
    returned = run_simulation(
        SETUP_FILENAME,
        _config(),
        simulation_parameters,
        tmp_path,
    )

    assert recorded["path_to_module"] == str(resolve_setup_path(SETUP_FILENAME))
    assert recorded["my_simulation_parameters"] is simulation_parameters
    assert returned == tmp_path


def test_run_simulation_raises_for_missing_setup_before_calling_runner(tmp_path: Path) -> None:
    """A missing setup file fails fast with ``FileNotFoundError`` and never invokes the runner."""
    called: list[Any] = []

    def fake_runner(*_args: Any, **kwargs: Any) -> str:
        called.append(kwargs)
        return str(tmp_path)

    with pytest.raises(FileNotFoundError, match="does_not_exist.py"):
        run_simulation(
            "does_not_exist.py",
            _config(),
            build_simulation_parameters(SimulationOverrides(), tmp_path),
            tmp_path,
            runner=fake_runner,
        )

    assert not called, "runner must not be called when the setup file is missing"


def test_run_simulation_uses_injected_setup_path_resolver_without_filesystem(tmp_path: Path) -> None:
    """An injected ``setup_path_resolver`` decouples orchestration from the real ``system_setups/`` tree.

    With a fake resolver returning a temporary path, ``run_simulation`` forwards that path to the
    runner verbatim and never touches the repository's setup directory \u2014 so a setup filename that
    does not exist on disk no longer raises ``FileNotFoundError``. This is the seam that lets tests
    verify argument wiring and the config-write-then-call ordering in isolation.
    """
    config = _config()
    simulation_parameters = build_simulation_parameters(SimulationOverrides(), tmp_path)
    # A filename that does NOT exist under system_setups/; the default resolver would raise.
    missing_filename = "this_setup_does_not_exist_anywhere.py"
    fake_setup_path = tmp_path / missing_filename
    fake_setup_path.write_text("# fake setup", encoding="utf-8")
    expected_result_dir = tmp_path / "sim-output"
    expected_result_dir.mkdir()

    recorded: dict[str, Any] = {}

    def fake_runner(
        path_to_module: str,
        my_simulation_parameters: Any = None,
        my_module_config: Optional[str] = None,
    ) -> str:
        recorded["path_to_module"] = path_to_module
        recorded["my_simulation_parameters"] = my_simulation_parameters
        recorded["my_module_config"] = my_module_config
        return str(expected_result_dir)

    def fake_resolver(setup_filename: str) -> Path:
        assert setup_filename == missing_filename
        return fake_setup_path

    returned = run_simulation(
        missing_filename,
        config,
        simulation_parameters,
        tmp_path,
        runner=fake_runner,
        setup_path_resolver=fake_resolver,
    )

    # The injected resolver's path is forwarded verbatim \u2014 no real filesystem lookup.
    assert recorded["path_to_module"] == str(fake_setup_path)
    assert recorded["my_simulation_parameters"] is simulation_parameters
    expected_config_path = tmp_path / MODULE_CONFIG_FILENAME
    assert recorded["my_module_config"] == str(expected_config_path)
    assert expected_config_path.is_file()
    assert returned == expected_result_dir
    assert isinstance(returned, Path)


def test_write_module_config_persists_config_and_creates_directory(tmp_path: Path) -> None:
    """``write_module_config`` writes the serialized config to ``MODULE_CONFIG_FILENAME``.

    The config is written inside a (possibly nested) *result_directory*, creating the
    directory first, and the function returns its path. This test exercises config
    persistence directly, without invoking any simulation runner, which is the seam
    the SRP split between :func:`write_module_config` and :func:`run_simulation` enables.
    """
    config = _config()
    result_directory = tmp_path / "nested" / "results"
    assert not result_directory.exists()

    config_path = write_module_config(config, result_directory)

    assert config_path == result_directory / MODULE_CONFIG_FILENAME
    assert result_directory.is_dir()
    assert config_path.is_file()
    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written == json.loads(config.to_json())
