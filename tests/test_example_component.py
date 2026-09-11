"""Test for the Example Component."""

# clean
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import example_component
from hisim.simulationparameters import SimulationParameters
from hisim.config import AUTO, ComponentID, describe_config
from tests import functions_for_testing as fft


@pytest.mark.system_setups
def test_example_component() -> None:
    """Verify ExampleComponent build() and i_simulate() produce expected outputs.

    Constructs an ExampleComponent from the default config, wires a fake
    thermal-energy-delivered input (50 W), calls build() with the default
    electricity/capacity/initial-temperature values, then runs one
    simulation step at timestep 10 min and asserts the resulting
    SingleTimeStepValues match the expected t_m_c, electricity_output, and
    stored_energy outputs.
    """

    mysim: SimulationParameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)

    # ``capacity`` is a sizable field, so the factory hands back AUTO and the config has to be
    # resolved against the facts of the surrounding system before a component may be built from
    # it. The helper carries the one fact this law reads: the building's conditioned floor area.
    assert example_component.ExampleComponentConfig.get_default_example_component().capacity is AUTO
    my_example_component_config = fft.sized_example_component_config()
    # The law reproduces the literal the module used to carry, exactly: 45 J/K/m2 x 121.2 m2.
    assert my_example_component_config.capacity == 45 * 121.2 == 5454.0
    log.information(f"default example component config {my_example_component_config}\n")
    my_example_component = example_component.ExampleComponent(
        config=my_example_component_config, my_simulation_parameters=mysim
    )

    # Define outputs
    thermal_energy_delivered_output = cp.ComponentOutput(
        object_name="source",
        field_name="ThermalEnergyDelivered",
        load_type=lt.LoadTypes.HEATING,
        unit=lt.Units.WATT,
        component_id=ComponentID("source"),
    )
    my_example_component.thermal_energy_delivered_c.source_output = thermal_energy_delivered_output

    number_of_outputs = fft.get_number_of_outputs([my_example_component, thermal_energy_delivered_output])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_example_component, thermal_energy_delivered_output])
    stsv.values[thermal_energy_delivered_output.global_index] = 50  # fake thermal energy delivered input

    # Test build function with default values
    my_example_component.build(
        my_example_component_config.electricity,
        my_example_component_config.capacity,
        my_example_component_config.initial_temperature,
    )
    log.information("Build variables with non_default values: ")
    log.information(f"electricity output = {my_example_component.electricity_output}")
    log.information(f"storage capacity = {my_example_component.capacity}")
    log.information(f"initial temperature = {my_example_component.initial_temperature}\n")
    assert my_example_component_config.capacity == my_example_component.capacity
    assert my_example_component_config.initial_temperature == my_example_component.initial_temperature

    # Test Simulation
    timestep = 10 * 60
    log.information(f"timestep = {timestep}")
    log.information(
        f"thermal energy delivered output [W]= {stsv.values[thermal_energy_delivered_output.global_index]}\n"
    )

    my_example_component.i_simulate(timestep, stsv, False)
    log.information("Output values after simulation: ")
    log.information(f"t_mC = {stsv.values[my_example_component.t_m_c.global_index]}")
    log.information(f"electricity outputC = {stsv.values[my_example_component.electricity_output_c.global_index]}")
    log.information(f"stored energyC = {stsv.values[my_example_component.stored_energy_c.global_index]}")
    log.information(f"output values = {stsv.values}")

    assert 50 == stsv.values[thermal_energy_delivered_output.global_index]
    assert 25 == stsv.values[my_example_component.t_m_c.global_index]
    assert 0 == stsv.values[my_example_component.electricity_output_c.global_index]
    assert 1626110.0999999999 == stsv.values[my_example_component.stored_energy_c.global_index]


@pytest.mark.base
def test_display_config_isolation() -> None:
    """Verify that omitting my_display_config creates independent instances.

    Regression test for mutable default argument: each call to __init__
    without an explicit my_display_config must receive its own
    DisplayConfig instance, not a shared one.
    """
    mysim: SimulationParameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    config = fft.sized_example_component_config()

    comp_a = example_component.ExampleComponent(my_simulation_parameters=mysim, config=config)
    comp_b = example_component.ExampleComponent(my_simulation_parameters=mysim, config=config)

    # Verify that my_display_config instances are distinct
    assert (
        comp_a.my_display_config is not comp_b.my_display_config
    ), "my_display_config must not be shared across instances"


@pytest.mark.base
def test_capacity_is_described_as_a_sized_field() -> None:
    """The capacity field describes itself as derived, with its law, its fact and its note.

    This is what a reader of ``hisim energy-system describe`` sees, and what the template
    claims a component's sizing looks like: the value is not a literal in the module but a
    law naming the fact it reads.
    """
    description = describe_config(example_component.ExampleComponentConfig)
    capacity = next(field for field in description.sizable_fields if field.name == "capacity")
    assert capacity.law == "45.0 * Size.CONDITIONED_FLOOR_AREA_IN_M2"
    assert capacity.facts_read == (("conditioned_floor_area_in_m2", "ONE"),)
    assert capacity.note is not None and "45 J/K" in capacity.note
