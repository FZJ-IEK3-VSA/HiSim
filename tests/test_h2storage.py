"""Test for H2 storage.

Created on Thu Jul 21 10:08:01 2022.
@author: Johanna
"""
# -*- coding: utf-8 -*-
import pytest
from tests import functions_for_testing as fft
from hisim import component as cp

from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.components import generic_hydrogen_storage


@pytest.mark.base
def test_chp_system():
    """Test chp system."""

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    my_h2_storage_config = (
        generic_hydrogen_storage.GenericHydrogenStorageConfig.get_default_config()
    )
    my_h2_storage = generic_hydrogen_storage.GenericHydrogenStorage(
        config=my_h2_storage_config, my_simulation_parameters=my_simulation_parameters
    )

    # Set Fake Inputs
    h2_input = cp.ComponentOutput(
        "FakeHydrogenInput", "HydrogenInput", lt.LoadTypes.GREEN_HYDROGEN, lt.Units.KG_PER_SEC
    )
    h2_output = cp.ComponentOutput(
        "FakeHydrogenOutput",
        "HydrogenOutput",
        lt.LoadTypes.GREEN_HYDROGEN,
        lt.Units.KG_PER_SEC,
    )

    number_of_outputs = fft.get_number_of_outputs([my_h2_storage, h2_input, h2_output])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_h2_storage.hydrogen_output_channel.source_output = h2_output
    my_h2_storage.hydrogen_input_channel.source_output = h2_input

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_h2_storage, h2_input, h2_output])

    # test if storage is charged
    stsv.values[h2_input.global_index] = 1e-4  # kg/s
    stsv.values[h2_output.global_index] = 0

    my_h2_storage.i_simulate(0, stsv, False)

    assert (
        stsv.values[my_h2_storage.hydrogen_soc.global_index]
        == 1e-2 * seconds_per_timestep / my_h2_storage_config.max_capacity
    )

    # test if storage is discharged
    stsv.values[h2_input.global_index] = 0  # kg/s
    stsv.values[h2_output.global_index] = 1e-4

    my_h2_storage.i_simulate(1, stsv, False)

    assert stsv.values[my_h2_storage.hydrogen_soc.global_index] == 0
