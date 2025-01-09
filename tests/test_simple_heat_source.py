"""Test for generic heat source."""
import pytest
from hisim import component as cp
from hisim.components import simple_heat_source
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from tests import functions_for_testing as fft

@pytest.mark.base
def test_heat_source():
    """Test heat source."""

    # simulation parameters
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)

    # default config
    my_heat_source_config = simple_heat_source.SimpleHeatSourceConfig.get_default_config_const_power()

    my_heat_source = simple_heat_source.SimpleHeatSource(
        config=my_heat_source_config, my_simulation_parameters=my_simulation_parameters
    )


    massflow = cp.ComponentOutput("Fake_massflow", "Fake_massflow", lt.LoadTypes.ANY, lt.Units.ANY)
    temperature_input = cp.ComponentOutput("Fake_t_in", "Fake_t_in", lt.LoadTypes.ANY, lt.Units.ANY)

    number_of_outputs = fft.get_number_of_outputs(
        [
            my_heat_source,
            massflow,
            temperature_input,
        ]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_heat_source.massflow_input_channel.source_output = massflow
    my_heat_source.temperature_input_channel.source_output = temperature_input

    fft.add_global_index_of_components(
        [
            my_heat_source,
            massflow,
            temperature_input,
        ]
    )

    stsv.values[massflow.global_index] = 0.3
    stsv.values[temperature_input.global_index] = 5

    timestep = 1

    # Simulate
    my_heat_source.i_simulate(timestep, stsv, False)

    assert 5000.0 == stsv.values[my_heat_source.thermal_power_delivered_channel.global_index]
