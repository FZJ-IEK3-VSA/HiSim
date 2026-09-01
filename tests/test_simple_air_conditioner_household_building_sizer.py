"""Tests for the simple air-conditioner household building-sizer system setup.

Covers three concerns:

1.  That a :class:`~hisim.components.building.Building` whose
    ``HeatingByResidents`` and ``HeatingByDevices`` inputs are left unconnected
    (no occupancy component) still simulates correctly with zero internal heat
    gains — the foundation for the new setup that has no occupancy component.
2.  That :func:`setup_function` in
    ``system_setups/simple_air_conditioner_household_building_sizer.py`` builds
    the expected four-component graph (Building, Weather, SimpleAirConditioner,
    SimpleAirConditionerController) with the manual AC → Building
    ThermalPowerDelivered connection, and that all mandatory inputs resolve.
3.  That the matching ``.scenario.json`` declares the same four components and
    one manual connection.
"""

# clean

import json
import os
from pathlib import Path
from typing import Any

import pytest

from hisim import component as cp
from hisim.components import building
from hisim.components import weather
from hisim.components.simple_air_conditioner import (
    SimpleAirConditioner,
    SimpleAirConditionerController,
)
from hisim import hisim_main
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters
from tests.functions_for_testing import add_global_index_of_components, get_number_of_outputs

# Path to the system setup module and scenario JSON, resolved relative to the
# repository root so the tests work regardless of the working directory.
_SETUP_MODULE: str = "simple_air_conditioner_household_building_sizer"
_SETUP_MODULE_PATH: Path = Path(__file__).resolve().parent.parent / "system_setups" / (
    _SETUP_MODULE + ".py"
)
_SCENARIO_JSON: Path = Path(__file__).resolve().parent.parent / "system_setups" / (
    _SETUP_MODULE + ".scenario.json"
)


# ==============================================================================
# 1. Building simulates without occupancy / device heat-gain connections
# ==============================================================================


@pytest.mark.base
def test_building_simulates_without_occupancy_connections() -> None:
    """A Building with no occupancy or device heat-gain inputs runs with 0 W gains.

    The ``HeatingByResidents`` and ``HeatingByDevices`` inputs are optional
    (``mandatory=False``) so that setups without an occupancy component can run.
    When unconnected, :meth:`SingleTimeStepValues.get_input_value` returns 0,
    so internal heat gains must be 0 W and the simulation must complete without
    error.
    """
    my_simulation_parameters = SimulationParameters.one_day_only(2021, 60)
    repo = cp.SimRepository()

    # Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()

    # Building — default German single-family home, no occupancy component
    my_building_config = building.BuildingConfig.preset_standard("Building")
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    my_building.set_sim_repo(repo)
    my_building.i_prepare_simulation()

    # Connect weather channels (mandatory inputs) — but deliberately leave
    # occupancy_heat_gain_channel and device_heat_gain_channel unconnected.
    my_building.temperature_outside_channel.source_output = my_weather.air_temperature_output
    my_building.altitude_channel.source_output = my_weather.altitude_output
    my_building.azimuth_channel.source_output = my_weather.azimuth_output
    my_building.direct_normal_irradiance_channel.source_output = my_weather.dni_output
    my_building.direct_horizontal_irradiance_channel.source_output = my_weather.dhi_output
    my_building.global_horizontal_irradiance_channel.source_output = my_weather.ghi_output
    my_building.direct_normal_irradiance_extra_channel.source_output = my_weather.dni_extra_output
    my_building.apparent_zenith_channel.source_output = my_weather.apparent_zenith_output

    # Sanity: the heat-gain inputs are indeed unconnected
    assert my_building.occupancy_heat_gain_channel.source_output is None
    assert my_building.device_heat_gain_channel.source_output is None

    # Allocate a single-time-step values vector and assign global indices
    number_of_outputs = get_number_of_outputs([my_weather, my_building])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)
    add_global_index_of_components([my_weather, my_building])

    # Simulate one timestep — must not raise
    my_weather.i_simulate(0, stsv, False)
    my_building.i_simulate(0, stsv, False)

    # Internal heat gains from occupants + devices must be 0 W (unconnected → 0)
    internal_gains_index = my_building.internal_heat_gains_from_residents_and_devices_channel.global_index
    assert stsv.values[internal_gains_index] == pytest.approx(0.0, abs=1e-9)

    # The building must still produce a valid indoor-air temperature
    indoor_temp_index = my_building.indoor_air_temperature_channel.global_index
    indoor_temp = stsv.values[indoor_temp_index]
    assert isinstance(indoor_temp, (int, float))
    assert -50.0 < indoor_temp < 100.0, f"Indoor temperature {indoor_temp} out of plausible range"


# ==============================================================================
# 2. setup_function builds the correct component graph
# ==============================================================================


@pytest.mark.base
def test_setup_function_builds_correct_component_graph(tmp_path: Any) -> None:
    """The setup function creates 4 components and wires them correctly.

    Uses :func:`hisim_main.initialize_from_python` to load the setup module (which
    handles the ``system_setups`` import path) with a short one-day simulation, then
    verifies:
    - exactly four components (Building, Weather, SimpleAirConditioner,
      SimpleAirConditionerController);
    - the manual AC → Building ThermalPowerDelivered connection is set;
    - ``prepare_calculation`` and ``connect_all_components`` succeed (all
      mandatory inputs resolve).
    """
    # Use a one-day simulation so prepare_calculation (weather data loading) is
    # fast.  Set a concrete result_directory so the setup function does not try
    # to configure the ResultPathProviderSingleton and so connect_input can write
    # its connections JSON.
    result_directory = str(tmp_path / "results")
    os.makedirs(result_directory, exist_ok=True)
    my_simulation_parameters = SimulationParameters.one_day_only(2021, 60)
    my_simulation_parameters.result_directory = result_directory

    setup_path = str(_SETUP_MODULE_PATH)
    my_sim = hisim_main.initialize_from_python(
        path_to_module=setup_path,
        my_simulation_parameters=my_simulation_parameters,
        my_module_config=None,
    )

    # ---- Verify the four expected components are present ----------------
    component_types = {
        type(wrapped.my_component) for wrapped in my_sim.wrapped_components
    }
    assert building.Building in component_types
    assert weather.Weather in component_types
    assert SimpleAirConditioner in component_types
    assert SimpleAirConditionerController in component_types
    assert len(my_sim.wrapped_components) == 4

    # ---- Verify the manual AC → Building ThermalPowerDelivered connection --
    my_building = next(
        wrapped.my_component
        for wrapped in my_sim.wrapped_components
        if isinstance(wrapped.my_component, building.Building)
    )
    my_air_conditioner = next(
        wrapped.my_component
        for wrapped in my_sim.wrapped_components
        if isinstance(wrapped.my_component, SimpleAirConditioner)
    )
    thermal_power_input = my_building.thermal_power_delivered_channel
    assert thermal_power_input.src_object_name == my_air_conditioner.component_name
    assert thermal_power_input.src_field_name == my_air_conditioner.ThermalPowerDelivered

    # ---- Verify all mandatory inputs resolve after auto-connection ---------
    # prepare_calculation auto-connects default connections; connect_all_components
    # validates that every mandatory input has a source_output.
    my_sim.prepare_calculation()
    my_sim.connect_all_components()

    # After connect_all_components, every mandatory input must be connected.
    unconnected_mandatory = [
        f"{cinput.component_name}.{cinput.field_name}"
        for wrapped in my_sim.wrapped_components
        for cinput in wrapped.component_inputs
        if cinput.is_mandatory and cinput.source_output is None
    ]
    assert not unconnected_mandatory, (
        f"Unconnected mandatory inputs: {unconnected_mandatory}"
    )

    # The heat-gain inputs must be unconnected (no occupancy component) yet
    # optional, so they must not appear in the unconnected_mandatory list above.
    assert my_building.occupancy_heat_gain_channel.source_output is None
    assert my_building.device_heat_gain_channel.source_output is None


# ==============================================================================
# 3. Scenario JSON structure matches the Python setup
# ==============================================================================


@pytest.mark.base
def test_scenario_json_structure() -> None:
    """The scenario JSON declares 4 components and 1 manual connection.

    Verifies that the ``.scenario.json`` file is valid JSON, contains the four
    expected component class names, and declares the manual
    SimpleAirConditioner.ThermalPowerDelivered → Building.ThermalPowerDelivered
    connection.
    """
    with _SCENARIO_JSON.open("r", encoding="utf-8") as f:
        scenario = json.load(f)

    # Four components with the expected class names
    component_classnames = [c["component_full_classname"] for c in scenario["components"]]
    assert "hisim.components.building.building.Building" in component_classnames
    assert "hisim.components.weather.Weather" in component_classnames
    assert "hisim.components.simple_air_conditioner.SimpleAirConditioner" in component_classnames
    assert (
        "hisim.components.simple_air_conditioner.SimpleAirConditionerController"
        in component_classnames
    )
    assert len(scenario["components"]) == 4

    # One manual connection: AC.ThermalPowerDelivered → Building.ThermalPowerDelivered
    connections = scenario.get("connections", [])
    assert len(connections) == 1
    conn = connections[0]
    assert conn["source"]["component_name"] == "SimpleAirConditioner"
    assert conn["source"]["field_name"] == "ThermalPowerDelivered"
    assert conn["target"]["component_name"] == "Building"
    assert conn["target"]["field_name"] == "ThermalPowerDelivered"

    # Building config sanity: cooling setpoint 25 °C, connect_automatically True
    building_config = next(
        c["configuration"] for c in scenario["components"]
        if c["component_full_classname"] == "hisim.components.building.building.Building"
    )
    assert building_config["set_cooling_temperature_in_celsius"] == 25.0
    building_entry = next(
        c for c in scenario["components"]
        if c["component_full_classname"] == "hisim.components.building.building.Building"
    )
    assert building_entry["connect_automatically"] is True

    # SimpleAirConditioner config sanity
    ac_config = next(
        c["configuration"] for c in scenario["components"]
        if c["component_full_classname"]
        == "hisim.components.simple_air_conditioner.SimpleAirConditioner"
    )
    assert ac_config["nominal_cooling_power_w"] == 2000.0
    assert ac_config["eta_carnot"] == 0.3
    assert ac_config["temperature_epsilon_k"] == 0.01

    # Controller config sanity
    ctrl_config = next(
        c["configuration"] for c in scenario["components"]
        if c["component_full_classname"]
        == "hisim.components.simple_air_conditioner.SimpleAirConditionerController"
    )
    assert ctrl_config["setpoint_temperature_c"] == 24.0
    assert ctrl_config["deadband_k"] == 0.5


class UndefinedForThisHousehold:
    """The KPIs that cannot exist for a household with no electricity at all.

    This setup builds a weather, a building and an air conditioner: no occupancy, no generation, no
    meter. Its total electricity consumption is genuinely zero, so every figure expressed as a
    proportion of it has no value -- not zero, which would be a measurement, but nothing. The KPI
    layer used to divide by that total and end the run with ``ZeroDivisionError``; it now emits the
    entries without values, the way it already did for a self-consumption rate with no production.

    Pinning the names keeps the distinction from being quietly "fixed" into zeros later, which would
    make a household that consumes nothing indistinguishable from one that consumes plenty and
    self-supplies none of it.
    """

    NAMES = (
        "Ratio between total production and total consumption",
        "Ratio between PV production and total consumption",
        "Relative electricity demand from grid",
        "Self-sufficiency rate according to solar htw berlin",
        "Total energy self-suffiency rate",
    )


@pytest.mark.system_setups
@utils.measure_execution_time
def test_the_setup_completes_a_kpi_run_with_its_undefined_ratios_empty() -> None:
    """Run the setup for one day with KPI and cost post-processing on.

    The three tests above build the component graph and inspect it; none of them runs the setup to
    completion, and none asks for KPIs -- the default post-processing options leave them off. So the
    setup could pass this suite while dying in post-processing, which is exactly what it did.

    Asserting that the undefined ratios carry no value, rather than only that the run finished,
    is what makes this a test of the convention instead of a test that nothing raised.
    """
    path = Path("../system_setups/simple_air_conditioner_household_building_sizer.py")

    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    sim_params.post_processing_options = [
        PostProcessingOptions.COMPUTE_KPIS,
        PostProcessingOptions.WRITE_KPIS_TO_JSON,
        PostProcessingOptions.COMPUTE_CAPEX,
        PostProcessingOptions.COMPUTE_OPEX,
    ]
    result_directory = hisim_main.main(str(path), sim_params)

    result_path = Path(result_directory)
    assert (result_path / "finished.flag").is_file(), f"the run did not finish: {result_directory}"
    kpi_path = result_path / "all_kpis.json"
    assert kpi_path.is_file(), f"all_kpis.json not found in {result_directory}"

    values_by_name: dict = {}

    def collect(node: object) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if isinstance(value, dict) and "value" in value and "unit" in value:
                values_by_name[key] = value["value"]
            else:
                collect(value)

    collect(json.loads(kpi_path.read_text(encoding="utf-8")))

    for name in UndefinedForThisHousehold.NAMES:
        assert name in values_by_name, f"KPI '{name}' is missing from {kpi_path}"
        assert values_by_name[name] is None, (
            f"KPI '{name}' carries {values_by_name[name]!r}, but this household consumes no "
            "electricity at all, so a proportion of its consumption is undefined rather than zero."
        )
