"""Test for the example storage."""

from hisim import component as cp
from hisim.components import example_component
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import log
from tests import functions_for_testing as fft


def test_example_component():
    """Test for the example component."""

    mysim:  SimulationParameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)

    my_example_component_config = example_component.DummyConfig.get_default_dummy()
    print("\n")
    log.information("default dummy config " + str(my_example_component_config) + "\n")
    my_example_component = example_component.Dummy(config=my_example_component_config, my_simulation_parameters=mysim)

    # Define outputs
    thermal_energy_delivered_output = cp.ComponentOutput(object_name="source", field_name="thermal energy delivered", load_type=lt.LoadTypes.HEATING, unit=lt.Units.WATT)
    my_example_component.thermal_energy_deliveredC.source_output = thermal_energy_delivered_output

    number_of_outputs = fft.get_number_of_outputs([my_example_component, thermal_energy_delivered_output])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_example_component, thermal_energy_delivered_output])
    stsv.values[thermal_energy_delivered_output.global_index] = 50  # fake thermal energy delivered input

    # Test build function (here with other values than config default values)
    electricity_non_default = -2e3
    capacity_non_default = 2000
    initial_temperature_non_default = 30
    my_example_component.build(electricity_non_default, capacity_non_default, initial_temperature_non_default)
    log.information("Build variables with non_default values: ")
    log.information("electricity output = " + str(my_example_component.electricity_output))  # set in build fct
    log.information("storage capacity = " + str(my_example_component.capacity))  # set in build fct
    log.information("initial temperature = " + str(my_example_component.initial_temperature) + "\n")  # set in build fct
    assert -2e3 * -1e3 == my_example_component.electricity_output
    assert 2000 == my_example_component.capacity
    assert 30 == my_example_component.initial_temperature

    # Test Simulation
    timestep = 10 * 60
    log.information("timestep = " + str(timestep))
    log.information("thermal energy delivered output [W]= " + str(stsv.values[thermal_energy_delivered_output.global_index]) + "\n")

    my_example_component.i_simulate(timestep, stsv, False)
    log.information("Output values after simulation: ")
    log.information("t_mC = " + str(stsv.values[my_example_component.t_mC.global_index]))
    log.information("electricity outputC = " + str(stsv.values[my_example_component.electricity_outputC.global_index]))
    log.information("stored energyC = " + str(stsv.values[my_example_component.stored_energyC.global_index]))
    log.information("output values = " + str(stsv.values))

    assert 50 == stsv.values[thermal_energy_delivered_output.global_index]
    assert 30 == stsv.values[my_example_component.t_mC.global_index]
    assert 0 == stsv.values[my_example_component.electricity_outputC.global_index]
    assert 606300.00 == stsv.values[my_example_component.stored_energyC.global_index]


if __name__ == "__main__":
    test_example_component()
