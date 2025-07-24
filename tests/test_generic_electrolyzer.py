"""Test for generic electrolyzer.

Created on Thu Jul 21 20:04:59 2022

@author: Johanna
"""
# -*- coding: utf-8 -*-
import pytest

from tests import functions_for_testing as fft
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.components import generic_electrolyzer, controller_l1_electrolyzer


@pytest.mark.base
def test_chp_system():
    """Test chp system."""

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    my_electrolyzer_config = (
        generic_electrolyzer.GenericElectrolyzerConfig.get_default_config(p_el=2400)
    )
    my_electrolyzer = generic_electrolyzer.GenericElectrolyzer(
        config=my_electrolyzer_config, my_simulation_parameters=my_simulation_parameters
    )
    my_electrolyzer_controller_config = (
        controller_l1_electrolyzer.L1ElectrolyzerControllerConfig.get_default_config()
    )
    my_electrolyzer_controller = (
        controller_l1_electrolyzer.L1GenericElectrolyzerController(
            config=my_electrolyzer_controller_config,
            my_simulation_parameters=my_simulation_parameters,
        )
    )

    # Set Fake Inputs
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
        [my_electrolyzer, my_electrolyzer_controller, electricity_target, hydrogensoc]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_electrolyzer_controller.electricity_target_channel.source_output = (
        electricity_target
    )
    my_electrolyzer_controller.hydrogen_soc_channel.source_output = hydrogensoc
    my_electrolyzer.electricity_target_channel.source_output = (
        my_electrolyzer_controller.available_electicity_output_channel
    )

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [my_electrolyzer, my_electrolyzer_controller, electricity_target, hydrogensoc]
    )

    # test if electrolyzer runs when capacity  in hydrogen storage and electricty available
    stsv.values[electricity_target.global_index] = 1.8e3
    stsv.values[hydrogensoc.global_index] = 50

    for timestep in range(
        int(
            (
                my_electrolyzer_controller_config.min_idle_time_in_seconds
                / seconds_per_timestep
            )
            + 2
        )
    ):
        my_electrolyzer_controller.i_simulate(timestep, stsv, False)
        my_electrolyzer.i_simulate(timestep, stsv, False)

    assert stsv.values[my_electrolyzer.electricity_output_channel.global_index] == 1.8e3
    assert stsv.values[my_electrolyzer.hydrogen_output_channel.global_index] > 5e-5

    # test if electrolyzer shuts down when too much hydrogen in storage and electricty available
    stsv.values[electricity_target.global_index] = 1.8e3
    stsv.values[hydrogensoc.global_index] = 99

    for timestep_t in range(
        timestep,
        timestep
        + int(
            (
                my_electrolyzer_controller_config.min_operation_time_in_seconds
                / seconds_per_timestep
            )
            + 2
        ),
    ):
        my_electrolyzer_controller.i_simulate(timestep_t, stsv, False)
        my_electrolyzer.i_simulate(timestep_t, stsv, False)

    assert stsv.values[my_electrolyzer.electricity_output_channel.global_index] == 0
    assert stsv.values[my_electrolyzer.hydrogen_output_channel.global_index] == 0

    # test if electrolyzer shuts down when hydrogen is ok, but no electricity available
    stsv.values[electricity_target.global_index] = 1.8e3
    stsv.values[hydrogensoc.global_index] = 50

    for ttt in range(
        timestep_t,
        timestep_t
        + int(
            (
                my_electrolyzer_controller_config.min_idle_time_in_seconds
                / seconds_per_timestep
            )
            + 2
        ),
    ):
        my_electrolyzer_controller.i_simulate(ttt, stsv, False)
        my_electrolyzer.i_simulate(ttt, stsv, False)

    stsv.values[electricity_target.global_index] = 0
    stsv.values[hydrogensoc.global_index] = 50

    for timestep_it in range(
        ttt,
        ttt
        + int(
            (
                my_electrolyzer_controller_config.min_operation_time_in_seconds
                / seconds_per_timestep
            )
            + 2
        ),
    ):
        my_electrolyzer_controller.i_simulate(timestep_it, stsv, False)
        my_electrolyzer.i_simulate(timestep_it, stsv, False)

    assert stsv.values[my_electrolyzer.electricity_output_channel.global_index] == 0
    assert stsv.values[my_electrolyzer.hydrogen_output_channel.global_index] == 0
