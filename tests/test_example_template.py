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
def test_example_template() -> None:
    """Test example template component behavior with stateful and stateless outputs.

    Validates that the example template component correctly processes an input
    signal (50 W electricity) and produces the expected stateful output
    (3000 Wh at timestep 600, 6000 Wh at timestep 601) and stateless output
    (51.0 at both timesteps) based on its internal logic.
    """

    mysim: SimulationParameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )

    my_example_template_config = (
        example_template.ComponentNameConfig.get_default_template_component()
    )
    print("\n")
    log.information(
        "default componentname config " + str(my_example_template_config) + "\n"
    )
    my_example_template = example_template.ComponentName(
        config=my_example_template_config, my_simulation_parameters=mysim
    )

    # Define outputs
    input_from_another_component_output = cp.ComponentOutput(
        object_name="source",
        field_name="input_from_another_component",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
    )
    my_example_template.input_from_other_component.source_output = (
        input_from_another_component_output
    )

    number_of_outputs: int = fft.get_number_of_outputs(
        [my_example_template, input_from_another_component_output]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [my_example_template, input_from_another_component_output]
    )
    stsv.values[input_from_another_component_output.global_index] = 50  # fake input

    # Test Simulation
    timestep: int = 10 * 60
    log.information("timestep = " + str(timestep))
    log.information(
        "input_from_another_component_output = "
        + str(stsv.values[input_from_another_component_output.global_index])
        + "\n"
    )

    my_example_template.i_simulate(timestep, stsv, False)
    log.information("Output values after simulation: ")
    log.information(
        "output with state = "
        + str(stsv.values[my_example_template.output_with_state.global_index])
    )
    log.information(
        "output without state = "
        + str(stsv.values[my_example_template.output_without_state.global_index])
    )
    log.information("output values = " + str(stsv.values) + "\n")

    assert 50 == stsv.values[input_from_another_component_output.global_index]
    assert 3000 == stsv.values[my_example_template.output_with_state.global_index]
    assert 51.0 == stsv.values[my_example_template.output_without_state.global_index]

    timestep = 10 * 60 + 1
    log.information("timestep = " + str(timestep))
    log.information(
        "input_from_another_component_output = "
        + str(stsv.values[input_from_another_component_output.global_index])
        + "\n"
    )

    my_example_template.i_simulate(timestep, stsv, False)
    log.information("Output values after simulation: ")
    log.information(
        "output with state = "
        + str(stsv.values[my_example_template.output_with_state.global_index])
    )
    log.information(
        "output without state = "
        + str(stsv.values[my_example_template.output_without_state.global_index])
    )
    log.information("output values = " + str(stsv.values))

    assert 50 == stsv.values[input_from_another_component_output.global_index]
    assert 6000 == stsv.values[my_example_template.output_with_state.global_index]
    assert 51.0 == stsv.values[my_example_template.output_without_state.global_index]


@pytest.mark.base
def test_get_default_template_component_no_args() -> None:
    """``get_default_template_component`` returns hardcoded defaults when called with no arguments."""
    config = example_template.ComponentNameConfig.get_default_template_component()
    assert config.building_name == "BUI1"
    assert config.name == "ComponentName default"
    assert config.loadtype == lt.LoadTypes.ELECTRICITY
    assert config.unit == lt.Units.WATT


@pytest.mark.base
def test_get_default_template_component_custom_building_name() -> None:
    """Passing ``building_name`` only changes that field; all other fields keep their defaults."""
    config = example_template.ComponentNameConfig.get_default_template_component(
        building_name="MyHouse"
    )
    assert config.building_name == "MyHouse"
    assert config.name == "ComponentName default"
    assert config.loadtype == lt.LoadTypes.ELECTRICITY
    assert config.unit == lt.Units.WATT


@pytest.mark.base
def test_get_default_template_component_empty_building_name() -> None:
    """An empty ``building_name`` is passed through without validation."""
    config = example_template.ComponentNameConfig.get_default_template_component(
        building_name=""
    )
    assert config.building_name == ""
    assert config.name == "ComponentName default"
    assert config.loadtype == lt.LoadTypes.ELECTRICITY
    assert config.unit == lt.Units.WATT


@pytest.mark.base
def test_get_main_classname() -> None:
    """``get_main_classname`` returns the full module path plus class name of ``ComponentName``.

    This pins the contract that the config's main class is ``ComponentName`` by
    comparing against ``ComponentName.get_full_classname()`` as well as the
    expected literal string.
    """
    classname = example_template.ComponentNameConfig.get_main_classname()
    assert classname == example_template.ComponentName.get_full_classname()
    assert classname == "hisim.components.example_template.ComponentName"
