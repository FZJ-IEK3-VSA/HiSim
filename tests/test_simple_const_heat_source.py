"""Test for generic heat source."""
import pytest
from hisim import component as cp
from hisim.components import simple_const_heat_source
from hisim.simulationparameters import SimulationParameters


@pytest.mark.base
def test_heat_source():
    """Test heat source."""

    # simulation parameters
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)

    # default config
    my_heat_source_config = simple_const_heat_source.SimpleHeatSourceConfig.get_default_config_const_power()

    # definition of outputs
    number_of_outputs = 1
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # ===================================================================================================================
    # Set Heat Pump
    my_heat_source = simple_const_heat_source.SimpleHeatSource(
        config=my_heat_source_config, my_simulation_parameters=my_simulation_parameters
    )

    timestep = 60

    # Simulate
    my_heat_source.i_restore_state()
    my_heat_source.i_simulate(timestep, stsv, False)

    assert 5000.0 == stsv.values[my_heat_source.thermal_power_delivered_channel.global_index]
