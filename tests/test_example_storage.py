"""Test for the Example Storage."""

# clean

import pytest

from hisim import component as cp
from hisim.components import example_storage
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import log
from tests import functions_for_testing as fft


@pytest.mark.base
def test_example_storage() -> None:
    """Verify SimpleStorage accumulates net charge into its fill state.

    Constructs a default thermal SimpleStorage, feeds it fake charging (30 kWh)
    and discharging (-10 kWh) outputs, then asserts that after `i_simulate` the
    reported `current_fill` reflects the net energy added (20 kWh at timestep
    300, 40 kWh at timestep 301).
    """

    simulation_parameters: SimulationParameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )

    my_example_storage_config: example_storage.SimpleStorageConfig = (
        example_storage.SimpleStorageConfig.get_default_thermal_storage()
    )
    my_example_storage: example_storage.SimpleStorage = example_storage.SimpleStorage(
        config=my_example_storage_config, my_simulation_parameters=simulation_parameters
    )

    # Define outputs
    charging_output: cp.ComponentOutput = cp.ComponentOutput(
        object_name="source",
        field_name="charging",
        load_type=lt.LoadTypes.WARM_WATER,
        unit=lt.Units.KWH,
    )
    discharging_output: cp.ComponentOutput = cp.ComponentOutput(
        object_name="source",
        field_name="discharging",
        load_type=lt.LoadTypes.WARM_WATER,
        unit=lt.Units.KWH,
    )

    my_example_storage.charging_input.source_output = charging_output
    my_example_storage.discharging_input.source_output = discharging_output

    number_of_outputs: int = fft.get_number_of_outputs(
        [my_example_storage, charging_output, discharging_output]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [my_example_storage, charging_output, discharging_output]
    )
    stsv.values[charging_output.global_index] = 30  # fake charg input
    stsv.values[discharging_output.global_index] = -10  # fake discharg input

    timestep: int = 300

    print("\n")
    log.information(f"timestep = {timestep}")
    log.information(f"fill state (in the beginning) = {my_example_storage.state.fill}")
    log.information(f"storage capacity = {my_example_storage.capacity}\n")
    log.information(f"charging output = {stsv.values[charging_output.global_index]}")
    log.information(f"discharging output  = {stsv.values[discharging_output.global_index]}\n")

    # Test current storage fill state before running the simulation
    assert 0 == stsv.values[my_example_storage.current_fill.global_index]
    my_example_storage.i_restore_state()
    my_example_storage.i_simulate(timestep, stsv, False)

    log.information(f"fill state (after charging and discharging) = {my_example_storage.state.fill}\n")
    # Test charging of the storage
    assert 30 == stsv.values[charging_output.global_index]
    # Test discharging of the storage
    assert -10 == stsv.values[discharging_output.global_index]
    # Test current storage fill state
    assert 20 == stsv.values[my_example_storage.current_fill.global_index]

    timestep = 301
    log.information(f"timestep = {timestep}")
    my_example_storage.i_simulate(timestep, stsv, False)
    log.information(f"fill state (after charging and discharging) = {my_example_storage.state.fill}\n")
    # Test current storage fill state
    assert 40 == stsv.values[my_example_storage.current_fill.global_index]
