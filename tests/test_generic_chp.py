"""Basic test for fuel cell."""

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
def test_chp_system():
    """Test chp system."""
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
        "FakeBoilerTemperatue",
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
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

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
    stsv.values[electricity_target.global_index] = -2.5e3
    stsv.values[hydrogensoc.global_index] = 50
    stsv.values[buffer_temperature.global_index] = 30
    stsv.values[boiler_temperature.global_index] = 55

    for timestep in range(
        int((chp_controller_config.min_idle_time_in_seconds / seconds_per_timestep) + 2)
    ):
        my_chp_controller.i_simulate(timestep, stsv, False)
        my_chp.i_simulate(timestep, stsv, False)

    assert stsv.values[my_chp.thermal_power_output_building_channel.global_index] == 500
    assert stsv.values[my_chp.thermal_power_output_dhw_channel.global_index] == 0
    assert stsv.values[my_chp.electricity_output_channel.global_index] > 500
    assert stsv.values[my_chp.fuel_consumption_channel.global_index] > 500 / (
        3.6e3 * 3.939e4
    )

    # test if chp shuts down when too little hydrogen in storage and electricty as well as heat needed
    stsv.values[electricity_target.global_index] = -2.5e3
    stsv.values[hydrogensoc.global_index] = 0
    stsv.values[buffer_temperature.global_index] = 30
    stsv.values[boiler_temperature.global_index] = 40

    for timestep_t in range(
        timestep,
        timestep
        + int(
            (chp_controller_config.min_idle_time_in_seconds / seconds_per_timestep) + 2
        ),
    ):
        my_chp_controller.i_simulate(timestep_t, stsv, False)
        my_chp.i_simulate(timestep_t, stsv, False)

    assert stsv.values[my_chp.thermal_power_output_building_channel.global_index] == 0
    assert stsv.values[my_chp.thermal_power_output_dhw_channel.global_index] == 0
    assert stsv.values[my_chp.electricity_output_channel.global_index] == 0
    assert stsv.values[my_chp.fuel_consumption_channel.global_index] == 0

    # test if chp shuts down when hydrogen is ok, electricity is needed, but heat not
    stsv.values[electricity_target.global_index] = -2.5e3
    stsv.values[hydrogensoc.global_index] = 50
    stsv.values[buffer_temperature.global_index] = 40.5
    stsv.values[boiler_temperature.global_index] = 40

    for timestep in range(
        int(
            (chp_controller_config.min_operation_time_in_seconds / seconds_per_timestep)
            + 2
        )
    ):
        my_chp_controller.i_simulate(timestep, stsv, False)
        my_chp.i_simulate(timestep, stsv, False)

    stsv.values[electricity_target.global_index] = -2.5e3
    stsv.values[hydrogensoc.global_index] = 50
    stsv.values[buffer_temperature.global_index] = 40.5
    stsv.values[boiler_temperature.global_index] = 61

    for ttt in range(
        timestep_t,
        timestep_t
        + int(
            (chp_controller_config.min_idle_time_in_seconds / seconds_per_timestep) + 2
        ),
    ):
        my_chp_controller.i_simulate(ttt, stsv, False)
        my_chp.i_simulate(ttt, stsv, False)

    assert stsv.values[my_chp.thermal_power_output_building_channel.global_index] == 0
    assert stsv.values[my_chp.thermal_power_output_dhw_channel.global_index] == 0
    assert stsv.values[my_chp.electricity_output_channel.global_index] == 0
    assert stsv.values[my_chp.fuel_consumption_channel.global_index] == 0

    # test if chp shuts down when hydrogen is ok, heat is needed, but electricity not
    stsv.values[electricity_target.global_index] = -2.5e3
    stsv.values[hydrogensoc.global_index] = 50
    stsv.values[buffer_temperature.global_index] = 40.5
    stsv.values[boiler_temperature.global_index] = 40

    for timestep in range(
        int(
            (chp_controller_config.min_operation_time_in_seconds / seconds_per_timestep)
            + 2
        )
    ):
        my_chp_controller.i_simulate(timestep, stsv, False)
        my_chp.i_simulate(timestep, stsv, False)

    stsv.values[electricity_target.global_index] = 2.5e3
    stsv.values[hydrogensoc.global_index] = 50
    stsv.values[buffer_temperature.global_index] = 40.5
    stsv.values[boiler_temperature.global_index] = 61

    for ttt in range(
        timestep_t,
        timestep_t
        + int(
            (chp_controller_config.min_idle_time_in_seconds / seconds_per_timestep) + 2
        ),
    ):
        my_chp_controller.i_simulate(ttt, stsv, False)
        my_chp.i_simulate(ttt, stsv, False)

    assert stsv.values[my_chp.thermal_power_output_building_channel.global_index] == 0
    assert stsv.values[my_chp.thermal_power_output_dhw_channel.global_index] == 0
    assert stsv.values[my_chp.electricity_output_channel.global_index] == 0
    assert stsv.values[my_chp.fuel_consumption_channel.global_index] == 0
