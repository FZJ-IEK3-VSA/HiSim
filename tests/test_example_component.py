"""Test for the Example Component."""

# clean
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import example_component
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@pytest.mark.examples
def test_example_component():
    """Test for the Example Component."""

    mysim: SimulationParameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)

    my_example_component_config = example_component.ExampleComponentConfig.get_default_example_component()
    print("\n")
    log.information("default example component config " + str(my_example_component_config) + "\n")
    my_example_component = example_component.ExampleComponent(config=my_example_component_config, my_simulation_parameters=mysim)

    # Define outputs
    thermal_energy_delivered_output = cp.ComponentOutput(
        object_name="source",
        field_name="thermal energy delivered",
        load_type=lt.LoadTypes.HEATING,
        unit=lt.Units.WATT,
    )
    my_example_component.thermal_energy_delivered_c.source_output = thermal_energy_delivered_output

    number_of_outputs = fft.get_number_of_outputs([my_example_component, thermal_energy_delivered_output])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_example_component, thermal_energy_delivered_output])
    stsv.values[thermal_energy_delivered_output.global_index] = 50  # fake thermal energy delivered input

    # Test build function with default values
    my_example_component.build(my_example_component_config.electricity, my_example_component_config.capacity, my_example_component_config.initial_temperature)
    log.information("Build variables with non_default values: ")
    log.information("electricity output = " + str(my_example_component.electricity_output))
    log.information("storage capacity = " + str(my_example_component.capacity))
    log.information("initial temperature = " + str(my_example_component.initial_temperature) + "\n")
    assert my_example_component_config.capacity == my_example_component.capacity
    assert my_example_component_config.initial_temperature == my_example_component.initial_temperature

    # Test Simulation
    timestep = 10 * 60
    log.information("timestep = " + str(timestep))
    log.information("thermal energy delivered output [W]= " + str(stsv.values[thermal_energy_delivered_output.global_index]) + "\n")

    my_example_component.i_simulate(timestep, stsv, False)
    log.information("Output values after simulation: ")
    log.information("t_mC = " + str(stsv.values[my_example_component.t_m_c.global_index]))
    log.information("electricity outputC = " + str(stsv.values[my_example_component.electricity_output_c.global_index]))
    log.information("stored energyC = " + str(stsv.values[my_example_component.stored_energy_c.global_index]))
    log.information("output values = " + str(stsv.values))

    assert 50 == stsv.values[thermal_energy_delivered_output.global_index]
    assert 25 == stsv.values[my_example_component.t_m_c.global_index]
    assert 0 == stsv.values[my_example_component.electricity_output_c.global_index]
    assert 1626110.0999999999 == stsv.values[my_example_component.stored_energy_c.global_index]
