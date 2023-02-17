"""Test for generic gas heater module."""
# clean
from hisim import component as cp
# import components as cps
# import components
from hisim.components import generic_gas_heater
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from tests import functions_for_testing as fft


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
    my_gas_heater_config = generic_gas_heater.GenericGasHeaterConfig.get_default_gasheater_config()
    my_gas_heater_config.temperature_delta_in_celsius = temperature_delta_in_celsius
    my_gas_heater_config.maximal_power_in_watt = maximal_power_in_watt

    my_gas_heater = generic_gas_heater.GasHeater(
        config=my_gas_heater_config, my_simulation_parameters=my_simulation_parameters
    )

    # Set Fake Outputs for Gas Heater
    control_signal_channel = cp.ComponentOutput(
        "FakeControlSignal", "ControlSignal", lt.LoadTypes.ANY, lt.Units.PERCENT
    )

    mass_flow_input_temperature_channel = cp.ComponentOutput(
        "FakeMassflowInputTemperature",
        "MassflowInputTemperature",
        lt.LoadTypes.WATER,
        lt.Units.CELSIUS,
    )

    number_of_outputs = fft.get_number_of_outputs(
        [control_signal_channel, mass_flow_input_temperature_channel, my_gas_heater]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Link inputs and outputs
    my_gas_heater.control_signal_channel.source_output = control_signal_channel
    my_gas_heater.mass_flow_input_tempertaure_channel.source_output = (
        mass_flow_input_temperature_channel
    )

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [control_signal_channel, mass_flow_input_temperature_channel, my_gas_heater]
    )
    stsv.values[control_signal_channel.global_index] = 1
    stsv.values[mass_flow_input_temperature_channel.global_index] = 30

    timestep = 30

    # Simulate
    my_gas_heater.i_simulate(timestep, stsv, False)
    log.information(str(stsv.values))

    # Mass-Flow out of Gas-Heater to heat up Storages or House
    assert (
        stsv.values[my_gas_heater.mass_flow_output_channel.global_index]
        == 0.2582496413199426
    )
    # Temperature of Water out of GasHeater
    assert (
        stsv.values[my_gas_heater.mass_flow_output_temperature_channel.global_index]
        == temperature_delta_in_celsius
        + stsv.values[mass_flow_input_temperature_channel.global_index]
    )
    # Real Power of GasHeater
    assert (
        stsv.values[my_gas_heater.thermal_output_power_channel.global_index] == 10_800
    )
