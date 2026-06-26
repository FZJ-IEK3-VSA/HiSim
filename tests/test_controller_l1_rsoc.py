"""Tests for the rSOC L1 controller config seam.

These tests verify that ``RsocControllerConfig`` can be constructed without
touching the filesystem or the module-global ``utils.HISIMPATH``: an in-memory
``config_json`` dict (or an explicit ``path`` for ``read_config``) can be
supplied so that config construction -- and therefore the controller built on
top of it -- is testable even when the ``rSOC_manufacturer_config.json`` inputs
file is absent.
"""

# clean
import json

import pytest

from hisim import component as cp
from hisim.components import controller_l1_rsoc
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


def _make_rsoc_config_dict() -> dict:
    """Return a representative in-memory rSOC manufacturer config variant."""
    return {
        "nom_load_soec": 40.0,
        "min_load_soec": 2.315,
        "max_load_soec": 49.64,
        "warm_start_time_soec": 70.0,
        "cold_start_time_soec": 1800.0,
        "switching_time_from_soec_to_sofc": 600.0,
        "nom_power_sofc": 10.0,
        "min_power_sofc": 1.7,
        "max_power_sofc": 13.0,
        "warm_start_time_sofc": 70.0,
        "cold_start_time_sofc": 1800.0,
        "switching_time_from_sofc_to_soec": 600.0,
    }


@pytest.mark.base
def test_config_rsoc_from_in_memory_dict() -> None:
    """config_rsoc builds the config from an in-memory dict (no filesystem)."""
    config = controller_l1_rsoc.RsocControllerConfig.config_rsoc(
        rsoc_name="RSOC_TEST", config_json=_make_rsoc_config_dict()
    )
    assert config.building_name == "BUI1"
    assert config.name == "rSCO l1 Controller"
    assert config.nom_load_soec == 40.0
    assert config.min_load_soec == 2.315
    assert config.max_load_soec == 49.64
    assert config.warm_start_time_soec == 70.0
    assert config.cold_start_time_soec == 1800.0
    assert config.switching_time_from_soec_to_sofc == 600.0
    assert config.nom_power_sofc == 10.0
    assert config.min_power_sofc == 1.7
    assert config.max_power_sofc == 13.0
    assert config.warm_start_time_sofc == 70.0
    assert config.cold_start_time_sofc == 1800.0
    assert config.switching_time_from_sofc_to_soec == 600.0


@pytest.mark.base
def test_config_rsoc_building_name_override_and_defaults() -> None:
    """config_rsoc forwards building_name and applies defaults for missing keys."""
    config = controller_l1_rsoc.RsocControllerConfig.config_rsoc(
        rsoc_name="RSOC_TEST",
        building_name="BUI2",
        config_json={"nom_load_soec": 40.0},
    )
    assert config.building_name == "BUI2"
    assert config.nom_load_soec == 40.0
    # Keys absent from the in-memory dict fall back to the documented defaults.
    assert config.min_load_soec == 0.0
    assert config.nom_power_sofc == 0.0
    assert config.switching_time_from_sofc_to_soec == 0.0


@pytest.mark.base
def test_read_config_with_explicit_path(tmp_path) -> None:
    """read_config reads a variant from an explicit path (no HISIMPATH coupling)."""
    variant = _make_rsoc_config_dict()
    config_file = tmp_path / "rSOC_manufacturer_config.json"
    config_file.write_text(
        json.dumps({"rSOC variants": {"RSOC_TEST": variant}}), encoding="utf-8"
    )
    loaded = controller_l1_rsoc.RsocControllerConfig.read_config(
        "RSOC_TEST", path=config_file
    )
    assert loaded == variant


@pytest.mark.base
def test_read_config_with_explicit_path_missing_variant(tmp_path) -> None:
    """read_config returns an empty dict for an unknown variant name."""
    config_file = tmp_path / "rSOC_manufacturer_config.json"
    config_file.write_text(
        json.dumps({"rSOC variants": {"RSOC_TEST": {"nom_load_soec": 1.0}}}),
        encoding="utf-8",
    )
    loaded = controller_l1_rsoc.RsocControllerConfig.read_config(
        "DOES_NOT_EXIST", path=config_file
    )
    assert loaded == {}


@pytest.mark.base
def test_rsoc_controller_built_from_in_memory_config() -> None:
    """The controller can be built and simulated from an in-memory config dict.

    This exercises the seam end to end: the config is constructed without the
    rSOC_manufacturer_config.json inputs file, the controller is wired up, and a
    single SOEC-mode timestep is simulated.
    """
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2021, seconds_per_timestep
    )

    config = controller_l1_rsoc.RsocControllerConfig.config_rsoc(
        rsoc_name="RSOC_TEST", config_json=_make_rsoc_config_dict()
    )
    my_controller = controller_l1_rsoc.RsocController(
        config=config, my_simulation_parameters=my_simulation_parameters
    )

    provided_power = cp.ComponentOutput(
        "FakeProvidedPower",
        controller_l1_rsoc.RsocController.ProvidedPower,
        lt.LoadTypes.ELECTRICITY,
        lt.Units.KILOWATT,
    )

    number_of_outputs = fft.get_number_of_outputs([my_controller, provided_power])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_controller.provided_power.source_output = provided_power
    fft.add_global_index_of_components([my_controller, provided_power])

    # Negative power -> SOEC mode; controller starts in OFF and transitions to Starting.
    stsv.values[provided_power.global_index] = -10.0

    my_controller.i_restore_state()
    # timestep >= 5 avoids the debug prints inside i_simulate.
    my_controller.i_simulate(5, stsv, False)

    # In the OFF -> Starting transition no power is dispatched yet.
    assert stsv.values[my_controller.current_delta.global_index] == 0.0
    assert stsv.values[my_controller.state_rsoc.global_index] == -1
    assert stsv.values[my_controller.total_off_count.global_index] == 1.0
    assert stsv.values[my_controller.total_standby_count.global_index] == 0.0
    assert stsv.values[my_controller.total_switch_count.global_index] == 0.0
