"""Tests for the generic CHP system and CHPConfig factory methods.

Covers integration of ``generic_chp.SimpleCHP`` with ``controller_l1_chp.L1CHPController``
under various demand/hydrogen scenarios, plus unit checks of ``CHPConfig``
default-config builders and ``GenericCHPState.clone``.
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
    """
    seconds_per_timestep = 60
    thermal_power = 500  # thermal power in Watt
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    # configure and add chp
    chp_config = generic_chp.CHPConfig.get_default_config_fuelcell(
        thermal_power=thermal_power
    )
    my_chp = generic_chp.SimpleCHP(
        my_simulation_parameters=my_simulation_parameters, config=chp_config
    )

    # configure chp controller
    chp_controller_config = (
        controller_l1_chp.L1CHPControllerConfig.get_default_config_fuel_cell_with_buffer()
    )
    chp_controller_config.electricity_threshold = chp_config.p_el / 2
    my_chp_controller = controller_l1_chp.L1CHPController(
        my_simulation_parameters=my_simulation_parameters, config=chp_controller_config
    )

    # Set Fake Inputs
    buffer_temperature = cp.ComponentOutput(
        "FakeBuffer", "BufferTemperature", lt.LoadTypes.TEMPERATURE, lt.Units.WATT
    )
    boiler_temperature = cp.ComponentOutput(
        "FakeBoilerTemperature",
        "HotWaterStorageTemperature",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.WATT,
    )
    electricity_target = cp.ComponentOutput(
        "FakeElectricityTarget",
        "ElectricityTarget",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
    )
    hydrogensoc = cp.ComponentOutput(
        "FakeH2SOC", "HydrogenSOC", lt.LoadTypes.GREEN_HYDROGEN, lt.Units.PERCENT
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

    my_chp.chp_onoff_signal_channel.source_output = (
        my_chp_controller.chp_heatingmode_signal_channel
    )
    my_chp.chp_heatingmode_signal_channel.source_output = (
        my_chp_controller.chp_heatingmode_signal_channel
    )

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

    for timestep in range(
        int((chp_controller_config.min_idle_time_in_seconds / seconds_per_timestep) + 2)
    ):
        my_chp_controller.i_simulate(timestep, single_timestep_values, False)
        my_chp.i_simulate(timestep, single_timestep_values, False)

    assert single_timestep_values.values[my_chp.thermal_power_output_building_channel.global_index] == 500
    assert single_timestep_values.values[my_chp.thermal_power_output_dhw_channel.global_index] == 0
    assert single_timestep_values.values[my_chp.electricity_output_channel.global_index] > 500
    assert single_timestep_values.values[my_chp.fuel_consumption_channel.global_index] > 500 / (
        3.6e3 * 3.939e4
    )

    # test if chp shuts down when too little hydrogen in storage and electricty as well as heat needed
    single_timestep_values.values[electricity_target.global_index] = -2.5e3
    single_timestep_values.values[hydrogensoc.global_index] = 0
    single_timestep_values.values[buffer_temperature.global_index] = 30
    single_timestep_values.values[boiler_temperature.global_index] = 40

    for timestep_t in range(
        timestep,
        timestep
        + int(
            (chp_controller_config.min_idle_time_in_seconds / seconds_per_timestep) + 2
        ),
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

    for timestep in range(
        int(
            (chp_controller_config.min_operation_time_in_seconds / seconds_per_timestep)
            + 2
        )
    ):
        my_chp_controller.i_simulate(timestep, single_timestep_values, False)
        my_chp.i_simulate(timestep, single_timestep_values, False)

    single_timestep_values.values[electricity_target.global_index] = -2.5e3
    single_timestep_values.values[hydrogensoc.global_index] = 50
    single_timestep_values.values[buffer_temperature.global_index] = 40.5
    single_timestep_values.values[boiler_temperature.global_index] = 61

    for ttt in range(
        timestep_t,
        timestep_t
        + int(
            (chp_controller_config.min_idle_time_in_seconds / seconds_per_timestep) + 2
        ),
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

    for timestep in range(
        int(
            (chp_controller_config.min_operation_time_in_seconds / seconds_per_timestep)
            + 2
        )
    ):
        my_chp_controller.i_simulate(timestep, single_timestep_values, False)
        my_chp.i_simulate(timestep, single_timestep_values, False)

    single_timestep_values.values[electricity_target.global_index] = 2.5e3
    single_timestep_values.values[hydrogensoc.global_index] = 50
    single_timestep_values.values[buffer_temperature.global_index] = 40.5
    single_timestep_values.values[boiler_temperature.global_index] = 61

    for ttt in range(
        timestep_t,
        timestep_t
        + int(
            (chp_controller_config.min_idle_time_in_seconds / seconds_per_timestep) + 2
        ),
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
    assert config.use == lt.LoadTypes.GAS
    assert config.name == "CHP"
    assert config.source_weight == 1
    assert config.building_name == "BUI1"


@pytest.mark.base
def test_get_default_config_chp_zero() -> None:
    """Test CHPConfig.get_default_config_chp with thermal_power=0 (boundary case)."""
    config = generic_chp.CHPConfig.get_default_config_chp(thermal_power=0)
    assert config.p_th == 0
    assert config.p_el == 0
    assert config.p_fuel == 0


@pytest.mark.base
def test_get_default_config_chp_default_building_name() -> None:
    """Omitting building_name uses the default 'BUI1'."""
    config = generic_chp.CHPConfig.get_default_config_chp(thermal_power=1000)
    assert config.building_name == "BUI1"


@pytest.mark.base
def test_get_default_config_chp_custom_building_and_powers() -> None:
    """Test CHPConfig.get_default_config_chp with a custom building name and thermal_power=500."""
    config = generic_chp.CHPConfig.get_default_config_chp(
        thermal_power=500, building_name="Custom"
    )
    assert config.building_name == "Custom"
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
    assert config.use == lt.LoadTypes.GREEN_HYDROGEN


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
