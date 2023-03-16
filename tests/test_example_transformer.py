"""Test for the Example Transformer."""

# clean
import pytest
from hisim import component as cp
from hisim.components import example_transformer
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import log
from tests import functions_for_testing as fft


@pytest.mark.base
def test_example_transformer():
    """Test for the Example Transformer."""

    mysim: SimulationParameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)

    my_example_transformer_config = example_transformer.ExampleTransformerConfig.get_default_transformer()
    print("\n")
    log.information("default transformer config " + str(my_example_transformer_config) + "\n")
    my_example_transformer = example_transformer.ExampleTransformer(config=my_example_transformer_config, my_simulation_parameters=mysim)

    # Define outputs
    transformerinput1_output = cp.ComponentOutput(
        object_name="source",
        field_name="transformerinput1",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
        output_description="Source 2"
    )
    transformerinput2_output = cp.ComponentOutput(
        object_name="source",
        field_name="transformerinput2",
        load_type=lt.LoadTypes.ANY,
        unit=lt.Units.ANY,
        output_description="Source 2"
    )
    my_example_transformer.input1.source_output = transformerinput1_output
    my_example_transformer.input2.source_output = transformerinput2_output

    number_of_outputs = fft.get_number_of_outputs([my_example_transformer, transformerinput1_output, transformerinput2_output])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_example_transformer, transformerinput1_output, transformerinput2_output])
    stsv.values[transformerinput1_output.global_index] = 50  # fake input
    stsv.values[transformerinput2_output.global_index] = 10  # fake input

    # Test Simulation
    timestep = 10 * 60
    log.information("timestep = " + str(timestep))
    log.information("transformer input1 = " + str(stsv.values[transformerinput1_output.global_index]))
    log.information("transformer input2= " + str(stsv.values[transformerinput2_output.global_index]) + "\n")

    my_example_transformer.i_simulate(timestep, stsv, False)
    log.information("Output values after simulation: ")
    log.information("output1 = " + str(stsv.values[my_example_transformer.output1.global_index]))
    log.information("output2 = " + str(stsv.values[my_example_transformer.output2.global_index]))
    log.information("output values = " + str(stsv.values) + "\n")

    assert 50 == stsv.values[transformerinput1_output.global_index]
    assert 10 == stsv.values[transformerinput2_output.global_index]
    assert 250 == stsv.values[my_example_transformer.output1.global_index]
    assert 10000 == stsv.values[my_example_transformer.output2.global_index]
