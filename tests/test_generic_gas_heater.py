"""Test for generic gas heater module."""
# clean
import pytest
from hisim import component as cp

# import components as cps
# import components
from hisim.components import generic_boiler
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from tests import functions_for_testing as fft


@pytest.mark.base
def test_gas_heater():
    """Test for the gas heater."""

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )
    # GasHeater
    temperature_delta_in_celsius = 10
    maximal_power_in_watt = 12_000
    # ===================================================================================================================
    # Set Gas Heater
    my_gas_heater_config = generic_boiler.GenericBoilerConfig.get_default_condensing_gas_boiler_config()
    my_gas_heater_config.temperature_delta_in_celsius = temperature_delta_in_celsius
    my_gas_heater_config.maximal_power_in_watt = maximal_power_in_watt

    my_gas_heater = generic_boiler.GenericBoiler(
        config=my_gas_heater_config, my_simulation_parameters=my_simulation_parameters
    )

    # Set Fake Outputs for Gas Heater
    control_signal_channel = cp.ComponentOutput(
        "FakeControlSignal", "ControlSignal", lt.LoadTypes.ANY, lt.Units.PERCENT
    )
    operating_mode_channel = cp.ComponentOutput(
        "FakeOperatingMode", "OperatingMode", lt.LoadTypes.ANY, lt.Units.ANY
    )
    temperature_delta_channel = cp.ComponentOutput(
        "FakeTemperatureDelta", "TemperatureDelta", lt.LoadTypes.TEMPERATURE, lt.Units.ANY
    )

    mass_flow_input_temperature_channel = cp.ComponentOutput(
        "FakeMassflowInputTemperature",
        "MassflowInputTemperature",
        lt.LoadTypes.WATER,
        lt.Units.CELSIUS,
    )

    number_of_outputs = fft.get_number_of_outputs(
        [control_signal_channel, operating_mode_channel, temperature_delta_channel, mass_flow_input_temperature_channel, my_gas_heater]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Link inputs and outputs
    my_gas_heater.control_signal_channel.source_output = control_signal_channel
    my_gas_heater.operating_mode_channel.source_output = operating_mode_channel
    my_gas_heater.temperature_delta_channel.source_output = temperature_delta_channel
    my_gas_heater.water_input_temperature_sh_channel.source_output = (
        mass_flow_input_temperature_channel
    )

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [control_signal_channel, operating_mode_channel, temperature_delta_channel, mass_flow_input_temperature_channel, my_gas_heater]
    )
    stsv.values[control_signal_channel.global_index] = 1
    stsv.values[operating_mode_channel.global_index] = 1
    stsv.values[temperature_delta_channel.global_index] = temperature_delta_in_celsius
    stsv.values[mass_flow_input_temperature_channel.global_index] = 30

    timestep = 30

    # Simulate
    my_gas_heater.i_simulate(timestep, stsv, False)
    log.information(str(stsv.values))

    # Mass-Flow out of Gas-Heater to heat up Storages or House
    assert (
        stsv.values[my_gas_heater.water_output_mass_flow_sh_channel.global_index]
        == 0.2583732057416268
    )
    # Temperature of Water out of GasHeater
    assert (
        stsv.values[my_gas_heater.water_output_temperature_sh_channel.global_index]
        == temperature_delta_in_celsius
        + stsv.values[mass_flow_input_temperature_channel.global_index]
    )
    # Real Power of GasHeater
    assert (
        stsv.values[my_gas_heater.thermal_output_power_sh_channel.global_index] == 10_800
    )
