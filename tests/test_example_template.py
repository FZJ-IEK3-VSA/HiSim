"""Test for the Example Template."""

# clean
import pytest
from hisim import component as cp
from hisim.components import example_template
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import log
from hisim.config import AUTO, ComponentID, ConfigSizingError, SizableFieldKind, describe_config
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

    # ``rated_power_in_watt`` is sized, so the factory's config carries AUTO and has to be
    # resolved against the facts of the surrounding system before a component is built from it.
    my_example_template_config = example_template.ComponentNameConfig.get_default_template_component().resolve(
        fft.default_building_sizing_context()
    )
    assert my_example_template_config.rated_power_in_watt == 2.0 * 121.2
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
    assert config.component_id.name == "ComponentNameDefault"
    assert config.loadtype == lt.LoadTypes.ELECTRICITY
    assert config.unit == lt.Units.WATT
    # The sized field is not a default *value*: the factory leaves it to the law.
    assert config.rated_power_in_watt is AUTO


@pytest.mark.base
def test_get_default_template_component_custom_building() -> None:
    """Passing a component_id with a building only changes that; all other fields keep defaults."""
    config = example_template.ComponentNameConfig.get_default_template_component(
        component_id=ComponentID(name="ComponentNameDefault", building="MyHouse")
    )
    assert config.component_id.building == "MyHouse"
    assert config.component_id.name == "ComponentNameDefault"
    assert config.loadtype == lt.LoadTypes.ELECTRICITY
    assert config.unit == lt.Units.WATT
    assert config.rated_power_in_watt is AUTO


@pytest.mark.base
def test_get_default_template_component_empty_building_is_refused() -> None:
    """An empty building label is refused at the identity, naming the field.

    Passed through, it would silently join into a key with a leading underscore — a component
    nobody addressed that way — so the identity layer refuses it where it is written.
    """
    with pytest.raises(ValueError, match="building label"):
        ComponentID(name="ComponentNameDefault", building="")


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


@pytest.mark.base
def test_the_template_describes_its_sizing_mechanism() -> None:
    """``describe_config`` shows the template's sized field with its law, fact and note.

    This is what the template exists to demonstrate and what a reader gets from
    ``hisim energy-system describe hisim.components.example_template.ComponentName``: the
    field is derived from a named fact of the surrounding system, not from a literal in the
    module, and the law says so in its own words.
    """
    description = describe_config(example_template.ComponentNameConfig)
    assert [field.name for field in description.sizable_fields] == ["rated_power_in_watt"]
    rated_power = description.sizable_fields[0]
    assert rated_power.law == "2.0 * Size.CONDITIONED_FLOOR_AREA_IN_M2"
    assert rated_power.facts_read == (("conditioned_floor_area_in_m2", "ONE"),)
    assert rated_power.kind is SizableFieldKind.LAW
    assert rated_power.note is not None and "W per m" in rated_power.note
    assert [field.name for field in description.fields if field.sizable] == ["rated_power_in_watt"]
    # The template contributes no fact of its own; see the note in the module about why.
    assert not description.facts_provided


@pytest.mark.base
def test_an_unresolved_template_config_is_refused_by_the_component() -> None:
    """A config that still says AUTO never reaches the component, and the error names the law."""
    mysim = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = example_template.ComponentNameConfig.get_default_template_component()
    with pytest.raises(ConfigSizingError) as refusal:
        example_template.ComponentName(config=config, my_simulation_parameters=mysim)
    assert "rated_power_in_watt" in str(refusal.value)
    assert "Size.CONDITIONED_FLOOR_AREA_IN_M2" in str(refusal.value)
