"""Tests for the SumBuilder components."""

# clean
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import sumbuilder
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@pytest.mark.base
def test_sum_builder_for_three_inputs():
    """Test for SumBuilderForThreeInputs."""
    mysim: SimulationParameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )

    my_sum_config = sumbuilder.SumBuilderConfig.get_sumbuilder_default_config()
    my_sum = sumbuilder.SumBuilderForThreeInputs(
        config=my_sum_config, my_simulation_parameters=mysim
    )

    # Define fake inputs
    fake_input1 = cp.ComponentOutput(
        object_name="fake1",
        field_name="input1",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
    )
    fake_input2 = cp.ComponentOutput(
        object_name="fake2",
        field_name="input2",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
    )
    fake_input3 = cp.ComponentOutput(
        object_name="fake3",
        field_name="input3",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
    )

    # Connect fake inputs using source_output
    my_sum.input1.source_output = fake_input1
    my_sum.input2.source_output = fake_input2
    my_sum.input3.source_output = fake_input3

    # Count outputs for all components including fake inputs
    number_of_outputs = fft.get_number_of_outputs(
        [my_sum, fake_input1, fake_input2, fake_input3]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index
    fft.add_global_index_of_components([my_sum, fake_input1, fake_input2, fake_input3])

    # Set values for fake inputs
    stsv.values[fake_input1.global_index] = 10.0
    stsv.values[fake_input2.global_index] = 20.0
    stsv.values[fake_input3.global_index] = 30.0

    # Test simulation
    timestep = 60
    my_sum.i_simulate(timestep, stsv, False)

    # Verify output is sum of inputs
    expected_sum = 10.0 + 20.0 + 30.0  # = 60.0
    assert stsv.values[my_sum.output1.global_index] == expected_sum

    # Test with different values
    stsv.values[fake_input1.global_index] = 5.5
    stsv.values[fake_input2.global_index] = -2.0
    stsv.values[fake_input3.global_index] = 10.5

    my_sum.i_simulate(timestep + 1, stsv, False)

    expected_sum = 5.5 + (-2.0) + 10.5  # = 14.0
    assert stsv.values[my_sum.output1.global_index] == expected_sum


@pytest.mark.base
def test_sum_builder_for_two_inputs():
    """Test for SumBuilderForTwoInputs."""
    mysim: SimulationParameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )

    my_sum_config = sumbuilder.SumBuilderConfig.get_sumbuilder_default_config()
    my_sum = sumbuilder.SumBuilderForTwoInputs(
        config=my_sum_config, my_simulation_parameters=mysim
    )

    # Define fake inputs
    fake_input1 = cp.ComponentOutput(
        object_name="fake1",
        field_name="input1",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
    )
    fake_input2 = cp.ComponentOutput(
        object_name="fake2",
        field_name="input2",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
    )

    # Connect fake inputs using source_output
    my_sum.input1.source_output = fake_input1
    my_sum.input2.source_output = fake_input2

    # Count outputs for all components including fake inputs
    number_of_outputs = fft.get_number_of_outputs([my_sum, fake_input1, fake_input2])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index
    fft.add_global_index_of_components([my_sum, fake_input1, fake_input2])

    # Set values for fake inputs
    stsv.values[fake_input1.global_index] = 15.0
    stsv.values[fake_input2.global_index] = 25.0

    # Test simulation
    timestep = 60
    my_sum.i_simulate(timestep, stsv, False)

    # Verify output is sum of inputs
    expected_sum = 15.0 + 25.0  # = 40.0
    assert stsv.values[my_sum.output1.global_index] == expected_sum

    # Test with negative values
    stsv.values[fake_input1.global_index] = -10.0
    stsv.values[fake_input2.global_index] = 50.0

    my_sum.i_simulate(timestep + 1, stsv, False)

    expected_sum = -10.0 + 50.0  # = 40.0
    assert stsv.values[my_sum.output1.global_index] == expected_sum
