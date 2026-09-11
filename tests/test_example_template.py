"""Test for the Example Template."""

# clean
from pathlib import Path

import pytest
from hisim import component as cp
from hisim.components import example_component, example_template
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator
from hisim import loadtypes as lt
from hisim import log
from hisim.config import AUTO, ComponentID, ConfigSizingError, SizableFieldKind, SizingContext, describe_config
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
    assert (
        my_example_template_config.rated_power_in_watt
        == example_template.SPECIFIC_RATED_POWER_IN_WATT_PER_M2 * fft.DEFAULT_CONDITIONED_FLOOR_AREA_IN_M2
    )
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

    This is also the one place where the law's *rendered* text is pinned: every other
    assertion about a description reads its structure, but the template is the file whose
    whole point is what ``describe`` prints, so the formatter is worth one exact string.
    """
    description = describe_config(example_template.ComponentNameConfig)
    assert [field.name for field in description.sizable_fields] == ["rated_power_in_watt"]
    rated_power = description.sizable_fields[0]
    assert rated_power.law == "2.0 * Size.CONDITIONED_FLOOR_AREA_IN_M2"
    assert rated_power.facts_read == (("conditioned_floor_area_in_m2", "ONE"),)
    assert rated_power.kind is SizableFieldKind.LAW
    assert rated_power.fields_read == ()
    assert rated_power.note is not None
    assert str(example_template.SPECIFIC_RATED_POWER_IN_WATT_PER_M2) in rated_power.note
    assert [field.name for field in description.fields if field.sizable] == ["rated_power_in_watt"]
    # The template contributes no fact of its own; see the note in the module about why.
    assert not description.facts_provided


def _stateless_output_of_one_step(conditioned_floor_area_in_m2: float, input_in_w: float) -> float:
    """Runs one timestep of the template component sized for one building, and returns its output.

    The stateless channel is the one the rated power caps, so this helper exists to let a test
    choose the building -- and with it the rated power -- and read back what the cap did.

    Args:
        conditioned_floor_area_in_m2: the one fact the component's law reads.
        input_in_w: the power offered at the component's input.

    Returns:
        float: the value written to ``OutputWithoutState``, in watts.
    """
    mysim = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = example_template.ComponentNameConfig.get_default_template_component().resolve(
        SizingContext(conditioned_floor_area_in_m2=conditioned_floor_area_in_m2)
    )
    component = example_template.ComponentName(config=config, my_simulation_parameters=mysim)
    source = cp.ComponentOutput(
        object_name="source",
        field_name="input_from_another_component",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        component_id=ComponentID("source"),
    )
    component.input_from_other_component.source_output = source
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([component, source]))
    fft.add_global_index_of_components([component, source])
    stsv.values[source.global_index] = input_in_w
    component.i_simulate(0, stsv, False)
    return stsv.values[component.output_without_state.global_index]


@pytest.mark.base
def test_the_stateless_output_is_capped_at_the_sized_rated_power() -> None:
    """A building small enough to size the device below its input gets the rated power out.

    This is what makes ``rated_power_in_watt`` more than decoration in the template: 20 m2 of
    floor area size the device at 40 W, the input offers 50 W, and the output is the 40 W the
    device was sized for -- both sides of the ``min`` being watts, which is why
    ``OutputWithoutState`` is declared in WATT.
    """
    rated_power_in_watt = example_template.SPECIFIC_RATED_POWER_IN_WATT_PER_M2 * 20.0
    assert rated_power_in_watt == 40.0
    assert _stateless_output_of_one_step(conditioned_floor_area_in_m2=20.0, input_in_w=50.0) == rated_power_in_watt


@pytest.mark.base
def test_the_stateless_output_passes_the_input_through_when_the_cap_does_not_bind() -> None:
    """The default building sizes the device at 242.4 W, well above the 51 W it is offered."""
    rated_power_in_watt = (
        example_template.SPECIFIC_RATED_POWER_IN_WATT_PER_M2 * fft.DEFAULT_CONDITIONED_FLOOR_AREA_IN_M2
    )
    output_in_w = _stateless_output_of_one_step(
        conditioned_floor_area_in_m2=fft.DEFAULT_CONDITIONED_FLOOR_AREA_IN_M2, input_in_w=50.0
    )
    assert output_in_w == 51.0 < rated_power_in_watt


@pytest.mark.base
def test_a_rated_power_written_as_a_string_is_coerced_to_a_float() -> None:
    """``value_type=float`` types the wire value: a file may write ``"242.4"`` and get a float."""
    written = {
        "component_id": {"name": "FromAFile"},
        "loadtype": "Electricity",
        "unit": "W",
        "rated_power_in_watt": "242.4",
    }
    assert example_template.ComponentNameConfig.from_dict(written).rated_power_in_watt == 242.4


@pytest.mark.base
def test_an_unresolved_template_config_is_refused_by_the_component() -> None:
    """A config that still says AUTO never reaches the component, and the error names the law."""
    mysim = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = example_template.ComponentNameConfig.get_default_template_component()
    with pytest.raises(ConfigSizingError) as refusal:
        example_template.ComponentName(config=config, my_simulation_parameters=mysim)
    assert "rated_power_in_watt" in str(refusal.value)
    assert "Size.CONDITIONED_FLOOR_AREA_IN_M2" in str(refusal.value)


@pytest.mark.base
def test_a_component_built_from_the_template_runs_inside_a_simulator(tmp_path: Path) -> None:
    """The template's whole job: copy it, put it in a Simulator, and it runs.

    Every other test in this file drives ``i_simulate`` directly, which skips the first
    thing a run does -- the ``Simulator`` calls ``i_prepare_simulation`` on every component
    before the first timestep, and ``Component`` raises ``NotImplementedError`` there rather
    than doing nothing. While the template omitted the hook, a component copied from it died
    on the first line of its first run and no test said so.

    The ``ExampleComponent`` next door is the source here only because the template declares
    its input mandatory and the Simulator refuses to run with an unconnected mandatory input;
    its ``ElectricityOutput`` is the one port in the two example modules with a matching load
    type and unit. It delivers 0 W over a day at hourly resolution, so the template's
    stateless output is its 1 W offset, far below the power the default building sizes it for.
    """
    my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    my_simulation_parameters.result_directory = str(tmp_path / "results")
    assert not my_simulation_parameters.post_processing_options

    my_sim: Simulator = Simulator(
        module_directory=str(tmp_path),
        module_filename="template_component_in_a_simulator",
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    my_source = example_component.ExampleComponent(
        config=fft.sized_example_component_config(), my_simulation_parameters=my_simulation_parameters
    )
    my_template_component = example_template.ComponentName(
        config=example_template.ComponentNameConfig.get_default_template_component().resolve(
            fft.default_building_sizing_context()
        ),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_template_component.connect_input(
        my_template_component.InputFromOtherComponent,
        my_source.component_name,
        my_source.ElectricityOutput,
    )

    my_sim.add_component(my_source)
    my_sim.add_component(my_template_component)

    my_sim.run_all_timesteps()

    results = my_sim.results_data_frame
    assert len(results) == my_simulation_parameters.timesteps == 24
    stateless_output = next(
        column for column in results.columns if example_template.ComponentName.OutputWithoutState in column
    )
    assert (results[stateless_output] == 1.0).all()
