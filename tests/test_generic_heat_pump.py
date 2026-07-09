"""Test for generic heat pump."""
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import generic_heat_pump
from hisim.simulationparameters import SimulationParameters


@pytest.mark.base
def test_generic_heat_pump():
    """Test GenericHeatPump and GenericHeatPumpController with fake temperature inputs.

    Verifies that:
    - The controller sends a heating signal when mean house temperature (10°C) is below
      the heating setpoint (18°C).
    - The heat pump delivers the expected thermal power (7420 W) corresponding to
      the configured model (Viessmann Vitocal 300-A).
    """

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )
    # Heat Pump
    manufacturer = "Viessmann Werke GmbH & Co KG"
    heat_pump_name = "Vitocal 300-A AWO-AC 301.B07"
    minimum_idle_time = 30
    minimum_operation_time = 15
    heat_pump_power = 7420.0

    # Heat Pump Controller
    temperature_air_heating_in_celsius = 18.0
    temperature_air_cooling_in_celsius = 28.0
    offset = 1
    hp_mode = 1

    number_of_outputs = 8
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # ===================================================================================================================
    # Set Heat Pump
    my_heat_pump = generic_heat_pump.GenericHeatPump(
        config=generic_heat_pump.GenericHeatPumpConfig(
            building_name="BUI1",
            manufacturer=manufacturer,
            name="GenericHeatPump",
            heat_pump_name=heat_pump_name,
            min_operation_time=minimum_idle_time,
            min_idle_time=minimum_operation_time,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Set Heat Pump Controller
    my_heat_pump_controller = generic_heat_pump.GenericHeatPumpController(
        config=generic_heat_pump.GenericHeatPumpControllerConfig(
            building_name="BUI1",
            name="GenericHeatPumpController",
            temperature_air_heating_in_celsius=temperature_air_heating_in_celsius,
            temperature_air_cooling_in_celsius=temperature_air_cooling_in_celsius,
            offset=offset,
            mode=hp_mode,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    t_air_outdoor_output = cp.ComponentOutput(
        "FakeTemperatureOutside",
        "TemperatureAir",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.WATT,
    )

    t_m_output = cp.ComponentOutput(
        "FakeHouse", "TemperatureMean", lt.LoadTypes.TEMPERATURE, lt.Units.WATT
    )

    my_heat_pump_controller.temperature_mean_channel.source_output = t_m_output
    my_heat_pump.temperature_outside_channel.source_output = t_air_outdoor_output
    my_heat_pump.state_channel.source_output = my_heat_pump_controller.state_channel

    # Link inputs and outputs
    t_m_output.global_index = 0
    stsv.values[0] = 10

    my_heat_pump_controller.state_channel.global_index = 1

    my_heat_pump.thermal_power_delivered_channel.global_index = 2
    my_heat_pump.heating_channel.global_index = 3
    my_heat_pump.cooling_channel.global_index = 4
    my_heat_pump.electricity_output_channel.global_index = 5
    my_heat_pump.number_of_cycles_channel.global_index = 6
    t_air_outdoor_output.global_index = 7
    timestep = 60
    # Simulate
    my_heat_pump_controller.i_restore_state()
    my_heat_pump_controller.i_simulate(timestep, stsv, False)

    my_heat_pump.i_restore_state()
    my_heat_pump.i_simulate(timestep, stsv, False)

    # Check if there is a signal to heat up the house
    assert 1 == stsv.values[1]
    # Check if the delivered heat is indeed that corresponded to the heat pump model
    assert heat_pump_power == stsv.values[2]


@pytest.mark.base
def test_set_time_correction_raises_runtime_error_when_called_twice() -> None:
    """Calling set_time_correction twice with a non-unit factor raises RuntimeError.

    The conversion guard rejects double-application of a time-correction factor
    because the power attributes would be scaled twice. A RuntimeError (not a
    bare Exception) signals the runtime-state violation.
    """
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    my_heat_pump = generic_heat_pump.GenericHeatPump(
        config=generic_heat_pump.GenericHeatPumpConfig(
            building_name="BUI1",
            manufacturer="Viessmann Werke GmbH & Co KG",
            name="GenericHeatPump",
            heat_pump_name="Vitocal 300-A AWO-AC 301.B07",
            min_operation_time=30,
            min_idle_time=15,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # First application with a non-unit factor converts and marks the pump.
    my_heat_pump.set_time_correction(2.0)
    assert my_heat_pump.has_been_converted is True

    # Second application must raise RuntimeError (the reachable guard from #966).
    with pytest.raises(RuntimeError, match="already been converted"):
        my_heat_pump.set_time_correction(2.0)
