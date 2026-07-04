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
def test_h2_storage() -> None:
    """Verify charging and discharging behavior of the generic hydrogen storage.

    Runs a one-day simulation at 60-second timesteps and checks that the
    storage state-of-charge increases as expected when hydrogen is fed in
    (charging step) and returns to zero when hydrogen is drawn out
    (discharging step).
    """

    seconds_per_timestep: int = 60
    my_simulation_parameters: SimulationParameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    my_h2_storage_config: generic_hydrogen_storage.GenericHydrogenStorageConfig = (
        generic_hydrogen_storage.GenericHydrogenStorageConfig.get_default_config()
    )
    my_h2_storage: generic_hydrogen_storage.GenericHydrogenStorage = generic_hydrogen_storage.GenericHydrogenStorage(
        config=my_h2_storage_config, my_simulation_parameters=my_simulation_parameters
    )

    # Set Fake Inputs
    h2_input: cp.ComponentOutput = cp.ComponentOutput(
        "FakeHydrogenInput", "HydrogenInput", lt.LoadTypes.GREEN_HYDROGEN, lt.Units.KG_PER_SEC
    )
    h2_output: cp.ComponentOutput = cp.ComponentOutput(
        "FakeHydrogenOutput",
        "HydrogenOutput",
        lt.LoadTypes.GREEN_HYDROGEN,
        lt.Units.KG_PER_SEC,
    )

    number_of_outputs: int = fft.get_number_of_outputs([my_h2_storage, h2_input, h2_output])
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
        == 1e-2 * seconds_per_timestep / my_h2_storage_config.max_capacity_in_kg
    )

    # test if storage is discharged
    stsv.values[h2_input.global_index] = 0  # kg/s
    stsv.values[h2_output.global_index] = 1e-4

    my_h2_storage.i_simulate(1, stsv, False)

    assert stsv.values[my_h2_storage.hydrogen_soc.global_index] == 0


@pytest.mark.base
def test_h2_storage_simultaneous_charge_dominates() -> None:
    """When charging exceeds discharging, the net flow charges the storage.

    Exercises the simultaneous-charge/discharge branch in ``i_simulate``:
    ``net_hydrogen_rate = charging_rate - discharging_rate`` is positive, so
    the storage is charged by the net amount and nothing is discharged.
    """
    seconds_per_timestep: int = 60
    my_simulation_parameters: SimulationParameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    my_h2_storage_config: generic_hydrogen_storage.GenericHydrogenStorageConfig = (
        generic_hydrogen_storage.GenericHydrogenStorageConfig.get_default_config()
    )
    my_h2_storage: generic_hydrogen_storage.GenericHydrogenStorage = generic_hydrogen_storage.GenericHydrogenStorage(
        config=my_h2_storage_config, my_simulation_parameters=my_simulation_parameters
    )

    h2_input: cp.ComponentOutput = cp.ComponentOutput(
        "FakeHydrogenInput", "HydrogenInput", lt.LoadTypes.GREEN_HYDROGEN, lt.Units.KG_PER_SEC
    )
    h2_output: cp.ComponentOutput = cp.ComponentOutput(
        "FakeHydrogenOutput", "HydrogenOutput", lt.LoadTypes.GREEN_HYDROGEN, lt.Units.KG_PER_SEC,
    )

    number_of_outputs: int = fft.get_number_of_outputs([my_h2_storage, h2_input, h2_output])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_h2_storage.hydrogen_output_channel.source_output = h2_output
    my_h2_storage.hydrogen_input_channel.source_output = h2_input
    fft.add_global_index_of_components([my_h2_storage, h2_input, h2_output])

    # Simultaneous charging (2e-4 kg/s) and discharging (1e-4 kg/s):
    # net flow is +1e-4 kg/s, so the storage charges by the net amount.
    stsv.values[h2_input.global_index] = 2e-4  # kg/s
    stsv.values[h2_output.global_index] = 1e-4  # kg/s

    my_h2_storage.i_simulate(0, stsv, False)

    expected_soc = 100 * (1e-4 * seconds_per_timestep) / my_h2_storage_config.max_capacity_in_kg
    assert stsv.values[my_h2_storage.hydrogen_soc.global_index] == pytest.approx(expected_soc)


@pytest.mark.base
def test_h2_storage_simultaneous_discharge_dominates() -> None:
    """When discharging exceeds charging, the net flow discharges the storage.

    Exercises the simultaneous-charge/discharge branch in ``i_simulate``:
    ``net_hydrogen_rate`` is negative, so the storage is discharged by the
    magnitude of the net flow and nothing is charged.
    """
    seconds_per_timestep: int = 60
    my_simulation_parameters: SimulationParameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    my_h2_storage_config: generic_hydrogen_storage.GenericHydrogenStorageConfig = (
        generic_hydrogen_storage.GenericHydrogenStorageConfig.get_default_config()
    )
    my_h2_storage: generic_hydrogen_storage.GenericHydrogenStorage = generic_hydrogen_storage.GenericHydrogenStorage(
        config=my_h2_storage_config, my_simulation_parameters=my_simulation_parameters
    )

    h2_input: cp.ComponentOutput = cp.ComponentOutput(
        "FakeHydrogenInput", "HydrogenInput", lt.LoadTypes.GREEN_HYDROGEN, lt.Units.KG_PER_SEC
    )
    h2_output: cp.ComponentOutput = cp.ComponentOutput(
        "FakeHydrogenOutput", "HydrogenOutput", lt.LoadTypes.GREEN_HYDROGEN, lt.Units.KG_PER_SEC,
    )

    number_of_outputs: int = fft.get_number_of_outputs([my_h2_storage, h2_input, h2_output])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_h2_storage.hydrogen_output_channel.source_output = h2_output
    my_h2_storage.hydrogen_input_channel.source_output = h2_input
    fft.add_global_index_of_components([my_h2_storage, h2_input, h2_output])

    # First charge the storage with 2e-4 kg/s so it has hydrogen to release.
    stsv.values[h2_input.global_index] = 2e-4  # kg/s
    stsv.values[h2_output.global_index] = 0
    my_h2_storage.i_simulate(0, stsv, False)
    fill_after_charge = 2e-4 * seconds_per_timestep  # kg
    assert stsv.values[my_h2_storage.hydrogen_soc.global_index] == pytest.approx(
        100 * fill_after_charge / my_h2_storage_config.max_capacity_in_kg
    )

    # Simultaneous charging (1e-4 kg/s) and discharging (2e-4 kg/s):
    # net flow is -1e-4 kg/s, so the storage discharges by the net magnitude.
    stsv.values[h2_input.global_index] = 1e-4  # kg/s
    stsv.values[h2_output.global_index] = 2e-4  # kg/s
    my_h2_storage.i_simulate(1, stsv, False)

    expected_fill = fill_after_charge - 1e-4 * seconds_per_timestep  # kg
    assert stsv.values[my_h2_storage.hydrogen_soc.global_index] == pytest.approx(
        100 * expected_fill / my_h2_storage_config.max_capacity_in_kg
    )
