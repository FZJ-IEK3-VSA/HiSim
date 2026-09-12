"""Tests for the generic CHP system and CHPConfig factory methods.

Covers integration of ``generic_chp.SimpleCHP`` with ``controller_l1_chp.L1CHPController``
under various demand/hydrogen scenarios, plus unit checks of ``CHPConfig``
default-config builders and ``GenericCHPState.clone``.

``L1CHPControllerConfig`` carries four default configurations whose thresholds are not
symmetric: ``t_min_dhw_in_celsius`` runs 42/50/50/42 over chp, fuel cell, chp-with-buffer and
fuel-cell-with-buffer, and a buffer raises ``t_min_heating_in_celsius`` to 35.0 for gas but to
31.0 for hydrogen. Nothing in the module, the tests or the commit history explains either, so
the values stand as they were written in 2023 and ``test_chp_controller_default_thresholds``
pins all four as literals - a pin that read its expectation off a sibling factory would agree
with any drift that happened to move both.
"""

# -*- coding: utf-8 -*-
import dataclasses

import pytest
from tests import functions_for_testing as fft

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import (
    generic_chp,
    controller_l1_chp,
)
from hisim.simulationparameters import SimulationParameters
from hisim.config import ComponentID


@pytest.mark.base
def test_chp_system() -> None:
    """Test the CHP+controller integration across four demand/hydrogen scenarios.

    Sets up a ``SimpleCHP`` with an ``L1CHPController`` and fake inputs for buffer
    temperature, boiler temperature, electricity target, and hydrogen SOC, then
    simulates multiple timesteps and asserts expected thermal, electrical, and fuel
    outputs for each case:
      - CHP runs when hydrogen SOC is sufficient and both heat and electricity are needed.
      - CHP shuts down when hydrogen SOC is zero.
      - CHP shuts down when heat is not needed (temperatures above thresholds).
      - CHP shuts down when electricity is not needed (electricity target positive).

    The controller is the fuel cell with a buffer storage, which regulates the buffer between
    31.0 °C and 40.0 °C. Two things this test used to lean on had to be put right for the second
    scenario to keep meaning what it says:

    * The CHP's on/off input was wired to the controller's *heating mode* channel, so what arrived
      at the CHP as "run" was really "heat the building rather than the water", and every "shuts
      down" assertion below was an assertion about which vessel was being served. In the second
      scenario the two states of charge tie exactly - ``(30 - 31) / (40 - 31)`` and
      ``(40 - 42) / (60 - 42)`` are both -1/9, to the last bit - so the mode fell to 0 and every
      output read as zero whatever the hydrogen store did. The input is now wired to the on/off
      channel, which is what the assertions talk about.
    * The second scenario then has to run long enough for the machine to be allowed to stop: the
      minimum *operation* time, not the minimum idle time, is what holds a running CHP on.

    The scenario's temperatures are left exactly as they were, tie included: once the on/off
    channel is the one being read, the tie only decides which vessel would be served, not whether
    the machine runs, and the second scenario's assertions fail when the hydrogen state of charge
    below is raised from 0 to 50 - which is the check they were always meant to be.
    """
    seconds_per_timestep = 60
    thermal_power = 500  # thermal power in Watt
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)

    # configure and add chp
    chp_config = generic_chp.CHPConfig.get_default_config_fuelcell(thermal_power=thermal_power)
    my_chp = generic_chp.SimpleCHP(my_simulation_parameters=my_simulation_parameters, config=chp_config)

    # configure chp controller
    chp_controller_config = controller_l1_chp.L1CHPControllerConfig.get_default_config_fuel_cell_with_buffer()
    chp_controller_config.electricity_threshold = chp_config.p_el / 2
    my_chp_controller = controller_l1_chp.L1CHPController(
        my_simulation_parameters=my_simulation_parameters, config=chp_controller_config
    )

    # Set Fake Inputs
    buffer_temperature = cp.ComponentOutput(
        "FakeBuffer",
        "BufferTemperature",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.WATT,
        component_id=ComponentID("FakeBuffer"),
    )
    boiler_temperature = cp.ComponentOutput(
        "FakeBoilerTemperature",
        "HotWaterStorageTemperature",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.WATT,
        component_id=ComponentID("FakeBoilerTemperature"),
    )
    electricity_target = cp.ComponentOutput(
        "FakeElectricityTarget",
        "ElectricityTarget",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
        component_id=ComponentID("FakeElectricityTarget"),
    )
    hydrogensoc = cp.ComponentOutput(
        "FakeH2SOC",
        "HydrogenSOC",
        lt.LoadTypes.GREEN_HYDROGEN,
        lt.Units.PERCENT,
        component_id=ComponentID("FakeH2SOC"),
    )

    number_of_outputs = fft.get_number_of_outputs(
        [
            my_chp,
            my_chp_controller,
            buffer_temperature,
            boiler_temperature,
            electricity_target,
            hydrogensoc,
        ]
    )
    single_timestep_values: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_chp_controller.electricity_target_channel.source_output = electricity_target
    my_chp_controller.hydrogen_soc_channel.source_output = hydrogensoc
    my_chp_controller.building_temperature_channel.source_output = buffer_temperature
    my_chp_controller.dhw_temperature_channel.source_output = boiler_temperature

    my_chp.chp_onoff_signal_channel.source_output = my_chp_controller.chp_onoff_signal_channel
    my_chp.chp_heatingmode_signal_channel.source_output = my_chp_controller.chp_heatingmode_signal_channel

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [
            my_chp,
            my_chp_controller,
            buffer_temperature,
            boiler_temperature,
            electricity_target,
            hydrogensoc,
        ]
    )

    # test if chp runs when hydrogen in storage and heat as well as electricity needed
    single_timestep_values.values[electricity_target.global_index] = -2.5e3
    single_timestep_values.values[hydrogensoc.global_index] = 50
    single_timestep_values.values[buffer_temperature.global_index] = 30
    single_timestep_values.values[boiler_temperature.global_index] = 55

    for timestep in range(int((chp_controller_config.min_idle_time_in_seconds / seconds_per_timestep) + 2)):
        my_chp_controller.i_simulate(timestep, single_timestep_values, False)
        my_chp.i_simulate(timestep, single_timestep_values, False)

    assert single_timestep_values.values[my_chp.thermal_power_output_building_channel.global_index] == 500
    assert single_timestep_values.values[my_chp.thermal_power_output_dhw_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.electricity_output_channel.global_index] > 500
    assert single_timestep_values.values[my_chp.fuel_consumption_channel.global_index] > 500 / (3.6e3 * 3.939e4)

    # test if chp shuts down when too little hydrogen in storage and electricty as well as heat needed
    single_timestep_values.values[electricity_target.global_index] = -2.5e3
    single_timestep_values.values[hydrogensoc.global_index] = 0
    single_timestep_values.values[buffer_temperature.global_index] = 30
    single_timestep_values.values[boiler_temperature.global_index] = 40

    for timestep_t in range(
        timestep,
        timestep + int((chp_controller_config.min_operation_time_in_seconds / seconds_per_timestep) + 2),
    ):
        my_chp_controller.i_simulate(timestep_t, single_timestep_values, False)
        my_chp.i_simulate(timestep_t, single_timestep_values, False)

    assert single_timestep_values.values[my_chp.thermal_power_output_building_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.thermal_power_output_dhw_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.electricity_output_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.fuel_consumption_channel.global_index] == 0

    # test if chp shuts down when hydrogen is ok, electricity is needed, but heat not
    single_timestep_values.values[electricity_target.global_index] = -2.5e3
    single_timestep_values.values[hydrogensoc.global_index] = 50
    single_timestep_values.values[buffer_temperature.global_index] = 40.5
    single_timestep_values.values[boiler_temperature.global_index] = 40

    for timestep in range(int((chp_controller_config.min_operation_time_in_seconds / seconds_per_timestep) + 2)):
        my_chp_controller.i_simulate(timestep, single_timestep_values, False)
        my_chp.i_simulate(timestep, single_timestep_values, False)

    single_timestep_values.values[electricity_target.global_index] = -2.5e3
    single_timestep_values.values[hydrogensoc.global_index] = 50
    single_timestep_values.values[buffer_temperature.global_index] = 40.5
    single_timestep_values.values[boiler_temperature.global_index] = 61

    for ttt in range(
        timestep_t,
        timestep_t + int((chp_controller_config.min_idle_time_in_seconds / seconds_per_timestep) + 2),
    ):
        my_chp_controller.i_simulate(ttt, single_timestep_values, False)
        my_chp.i_simulate(ttt, single_timestep_values, False)

    assert single_timestep_values.values[my_chp.thermal_power_output_building_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.thermal_power_output_dhw_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.electricity_output_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.fuel_consumption_channel.global_index] == 0

    # test if chp shuts down when hydrogen is ok, heat is needed, but electricity not
    single_timestep_values.values[electricity_target.global_index] = -2.5e3
    single_timestep_values.values[hydrogensoc.global_index] = 50
    single_timestep_values.values[buffer_temperature.global_index] = 40.5
    single_timestep_values.values[boiler_temperature.global_index] = 40

    for timestep in range(int((chp_controller_config.min_operation_time_in_seconds / seconds_per_timestep) + 2)):
        my_chp_controller.i_simulate(timestep, single_timestep_values, False)
        my_chp.i_simulate(timestep, single_timestep_values, False)

    single_timestep_values.values[electricity_target.global_index] = 2.5e3
    single_timestep_values.values[hydrogensoc.global_index] = 50
    single_timestep_values.values[buffer_temperature.global_index] = 40.5
    single_timestep_values.values[boiler_temperature.global_index] = 61

    for ttt in range(
        timestep_t,
        timestep_t + int((chp_controller_config.min_idle_time_in_seconds / seconds_per_timestep) + 2),
    ):
        my_chp_controller.i_simulate(ttt, single_timestep_values, False)
        my_chp.i_simulate(ttt, single_timestep_values, False)

    assert single_timestep_values.values[my_chp.thermal_power_output_building_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.thermal_power_output_dhw_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.electricity_output_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.fuel_consumption_channel.global_index] == 0


@pytest.mark.base
def test_chp_heats_the_water_to_the_dhw_maximum_in_summer() -> None:
    """Outside the heating season the CHP runs until the *water's* maximum, not the room's.

    ``calculate_state`` has a branch of its own for the months between
    ``day_of_heating_season_end`` and ``day_of_heating_season_begin``, where the drain hot water
    storage is the only vessel served. It switched the machine off at
    ``t_max_heating_in_celsius`` - the top of the space-heating band, 20.5 °C for the bufferless
    gas configuration used here - while the band the same configuration asks for the water is
    42 to 60 °C. Every summer run therefore stopped as soon as the minimum runtime let it, and
    the 60 °C was never reached.

    The three phases below are one continuous run of the same controller: the store starts under
    its lower bound and the machine comes on, then sits at 50 °C - inside the water band, above
    the heating maximum - for longer than the minimum operation time, which is what makes the
    assertion about the state machine rather than about the runtime guard, and only above 60 °C
    does it switch off.
    """
    seconds_per_timestep = 60
    thermal_power = 500  # thermal power in Watt
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)

    chp_config = generic_chp.CHPConfig.get_default_config_chp(thermal_power=thermal_power)
    my_chp = generic_chp.SimpleCHP(my_simulation_parameters=my_simulation_parameters, config=chp_config)

    chp_controller_config = controller_l1_chp.L1CHPControllerConfig.get_default_config_chp()
    my_chp_controller = controller_l1_chp.L1CHPController(
        my_simulation_parameters=my_simulation_parameters, config=chp_controller_config
    )

    # Set Fake Inputs
    building_temperature = cp.ComponentOutput(
        "FakeBuilding",
        "BuildingTemperature",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.WATT,
        component_id=ComponentID("FakeBuilding"),
    )
    boiler_temperature = cp.ComponentOutput(
        "FakeBoilerTemperature",
        "HotWaterStorageTemperature",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.WATT,
        component_id=ComponentID("FakeBoilerTemperature"),
    )
    electricity_target = cp.ComponentOutput(
        "FakeElectricityTarget",
        "ElectricityTarget",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
        component_id=ComponentID("FakeElectricityTarget"),
    )

    number_of_outputs = fft.get_number_of_outputs(
        [
            my_chp,
            my_chp_controller,
            building_temperature,
            boiler_temperature,
            electricity_target,
        ]
    )
    single_timestep_values: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_chp_controller.electricity_target_channel.source_output = electricity_target
    my_chp_controller.building_temperature_channel.source_output = building_temperature
    my_chp_controller.dhw_temperature_channel.source_output = boiler_temperature

    my_chp.chp_onoff_signal_channel.source_output = my_chp_controller.chp_onoff_signal_channel
    my_chp.chp_heatingmode_signal_channel.source_output = my_chp_controller.chp_heatingmode_signal_channel

    fft.add_global_index_of_components(
        [
            my_chp,
            my_chp_controller,
            building_temperature,
            boiler_temperature,
            electricity_target,
        ]
    )

    # The heating season runs from day 270 to day 150 of the following year, so a day in July is
    # outside it. The controller measures the season in timesteps, as it does in __init__.
    day_in_july = 190
    first_summer_timestep = int(day_in_july * 24 * 3600 / seconds_per_timestep)
    assert my_chp_controller.heating_season_end < first_summer_timestep < my_chp_controller.heating_season_begin
    minimum_runtime_in_timesteps = int(chp_controller_config.min_operation_time_in_seconds / seconds_per_timestep)

    # The store is below its lower bound (42 °C) and electricity is wanted: the CHP comes on and,
    # summer being water-heating only, serves the drain hot water storage.
    single_timestep_values.values[electricity_target.global_index] = -2.5e3
    single_timestep_values.values[building_temperature.global_index] = 22
    single_timestep_values.values[boiler_temperature.global_index] = 40

    for timestep in range(first_summer_timestep, first_summer_timestep + 2):
        my_chp_controller.i_simulate(timestep, single_timestep_values, False)
        my_chp.i_simulate(timestep, single_timestep_values, False)

    assert single_timestep_values.values[my_chp_controller.chp_onoff_signal_channel.global_index] == 1
    assert single_timestep_values.values[my_chp_controller.chp_heatingmode_signal_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.thermal_power_output_dhw_channel.global_index] == thermal_power

    # 50 °C is above the heating maximum of 20.5 °C and well inside the water band of 42 to 60 °C.
    # The run continues past the minimum operation time, so nothing but the set temperatures holds
    # the machine on any more.
    single_timestep_values.values[boiler_temperature.global_index] = 50

    for timestep in range(
        first_summer_timestep + 2,
        first_summer_timestep + minimum_runtime_in_timesteps + 2,
    ):
        my_chp_controller.i_simulate(timestep, single_timestep_values, False)
        my_chp.i_simulate(timestep, single_timestep_values, False)

    assert single_timestep_values.values[my_chp_controller.chp_onoff_signal_channel.global_index] == 1
    assert single_timestep_values.values[my_chp.thermal_power_output_dhw_channel.global_index] == thermal_power
    assert single_timestep_values.values[my_chp.thermal_power_output_building_channel.global_index] == 0

    # Above the water's own maximum it stops.
    single_timestep_values.values[boiler_temperature.global_index] = 61

    for timestep in range(
        first_summer_timestep + minimum_runtime_in_timesteps + 2,
        first_summer_timestep + minimum_runtime_in_timesteps + 4,
    ):
        my_chp_controller.i_simulate(timestep, single_timestep_values, False)
        my_chp.i_simulate(timestep, single_timestep_values, False)

    assert single_timestep_values.values[my_chp_controller.chp_onoff_signal_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.thermal_power_output_dhw_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.thermal_power_output_building_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.electricity_output_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.fuel_consumption_channel.global_index] == 0


@pytest.mark.base
def test_get_default_config_chp_basic() -> None:
    """Test CHPConfig.get_default_config_chp with thermal_power=1000 and default building name."""
    config = generic_chp.CHPConfig.get_default_config_chp(thermal_power=1000)
    assert config.p_th == 1000
    assert config.p_el == pytest.approx(660)
    assert config.p_fuel == pytest.approx(2000)
    assert config.fuel_type == lt.LoadTypes.GAS
    assert config.component_id.name == "CHP"
    assert config.source_weight == 1
    assert config.component_id.building is None


@pytest.mark.base
def test_get_default_config_chp_zero() -> None:
    """Test CHPConfig.get_default_config_chp with thermal_power=0 (boundary case)."""
    config = generic_chp.CHPConfig.get_default_config_chp(thermal_power=0)
    assert config.p_th == 0
    assert config.p_el == 0
    assert config.p_fuel == 0


@pytest.mark.base
def test_get_default_config_chp_default_building() -> None:
    """Omitting the component_id leaves the identity without a building."""
    config = generic_chp.CHPConfig.get_default_config_chp(thermal_power=1000)
    assert config.component_id.building is None


@pytest.mark.base
def test_get_default_config_chp_custom_building_and_powers() -> None:
    """Test CHPConfig.get_default_config_chp with a custom building name and thermal_power=500."""
    config = generic_chp.CHPConfig.get_default_config_chp(
        thermal_power=500, component_id=ComponentID(name="CHP", building="Custom")
    )
    assert config.component_id.building == "Custom"
    assert config.p_th == 500
    assert config.p_el == pytest.approx(330)
    assert config.p_fuel == pytest.approx(1000)


@pytest.mark.base
def test_get_default_config_fuelcell_basic() -> None:
    """Test CHPConfig.get_default_config_fuelcell with thermal_power=1000."""
    config = generic_chp.CHPConfig.get_default_config_fuelcell(thermal_power=1000)
    assert config.p_th == 1000
    assert config.p_el == pytest.approx((0.48 / 0.43) * 1000)
    assert config.p_fuel == pytest.approx((1 / 0.43) * 1000)
    assert config.fuel_type == lt.LoadTypes.GREEN_HYDROGEN


@pytest.mark.base
def test_get_default_config_fuelcell_zero() -> None:
    """Test CHPConfig.get_default_config_fuelcell with thermal_power=0 (boundary case)."""
    config = generic_chp.CHPConfig.get_default_config_fuelcell(thermal_power=0)
    assert config.p_th == 0
    assert config.p_el == 0
    assert config.p_fuel == 0


@pytest.mark.base
def test_generic_chp_state_clone_state_one() -> None:
    """GenericCHPState(state=1).clone() returns a new instance with the same state."""
    original = generic_chp.GenericCHPState(state=1)
    cloned = original.clone()
    assert cloned.state == 1
    assert cloned is not original


@pytest.mark.base
def test_generic_chp_state_clone_state_zero() -> None:
    """GenericCHPState(state=0).clone() preserves the boundary state value."""
    original = generic_chp.GenericCHPState(state=0)
    cloned = original.clone()
    assert cloned.state == 0
    assert cloned is not original


@pytest.mark.base
def test_generic_chp_state_clone_independence() -> None:
    """Mutating the clone's state does not affect the original (independence check)."""
    original = generic_chp.GenericCHPState(state=1)
    cloned = original.clone()
    cloned.state = 5
    assert original.state == 1
    assert cloned.state == 5


@pytest.mark.base
def test_chp_controller_default_thresholds() -> None:
    """Pins every threshold that the four default controller configurations carry.

    They cross two fuels with the presence of a buffer storage, and the numbers are not
    symmetric: the lower drain hot water bound runs 42 / 50 / 50 / 42 °C down the list below, and
    a buffer raises the lower heating bound to 35.0 °C for gas but to 31.0 °C for hydrogen.
    Nothing on record says why, so the values are kept as they were written in 2023 rather than
    guessed at, and pinned here so that any later change to one of them has to be deliberate.

    Every expectation is a literal. Checking one factory against another would pass just as
    happily if both of them drifted, which is the change this is meant to catch.
    """
    config_class = controller_l1_chp.L1CHPControllerConfig
    expected: dict[str, dict[str, object]] = {
        "chp": {
            "component_name": "CHPController",
            "use": lt.LoadTypes.GAS,
            "h2_soc_threshold": 0,
            "t_min_heating_in_celsius": 20.0,
            "t_max_heating_in_celsius": 20.5,
            "t_min_dhw_in_celsius": 42,
            "t_max_dhw_in_celsius": 60,
            "day_of_heating_season_begin": 270,
            "day_of_heating_season_end": 150,
            "min_operation_time_in_seconds": 3600 * 4,
            "min_idle_time_in_seconds": 3600 * 2,
        },
        "fuel_cell": {
            "component_name": "FuelCellController",
            "use": lt.LoadTypes.GREEN_HYDROGEN,
            "h2_soc_threshold": 8.0,
            "t_min_heating_in_celsius": 20.0,
            "t_max_heating_in_celsius": 20.5,
            "t_min_dhw_in_celsius": 50,
            "t_max_dhw_in_celsius": 60,
            "day_of_heating_season_begin": 270,
            "day_of_heating_season_end": 150,
            "min_operation_time_in_seconds": 3600 * 4,
            "min_idle_time_in_seconds": 3600 * 2,
        },
        "chp_with_buffer": {
            "component_name": "CHPController",
            "use": lt.LoadTypes.GAS,
            "h2_soc_threshold": 0,
            "t_min_heating_in_celsius": 35.0,
            "t_max_heating_in_celsius": 40.0,
            "t_min_dhw_in_celsius": 50,
            "t_max_dhw_in_celsius": 60,
            "day_of_heating_season_begin": 269,
            "day_of_heating_season_end": 150,
            "min_operation_time_in_seconds": 3600 * 4,
            "min_idle_time_in_seconds": 3600 * 2,
        },
        "fuel_cell_with_buffer": {
            "component_name": "FuelCellController",
            "use": lt.LoadTypes.GREEN_HYDROGEN,
            "h2_soc_threshold": 8.0,
            "t_min_heating_in_celsius": 31.0,
            "t_max_heating_in_celsius": 40.0,
            "t_min_dhw_in_celsius": 42,
            "t_max_dhw_in_celsius": 60,
            "day_of_heating_season_begin": 269,
            "day_of_heating_season_end": 150,
            "min_operation_time_in_seconds": 3600 * 4,
            "min_idle_time_in_seconds": 3600 * 2,
        },
    }

    for factory_name, fields in expected.items():
        config = getattr(config_class, "get_default_config_" + factory_name)()
        actual: dict[str, object] = {
            field: config.component_id.name if field == "component_name" else getattr(config, field)
            for field in fields
        }
        assert actual == fields, factory_name


@pytest.mark.base
def test_chp_controller_config_refuses_a_heating_band_of_zero_width() -> None:
    """Equal heating bounds are refused at construction, rather than dividing by zero later.

    ``determine_heating_mode`` reads the building's heating level as the measured temperature's
    position inside the band divided by the band's width, so a band of zero width would raise
    only in the middle of a simulation, if at all.
    """
    config = controller_l1_chp.L1CHPControllerConfig.get_default_config_chp()

    with pytest.raises(ValueError, match="t_min_heating_in_celsius"):
        dataclasses.replace(config, t_min_heating_in_celsius=config.t_max_heating_in_celsius)


@pytest.mark.base
def test_chp_controller_config_refuses_an_inverted_dhw_band() -> None:
    """A lower drain hot water bound above the upper one is refused at construction.

    An inverted band does not fail anywhere later: it flips the sign of the storage's heating
    level, so the controller would quietly serve the fuller vessel and the run would look like a
    working simulation of a differently configured house.
    """
    config = controller_l1_chp.L1CHPControllerConfig.get_default_config_chp()

    with pytest.raises(ValueError, match="t_min_dhw_in_celsius"):
        dataclasses.replace(config, t_min_dhw_in_celsius=config.t_max_dhw_in_celsius + 1)
