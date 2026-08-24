"""Test for the Example Template."""

# clean
import pytest
from hisim import component as cp
from hisim.components import example_template
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import log
from hisim.config import ComponentID
from tests import functions_for_testing as fft


@pytest.mark.base
def test_example_template() -> None:
    """Test example template component behavior with stateful and stateless outputs.

    Validates that the example template component correctly processes an input
    signal (50 W electricity) and produces the expected stateful output
    (3000 Wh at timestep 600, 6000 Wh at timestep 601) and stateless output
    (51.0 at both timesteps) based on its internal logic.
    """

    mysim: SimulationParameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)

    my_example_template_config = example_template.ComponentNameConfig.get_default_template_component()
    print("\n")
    log.information(f"default componentname config {my_example_template_config}\n")
    my_example_template = example_template.ComponentName(
        config=my_example_template_config, my_simulation_parameters=mysim
    )

    # Define outputs
    input_from_another_component_output = cp.ComponentOutput(
        object_name="source",
        field_name="input_from_another_component",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        component_id=ComponentID("source"),
    )
    my_example_template.input_from_other_component.source_output = input_from_another_component_output

    number_of_outputs: int = fft.get_number_of_outputs([my_example_template, input_from_another_component_output])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_example_template, input_from_another_component_output])
    stsv.values[input_from_another_component_output.global_index] = 50  # fake input

    # Test Simulation
    timestep: int = 10 * 60
    log.information(f"timestep = {timestep}")
    log.information(
        "input_from_another_component_output = " f"{stsv.values[input_from_another_component_output.global_index]}\n"
    )

    my_example_template.i_simulate(timestep, stsv, False)
    log.information("Output values after simulation: ")
    log.information("output with state = " f"{stsv.values[my_example_template.output_with_state.global_index]}")
    log.information("output without state = " f"{stsv.values[my_example_template.output_without_state.global_index]}")
    log.information(f"output values = {stsv.values}\n")

    assert 50 == stsv.values[input_from_another_component_output.global_index]
    assert 3000 == stsv.values[my_example_template.output_with_state.global_index]
    assert 51.0 == stsv.values[my_example_template.output_without_state.global_index]

    timestep = 10 * 60 + 1
    log.information(f"timestep = {timestep}")
    log.information(
        "input_from_another_component_output = " f"{stsv.values[input_from_another_component_output.global_index]}\n"
    )

    my_example_template.i_simulate(timestep, stsv, False)
    log.information("Output values after simulation: ")
    log.information("output with state = " f"{stsv.values[my_example_template.output_with_state.global_index]}")
    log.information("output without state = " f"{stsv.values[my_example_template.output_without_state.global_index]}")
    log.information(f"output values = {stsv.values}")

    assert 50 == stsv.values[input_from_another_component_output.global_index]
    assert 6000 == stsv.values[my_example_template.output_with_state.global_index]
    assert 51.0 == stsv.values[my_example_template.output_without_state.global_index]


@pytest.mark.base
def test_get_default_template_component_no_args() -> None:
    """``get_default_template_component`` returns hardcoded defaults when called with no arguments."""
    config = example_template.ComponentNameConfig.get_default_template_component()
    assert config.component_id.building is None
    assert config.component_id.name == "ComponentName default"
    assert config.loadtype == lt.LoadTypes.ELECTRICITY
    assert config.unit == lt.Units.WATT


@pytest.mark.base
def test_get_default_template_component_custom_building() -> None:
    """Passing a component_id with a building only changes that; all other fields keep defaults."""
    config = example_template.ComponentNameConfig.get_default_template_component(
        component_id=ComponentID(name="ComponentName default", building="MyHouse")
    )
    assert config.component_id.building == "MyHouse"
    assert config.component_id.name == "ComponentName default"
    assert config.loadtype == lt.LoadTypes.ELECTRICITY
    assert config.unit == lt.Units.WATT


@pytest.mark.base
def test_get_default_template_component_empty_building() -> None:
    """An empty building is passed through without validation."""
    config = example_template.ComponentNameConfig.get_default_template_component(
        component_id=ComponentID(name="ComponentName default", building="")
    )
    assert config.component_id.building == ""
    assert config.component_id.name == "ComponentName default"
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
