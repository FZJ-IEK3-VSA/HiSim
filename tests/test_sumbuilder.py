"""Tests for the SumBuilder components."""

# clean
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import sumbuilder
from hisim.simulationparameters import SimulationParameters
from hisim.config import ComponentID
from tests import functions_for_testing as fft


@pytest.mark.base
def test_sum_builder_for_three_inputs() -> None:
    """Verify SumBuilderForThreeInputs outputs the sum of its three inputs.

    Connects three fake ComponentOutput inputs, runs two simulation steps
    with different value sets (including a negative input), and asserts the
    component's output equals the arithmetic sum of the inputs each step.
    """
    mysim: SimulationParameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)

    sum_builder_config = sumbuilder.SumBuilderConfig.get_sumbuilder_default_config()
    my_sum = sumbuilder.SumBuilderForThreeInputs(config=sum_builder_config, my_simulation_parameters=mysim)

    # Define fake inputs
    fake_input1 = cp.ComponentOutput(
        object_name="fake1",
        field_name="input1",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
        component_id=ComponentID("fake1"),
    )
    fake_input2 = cp.ComponentOutput(
        object_name="fake2",
        field_name="input2",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
        component_id=ComponentID("fake2"),
    )
    fake_input3 = cp.ComponentOutput(
        object_name="fake3",
        field_name="input3",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
        component_id=ComponentID("fake3"),
    )

    # Connect fake inputs using source_output
    my_sum.input1.source_output = fake_input1
    my_sum.input2.source_output = fake_input2
    my_sum.input3.source_output = fake_input3

    # Count outputs for all components including fake inputs
    number_of_outputs = fft.get_number_of_outputs([my_sum, fake_input1, fake_input2, fake_input3])
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
def test_sum_builder_for_two_inputs() -> None:
    """Verify SumBuilderForTwoInputs outputs the sum of its two inputs.

    Connects two fake ComponentOutput inputs, runs two simulation steps
    with different value sets (including a negative input), and asserts the
    component's output equals the arithmetic sum of the inputs each step.
    """
    mysim: SimulationParameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)

    sum_builder_config = sumbuilder.SumBuilderConfig.get_sumbuilder_default_config()
    my_sum = sumbuilder.SumBuilderForTwoInputs(config=sum_builder_config, my_simulation_parameters=mysim)

    # Define fake inputs
    fake_input1 = cp.ComponentOutput(
        object_name="fake1",
        field_name="input1",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
        component_id=ComponentID("fake1"),
    )
    fake_input2 = cp.ComponentOutput(
        object_name="fake2",
        field_name="input2",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
        component_id=ComponentID("fake2"),
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


def _make_calculate_operation() -> sumbuilder.CalculateOperation:
    """Create a fresh ``CalculateOperation`` instance for testing.

    Returns a component with no inputs and no operations yet, ready for
    ``add_numbered_input`` / ``add_operation`` calls.
    """
    mysim: SimulationParameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    config = sumbuilder.SumBuilderConfig.get_sumbuilder_default_config()
    return sumbuilder.CalculateOperation(config=config, my_simulation_parameters=mysim)


@pytest.mark.base
def test_calculate_operation_subtract() -> None:
    """Verify CalculateOperation performs Subtract on two inputs.

    Adds two inputs and one Subtract operation, then simulates and checks
    that the output equals ``input1 - input2``.
    """
    calc = _make_calculate_operation()

    # Two inputs need one operation between them.
    input1 = calc.add_numbered_input()
    calc.add_operation("Subtract")
    input2 = calc.add_numbered_input()
    assert calc.operations == ["Subtract"]

    fake_output1 = cp.ComponentOutput(
        object_name="fake1",
        field_name="out1",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
        component_id=ComponentID("fake1"),
    )
    fake_output2 = cp.ComponentOutput(
        object_name="fake2",
        field_name="out2",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
        component_id=ComponentID("fake2"),
    )
    input1.source_output = fake_output1
    input2.source_output = fake_output2

    number_of_outputs = fft.get_number_of_outputs([calc, fake_output1, fake_output2])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)
    fft.add_global_index_of_components([calc, fake_output1, fake_output2])

    stsv.values[fake_output1.global_index] = 100.0
    stsv.values[fake_output2.global_index] = 30.0

    calc.i_simulate(timestep=0, stsv=stsv, force_convergence=False)

    assert stsv.values[calc.output1.global_index] == pytest.approx(70.0)


@pytest.mark.base
def test_calculate_operation_add_operation_invalid_name_raises_value_error() -> None:
    """Adding an unknown operation name raises ValueError, not bare Exception."""
    calc = _make_calculate_operation()
    calc.add_numbered_input()  # balance: 1 input, 0 operations
    with pytest.raises(ValueError, match="Operation not implemented!"):
        calc.add_operation("Power")


@pytest.mark.base
def test_calculate_operation_too_many_inputs_raises_value_error() -> None:
    """Adding an operation when too many inputs are connected raises ValueError."""
    calc = _make_calculate_operation()
    calc.add_numbered_input()
    calc.add_numbered_input()  # 2 inputs, 0 operations → 1 operation missing
    with pytest.raises(ValueError, match="1 operations are missing!"):
        calc.add_operation("Sum")


@pytest.mark.base
def test_calculate_operation_too_few_inputs_raises_value_error() -> None:
    """Adding an operation with no inputs connected raises ValueError."""
    calc = _make_calculate_operation()
    # 0 inputs, 0 operations → need 1 input before an operation can be added
    with pytest.raises(ValueError, match="1 operations are missing!"):
        calc.add_operation("Sum")


@pytest.mark.base
def test_calculate_operation_simulate_invalid_operation_raises_value_error() -> None:
    """An invalid operation string at simulate time raises ValueError, not bare Exception.

    This bypasses ``add_operation`` validation by directly injecting a bogus
    operation string into ``calc.operations`` to exercise the defensive guard
    in ``i_simulate``.
    """
    calc = _make_calculate_operation()

    input1 = calc.add_numbered_input()
    # Inject a valid operation count but an invalid name directly.
    calc.operations = ["Bogus"]
    input2 = calc.add_numbered_input()

    fake_output1 = cp.ComponentOutput(
        object_name="fake1",
        field_name="out1",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
        component_id=ComponentID("fake1"),
    )
    fake_output2 = cp.ComponentOutput(
        object_name="fake2",
        field_name="out2",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
        component_id=ComponentID("fake2"),
    )
    input1.source_output = fake_output1
    input2.source_output = fake_output2

    number_of_outputs = fft.get_number_of_outputs([calc, fake_output1, fake_output2])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)
    fft.add_global_index_of_components([calc, fake_output1, fake_output2])

    stsv.values[fake_output1.global_index] = 10.0
    stsv.values[fake_output2.global_index] = 5.0

    with pytest.raises(ValueError, match="Operation invalid!"):
        calc.i_simulate(timestep=0, stsv=stsv, force_convergence=False)
