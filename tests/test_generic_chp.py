"""Tests for the generic CHP system and CHPConfig factory methods.

Covers integration of ``generic_chp.SimpleCHP`` with ``controller_l1_chp.L1CHPController``
under various demand/hydrogen scenarios, plus unit checks of ``CHPConfig``
default-config builders and ``GenericCHPState.clone``.

The ``L1CHPControllerConfig`` defaults used here changed with P4 decision D-4: the four
factories used to cross fuel with buffer inconsistently - ``t_min_dhw_in_celsius`` ran
42/50/50/42 over chp, fuel cell, chp-with-buffer and fuel-cell-with-buffer, and the buffer
raised ``t_min_heating_in_celsius`` to 35.0 for gas but to 31.0 for hydrogen. No comment, test
or commit ever gave a reason for either flip, so they were read as copy errors and normalised
onto the first-written values. The last two tests here pin the normalised vocabulary, so that
the declarative conversion can mint a ``gas`` and a ``hydrogen`` preset plus one buffer override.
"""

# -*- coding: utf-8 -*-
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

    The fuel cell with buffer now regulates the buffer between 35.0 °C and 40.0 °C rather than
    between 31.0 °C and 40.0 °C (D-4), and two things this test used to lean on had to be put
    right for the second scenario to keep meaning what it says:

    * The CHP's on/off input was wired to the controller's *heating mode* channel, so ``state.state``
      was the mode and every "shuts down" assertion below was really an assertion about which vessel
      was being heated. With the old 31.0 °C the second scenario's two states of charge came out
      exactly equal - ``(30 - 31) / (40 - 31)`` and ``(40 - 42) / (60 - 42)`` are both -1/9, to the
      last bit - so the mode fell to 0 and the outputs read as zero. At 35.0 °C the tie is gone.
      The input is now wired to the on/off channel, which is what the assertions talk about.
    * The second scenario then has to run long enough for the machine to be allowed to stop: the
      minimum *operation* time, not the minimum idle time, is what holds a running CHP on.
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
def test_chp_controller_fuels_differ_only_in_the_fuel() -> None:
    """The gas and hydrogen defaults agree on every threshold that is not about the fuel (D-4).

    Before D-4 the fuel cell asked for 50 °C of drain hot water where the CHP asked for 42 °C.
    Nothing the controller computes from that threshold - the storage's state of charge and the
    decision to heat water rather than the building - knows what is burnt, so the two now agree.
    """
    gas = controller_l1_chp.L1CHPControllerConfig.get_default_config_chp()
    hydrogen = controller_l1_chp.L1CHPControllerConfig.get_default_config_fuel_cell()

    assert gas.use == lt.LoadTypes.GAS
    assert hydrogen.use == lt.LoadTypes.GREEN_HYDROGEN
    assert gas.h2_soc_threshold == 0
    assert hydrogen.h2_soc_threshold == 8.0
    assert gas.t_min_dhw_in_celsius == hydrogen.t_min_dhw_in_celsius == 42

    differing_fields = {"component_id", "use", "h2_soc_threshold"}
    for field in gas.to_dict():
        if field in differing_fields:
            continue
        assert gas.to_dict()[field] == hydrogen.to_dict()[field], field


@pytest.mark.base
def test_chp_controller_buffer_override_is_one_thing() -> None:
    """The buffer changes the same three fields by the same values for either fuel (D-4).

    Before D-4 the buffer meant 35.0 °C for gas and 31.0 °C for hydrogen, and it also moved the
    drain hot water threshold, in opposite directions per fuel. A buffer storage sits on the space
    heating side only, so it may move the heating band and the start of the heating season, and
    nothing else.
    """
    config_class = controller_l1_chp.L1CHPControllerConfig
    pairs = [
        (config_class.get_default_config_chp(), config_class.get_default_config_chp_with_buffer()),
        (config_class.get_default_config_fuel_cell(), config_class.get_default_config_fuel_cell_with_buffer()),
    ]
    overrides = []
    for without_buffer, with_buffer in pairs:
        assert with_buffer.t_min_heating_in_celsius == 35.0
        assert with_buffer.t_max_heating_in_celsius == 40.0
        assert with_buffer.day_of_heating_season_begin == 269
        assert with_buffer.t_min_dhw_in_celsius == without_buffer.t_min_dhw_in_celsius
        plain, buffered = without_buffer.to_dict(), with_buffer.to_dict()
        overrides.append({field: buffered[field] for field in plain if plain[field] != buffered[field]})

    assert overrides[0] == overrides[1]
    assert set(overrides[0]) == {
        "t_min_heating_in_celsius",
        "t_max_heating_in_celsius",
        "day_of_heating_season_begin",
    }
