"""Tests for household_coal_heating_building_sizer system setup and coal boiler component."""

# clean
import os
import pytest

from hisim import component as cp
from hisim import hisim_main
from hisim import loadtypes as lt
from hisim import log
from hisim import utils
from hisim.components import generic_boiler
from hisim.components.dual_circuit_system import HeatingMode
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessingoptions import PostProcessingOptions
from tests import functions_for_testing as fft


# =============================================================================
# Component-level tests
# =============================================================================


@pytest.mark.base
def test_coal_boiler_config_defaults():
    """Verify that the default coal boiler config has the expected fuel type and boiler type."""
    cfg = generic_boiler.GenericBoilerConfig.get_scaled_conventional_coal_boiler_config(
        heating_load_of_building_in_watt=12_000
    )
    assert cfg.energy_carrier == lt.LoadTypes.COAL
    assert cfg.boiler_type == generic_boiler.BoilerType.CONVENTIONAL
    assert cfg.name == "ConventionalCoalBoiler"
    # Efficiency bounds for coal
    assert cfg.eff_th_min == pytest.approx(0.55)
    assert cfg.eff_th_max == pytest.approx(0.80)
    # Minimum power is 1/12 of maximum
    assert cfg.minimal_thermal_power_in_watt == pytest.approx(12_000 / 12)


@pytest.mark.base
def test_coal_boiler_controller_config_defaults():
    """Verify that the coal boiler controller is non-modulating with correct run/resting times."""
    ctrl_cfg = generic_boiler.GenericBoilerControllerConfig.get_default_coal_controller_config(
        minimal_thermal_power_in_watt=1_000,
        maximal_thermal_power_in_watt=12_000,
    )
    assert ctrl_cfg.is_modulating is False
    assert ctrl_cfg.name == "CoalBoilerController"
    # 45 min minimum runtime
    assert ctrl_cfg.minimum_runtime_in_seconds == pytest.approx(45 * 60)
    # 20 min minimum resting time
    assert ctrl_cfg.minimum_resting_time_in_seconds == pytest.approx(20 * 60)


@pytest.mark.base
def test_coal_boiler_space_heating_output():
    """Test coal boiler thermal output in space-heating mode at full power.

    Configuration: 12 kW max, on-off (control_signal=1), temperature_delta=10 K,
    water_input_temperature=30 °C.

    Expected at full power (eff=0.80):
      - thermal_power_sh  = 12000 * 0.80 = 9600 W
      - mass_flow_sh      = 9600 / (4180 * 10) ≈ 0.22967 kg/s
      - water_output_temp = 30 + 10 = 40 °C
      - energy_demand_sh  = 12000 * 60 / 3600 = 200 Wh
    """
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2021, seconds_per_timestep)

    temperature_delta_in_celsius = 10
    maximal_power_in_watt = 12_000
    water_input_temp = 30.0

    my_coal_heater_config = generic_boiler.GenericBoilerConfig.get_scaled_conventional_coal_boiler_config(
        heating_load_of_building_in_watt=maximal_power_in_watt
    )
    my_coal_heater = generic_boiler.GenericBoiler(
        config=my_coal_heater_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Fake inputs
    control_signal_channel = cp.ComponentOutput(
        "FakeControlSignal", "ControlSignal", lt.LoadTypes.ANY, lt.Units.PERCENT
    )
    operating_mode_channel = cp.ComponentOutput(
        "FakeOperatingMode", "OperatingMode", lt.LoadTypes.ANY, lt.Units.ANY
    )
    temperature_delta_channel = cp.ComponentOutput(
        "FakeTemperatureDelta", "TemperatureDelta", lt.LoadTypes.TEMPERATURE, lt.Units.ANY
    )
    water_input_temperature_channel = cp.ComponentOutput(
        "FakeWaterInputTemp", "WaterInputTemperature", lt.LoadTypes.WATER, lt.Units.CELSIUS
    )

    number_of_outputs = fft.get_number_of_outputs(
        [
            control_signal_channel,
            operating_mode_channel,
            temperature_delta_channel,
            water_input_temperature_channel,
            my_coal_heater,
        ]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Wire inputs
    my_coal_heater.control_signal_channel.source_output = control_signal_channel
    my_coal_heater.operating_mode_channel.source_output = operating_mode_channel
    my_coal_heater.temperature_delta_channel.source_output = temperature_delta_channel
    my_coal_heater.water_input_temperature_sh_channel.source_output = water_input_temperature_channel

    fft.add_global_index_of_components(
        [
            control_signal_channel,
            operating_mode_channel,
            temperature_delta_channel,
            water_input_temperature_channel,
            my_coal_heater,
        ]
    )

    # Full power, space heating mode
    stsv.values[control_signal_channel.global_index] = 1.0
    stsv.values[operating_mode_channel.global_index] = HeatingMode.SPACE_HEATING.value
    stsv.values[temperature_delta_channel.global_index] = temperature_delta_in_celsius
    stsv.values[water_input_temperature_channel.global_index] = water_input_temp

    my_coal_heater.i_simulate(timestep=30, stsv=stsv, force_convergence=False)
    log.information(str(stsv.values))

    assert stsv.values[my_coal_heater.thermal_output_power_sh_channel.global_index] == pytest.approx(9_600.0)
    assert stsv.values[my_coal_heater.water_output_mass_flow_sh_channel.global_index] == pytest.approx(
        9_600.0 / (4180 * temperature_delta_in_celsius)
    )
    assert stsv.values[my_coal_heater.water_output_temperature_sh_channel.global_index] == pytest.approx(
        water_input_temp + temperature_delta_in_celsius
    )
    assert stsv.values[my_coal_heater.energy_demand_sh_channel.global_index] == pytest.approx(200.0)
    # DHW outputs must be zero in SH mode
    assert stsv.values[my_coal_heater.thermal_output_power_dhw_channel.global_index] == pytest.approx(0.0)
    assert stsv.values[my_coal_heater.water_output_mass_flow_dhw_channel.global_index] == pytest.approx(0.0)


@pytest.mark.base
def test_coal_boiler_off_mode():
    """Test that the coal boiler produces no heat when the control signal is 0."""
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2021, seconds_per_timestep)

    my_coal_heater_config = generic_boiler.GenericBoilerConfig.get_scaled_conventional_coal_boiler_config(
        heating_load_of_building_in_watt=12_000
    )
    my_coal_heater = generic_boiler.GenericBoiler(
        config=my_coal_heater_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    control_signal_channel = cp.ComponentOutput(
        "FakeControlSignal", "ControlSignal", lt.LoadTypes.ANY, lt.Units.PERCENT
    )
    operating_mode_channel = cp.ComponentOutput(
        "FakeOperatingMode", "OperatingMode", lt.LoadTypes.ANY, lt.Units.ANY
    )
    temperature_delta_channel = cp.ComponentOutput(
        "FakeTemperatureDelta", "TemperatureDelta", lt.LoadTypes.TEMPERATURE, lt.Units.ANY
    )
    water_input_temperature_channel = cp.ComponentOutput(
        "FakeWaterInputTemp", "WaterInputTemperature", lt.LoadTypes.WATER, lt.Units.CELSIUS
    )

    number_of_outputs = fft.get_number_of_outputs(
        [
            control_signal_channel,
            operating_mode_channel,
            temperature_delta_channel,
            water_input_temperature_channel,
            my_coal_heater,
        ]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_coal_heater.control_signal_channel.source_output = control_signal_channel
    my_coal_heater.operating_mode_channel.source_output = operating_mode_channel
    my_coal_heater.temperature_delta_channel.source_output = temperature_delta_channel
    my_coal_heater.water_input_temperature_sh_channel.source_output = water_input_temperature_channel

    fft.add_global_index_of_components(
        [
            control_signal_channel,
            operating_mode_channel,
            temperature_delta_channel,
            water_input_temperature_channel,
            my_coal_heater,
        ]
    )

    # Boiler off
    stsv.values[control_signal_channel.global_index] = 0.0
    stsv.values[operating_mode_channel.global_index] = HeatingMode.OFF.value
    stsv.values[temperature_delta_channel.global_index] = 0.0
    stsv.values[water_input_temperature_channel.global_index] = 30.0

    my_coal_heater.i_simulate(timestep=30, stsv=stsv, force_convergence=False)

    assert stsv.values[my_coal_heater.thermal_output_power_sh_channel.global_index] == pytest.approx(0.0)
    assert stsv.values[my_coal_heater.water_output_mass_flow_sh_channel.global_index] == pytest.approx(0.0)
    assert stsv.values[my_coal_heater.energy_demand_sh_channel.global_index] == pytest.approx(0.0)


@pytest.mark.base
def test_coal_boiler_scaled_power():
    """Test that get_scaled_conventional_coal_boiler_config scales max power correctly."""
    heating_load = 8_000.0
    cfg = generic_boiler.GenericBoilerConfig.get_scaled_conventional_coal_boiler_config(
        heating_load_of_building_in_watt=heating_load
    )
    assert cfg.maximal_thermal_power_in_watt == pytest.approx(heating_load)
    assert cfg.minimal_thermal_power_in_watt == pytest.approx(heating_load / 12)


# =============================================================================
# System-level test
# =============================================================================

# Shared simulation parameters for the system-setup test: one day, 15-min timestep
_my_simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15)
_my_simulation_parameters.post_processing_options.append(
    PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
)
_my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
_my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
_my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
_my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)


@pytest.mark.system_setups
@utils.measure_execution_time
def test_household_coal_heating_building_sizer():
    """Run the coal heating building sizer system setup for one day and verify it completes."""
    path = "../system_setups/household_coal_heating_building_sizer.py"

    hisim_main.main(path, _my_simulation_parameters)
    log.information(os.getcwd())
