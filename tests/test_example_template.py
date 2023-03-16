"""Test for the Example Template."""

# clean
import pytest
from hisim import component as cp
from hisim.components import example_template
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import log
from tests import functions_for_testing as fft


@pytest.mark.base
def test_example_template():
    """Test for the Example Template."""

    mysim: SimulationParameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)

    my_example_template_config = example_template.ComponentNameConfig.get_default_template_component()
    print("\n")
    log.information("default componentname config " + str(my_example_template_config) + "\n")
    my_example_template = example_template.ComponentName(config=my_example_template_config, my_simulation_parameters=mysim)

    # Define outputs
    input_from_another_component_output = cp.ComponentOutput(
        object_name="source",
        field_name="input_from_another_component",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
    )
    my_example_template.input_from_other_component.source_output = input_from_another_component_output

    number_of_outputs = fft.get_number_of_outputs([my_example_template, input_from_another_component_output])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_example_template, input_from_another_component_output])
    stsv.values[input_from_another_component_output.global_index] = 50  # fake input

    # Test Simulation
    timestep = 10 * 60
    log.information("timestep = " + str(timestep))
    log.information("input_from_another_component_output = " + str(stsv.values[input_from_another_component_output.global_index]) + "\n")

    my_example_template.i_simulate(timestep, stsv, False)
    log.information("Output values after simulation: ")
    log.information("output with state = " + str(stsv.values[my_example_template.output_with_state.global_index]))
    log.information("output without state = " + str(stsv.values[my_example_template.output_without_state.global_index]))
    log.information("output values = " + str(stsv.values) + "\n")

    assert 50 == stsv.values[input_from_another_component_output.global_index]
    assert 3000 == stsv.values[my_example_template.output_with_state.global_index]
    assert 51.0 == stsv.values[my_example_template.output_without_state.global_index]

    timestep = 10 * 60 + 1
    log.information("timestep = " + str(timestep))
    log.information("input_from_another_component_output = " + str(stsv.values[input_from_another_component_output.global_index]) + "\n")

    my_example_template.i_simulate(timestep, stsv, False)
    log.information("Output values after simulation: ")
    log.information("output with state = " + str(stsv.values[my_example_template.output_with_state.global_index]))
    log.information("output without state = " + str(stsv.values[my_example_template.output_without_state.global_index]))
    log.information("output values = " + str(stsv.values))

    assert 50 == stsv.values[input_from_another_component_output.global_index]
    assert 6000 == stsv.values[my_example_template.output_with_state.global_index]
    assert 51.0 == stsv.values[my_example_template.output_without_state.global_index]
