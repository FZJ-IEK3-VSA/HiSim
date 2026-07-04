"""Tests for the rSOC L2 battery-system controller config seam.

These tests verify that ``RsocBatteryControllerConfig`` can be constructed
without touching the filesystem or the module-global ``utils.HISIMPATH``: an
in-memory ``config_data`` dict (or an explicit ``config_path`` for
``read_config``) can be supplied so that config construction -- and therefore
the controller built on top of it -- is testable even when the
``rSOC_manufacturer_config.json`` inputs file is absent.
"""

# clean
import json
import pathlib

import pytest

from hisim import component as cp
from hisim.components import controller_l2_rsoc_battery_system as l2
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


def _make_rsoc_config_dict() -> dict[str, float]:
    """Return a representative in-memory rSOC manufacturer config variant."""
    return {
        "nom_load_soec": 40.0,
        "min_load_soec": 2.315,
        "max_load_soec": 49.64,
        "standby_load": 1.0,
        "nom_power_sofc": 10.0,
        "min_power_sofc": 1.7,
        "max_power_sofc": 13.0,
    }


@pytest.mark.base
def test_config_rsoc_from_in_memory_dict() -> None:
    """config_rsoc builds the config from an in-memory dict (no filesystem)."""
    config = l2.RsocBatteryControllerConfig.config_rsoc(
        rsoc_name="RSOC_TEST",
        operation_mode="StandbyLoad",
        config_data=_make_rsoc_config_dict(),
    )
    assert config.building_name == "BUI1"
    assert config.name == "rSOC and Battery Controller"
    assert config.nom_load_soec == 40.0
    assert config.min_load_soec == 2.315
    assert config.max_load_soec == 49.64
    assert config.standby_load == 1.0
    assert config.nom_power_sofc == 10.0
    assert config.min_power_sofc == 1.7
    assert config.max_power_sofc == 13.0
    assert config.operation_mode == "StandbyLoad"


@pytest.mark.base
def test_config_rsoc_building_name_override_and_defaults() -> None:
    """config_rsoc forwards building_name and applies defaults for missing keys."""
    config = l2.RsocBatteryControllerConfig.config_rsoc(
        rsoc_name="RSOC_TEST",
        operation_mode="MinimumLoad",
        building_name="BUI2",
        config_data={"nom_load_soec": 40.0},
    )
    assert config.building_name == "BUI2"
    assert config.operation_mode == "MinimumLoad"
    assert config.nom_load_soec == 40.0
    # Keys absent from the in-memory dict fall back to the documented defaults.
    assert config.min_load_soec == 0.0
    assert config.max_load_soec == 0.0
    assert config.standby_load == 0.0
    assert config.nom_power_sofc == 0.0
    assert config.min_power_sofc == 0.0
    assert config.max_power_sofc == 0.0


@pytest.mark.base
def test_read_config_with_explicit_path(tmp_path: pathlib.Path) -> None:
    """read_config reads a variant from an explicit path (no HISIMPATH coupling)."""
    variant = _make_rsoc_config_dict()
    config_file = tmp_path / "rSOC_manufacturer_config.json"
    config_file.write_text(
        json.dumps({"rSOC variants": {"RSOC_TEST": variant}}), encoding="utf-8"
    )
    loaded = l2.RsocBatteryControllerConfig.read_config(
        "RSOC_TEST", config_path=config_file
    )
    assert loaded == variant


@pytest.mark.base
def test_read_config_with_explicit_path_missing_variant(tmp_path: pathlib.Path) -> None:
    """read_config returns an empty dict for an unknown variant name."""
    config_file = tmp_path / "rSOC_manufacturer_config.json"
    config_file.write_text(
        json.dumps({"rSOC variants": {"RSOC_TEST": {"nom_load_soec": 1.0}}}),
        encoding="utf-8",
    )
    loaded = l2.RsocBatteryControllerConfig.read_config(
        "DOES_NOT_EXIST", config_path=config_file
    )
    assert loaded == {}


@pytest.mark.base
def test_rsoc_battery_controller_built_from_in_memory_config() -> None:
    """The controller can be built and simulated from an in-memory config dict.

    This exercises the seam end to end: the config is constructed without the
    rSOC_manufacturer_config.json inputs file, the controller is wired up, and a
    single SOFC-mode timestep is simulated.
    """
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2021, seconds_per_timestep
    )

    config = l2.RsocBatteryControllerConfig.config_rsoc(
        rsoc_name="RSOC_TEST",
        operation_mode="StandbyLoad",
        config_data=_make_rsoc_config_dict(),
    )
    my_controller = l2.RsocBatteryController(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
    )

    res_load = cp.ComponentOutput(
        "FakeRESLoad",
        l2.RsocBatteryController.RESLoad,
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
    )
    demand = cp.ComponentOutput(
        "FakeDemand",
        l2.RsocBatteryController.Demand,
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
    )

    number_of_outputs = fft.get_number_of_outputs([my_controller, res_load, demand])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_controller.load_input.source_output = res_load
    my_controller.demand_input.source_output = demand
    fft.add_global_index_of_components([my_controller, res_load, demand])

    # demand (5 kW) > res_load (0 kW) -> power_delta = +5 kW -> SOFC branch.
    # StandbyLoad with min_power_sofc (1.7) <= 5 <= max_power_sofc (13):
    #   load_to_system = 5, power_to_battery = 0.
    stsv.values[res_load.global_index] = 0.0
    stsv.values[demand.global_index] = 5000.0

    my_controller.i_restore_state()
    my_controller.i_simulate(0, stsv, False)

    assert stsv.values[my_controller.power.global_index] == 5.0
    assert stsv.values[my_controller.load_to_system.global_index] == 5.0
    assert stsv.values[my_controller.load_to_battery.global_index] == 0.0
