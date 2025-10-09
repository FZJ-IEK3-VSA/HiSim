"""Test for simple hot water storage."""
# clean
import pytest
import numpy as np
from hisim import component as cp
from hisim.components import simple_water_storage
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@pytest.mark.base
def test_simple_storage():
    """Test for simple hot water storage."""

    # calculate mixing factors and run simulation for different seconds per timestep
    seconds_per_timesteps_to_test = [60, 60 * 15, 60 * 30, 60 * 60, 60 * 120]
    factor_for_water_storage_portion = 0.0
    for sec_per_timestep in seconds_per_timesteps_to_test:
        if sec_per_timestep <= 3600:
            factor_for_water_storage_portion = sec_per_timestep / 3600
        elif sec_per_timestep > 3600:
            factor_for_water_storage_portion = 1.0

        simulate_simple_water_storage(
            sec_per_timestep,
            factor_for_water_storage_portion=factor_for_water_storage_portion,
        )


def simulate_simple_water_storage(
    sec_per_timesteps: int, factor_for_water_storage_portion: float
) -> None:
    """Simulate and test simple hot water storage."""

    seconds_per_timestep = sec_per_timesteps
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    # Set Simple Heat Water Storage
    hws_name = "SimpleHeatWaterStorage"
    volume_heating_water_storage_in_liter = 100

    # ===================================================================================================================
    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig(
        building_name="BUI1",
        name=hws_name,
        volume_heating_water_storage_in_liter=volume_heating_water_storage_in_liter,
        heat_transfer_coefficient_in_watt_per_m2_per_kelvin=2.0,
        heat_exchanger_is_present=False,
        position_hot_water_storage_in_system=simple_water_storage.PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_SOURCE,
        device_co2_footprint_in_kg=100,
        investment_costs_in_euro=volume_heating_water_storage_in_liter * 14.51,
        lifetime_in_years=100,
        maintenance_costs_in_euro_per_year=0.0,
        subsidy_as_percentage_of_investment_costs=0.0,
    )
    my_simple_heat_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    water_mass_flow_rate_hds = cp.ComponentOutput(
        "FakeWaterInputTemperatureFromHds",
        "WaterMassFlowRateFromHeatDistributionSystem",
        lt.LoadTypes.WARM_WATER,
        lt.Units.KG_PER_SEC,
    )

    water_temperature_input_from_heat_distribution_system = cp.ComponentOutput(
        "FakeWaterInputTemperatureFromHds",
        "WaterTemperatureInputFromHeatDistributionSystem",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
    )

    water_temperature_input_from_heat_generator = cp.ComponentOutput(
        "FakeWaterInputTemperatureFromHeatGenerator",
        "WaterTemperatureInputFromHeatGenerator",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
    )

    water_mass_flow_rate_from_heat_generator = cp.ComponentOutput(
        "FakeWaterMassFlowRateFromHeatGenerator",
        "WaterMassFlowRateFromHeatGenerator",
        lt.LoadTypes.WARM_WATER,
        lt.Units.KG_PER_SEC,
    )

    state_controller = cp.ComponentOutput(
        "FakeState", "State", lt.LoadTypes.ANY, lt.Units.ANY
    )

    # connect fake inputs to simple hot water storage
    my_simple_heat_water_storage.water_mass_flow_rate_heat_distribution_system_input_channel.source_output = (
        water_mass_flow_rate_hds
    )
    my_simple_heat_water_storage.water_temperature_heat_distribution_system_input_channel.source_output = (
        water_temperature_input_from_heat_distribution_system
    )
    my_simple_heat_water_storage.water_temperature_heat_generator_input_channel.source_output = (
        water_temperature_input_from_heat_generator
    )

    my_simple_heat_water_storage.water_mass_flow_rate_heat_generator_input_channel.source_output = (
        water_mass_flow_rate_from_heat_generator
    )

    my_simple_heat_water_storage.state_channel.source_output = state_controller

    number_of_outputs = fft.get_number_of_outputs(
        [
            water_mass_flow_rate_hds,
            water_temperature_input_from_heat_distribution_system,
            water_temperature_input_from_heat_generator,
            water_mass_flow_rate_from_heat_generator,
            state_controller,
            my_simple_heat_water_storage,
        ]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [
            water_mass_flow_rate_hds,
            water_temperature_input_from_heat_distribution_system,
            water_temperature_input_from_heat_generator,
            water_mass_flow_rate_from_heat_generator,
            state_controller,
            my_simple_heat_water_storage,
        ]
    )
    stsv.values[water_mass_flow_rate_hds.global_index] = 0.787
    stsv.values[water_temperature_input_from_heat_distribution_system.global_index] = 48
    stsv.values[water_temperature_input_from_heat_generator.global_index] = 52
    stsv.values[water_mass_flow_rate_from_heat_generator.global_index] = 0.59
    stsv.values[state_controller.global_index] = 1

    # Simulate for timestep 300
    timestep = 300
    my_simple_heat_water_storage.mean_water_temperature_in_water_storage_in_celsius = 50
    my_simple_heat_water_storage.state.mean_water_temperature_in_celsius = 50
    previous_mean_temperature_in_celsius = 50.0
    my_simple_heat_water_storage.i_simulate(timestep, stsv, False)

    water_temperature_output_in_celsius_to_heat_distribution_system = stsv.values[5]
    water_temperature_output_in_celsius_to_heat_generator = stsv.values[6]

    # test mean water temperature calculation in storage
    mass_water_hds_in_kg = (
        stsv.values[water_mass_flow_rate_hds.global_index]
        * seconds_per_timestep
    )
    mass_water_hp_in_kg = (
        stsv.values[water_mass_flow_rate_from_heat_generator.global_index]
        * seconds_per_timestep
    )

    calculated_mean_water_temperature_in_celsius = (
        my_simple_heat_water_storage.water_mass_in_storage_in_kg
        * previous_mean_temperature_in_celsius
        + mass_water_hp_in_kg
        * stsv.values[water_temperature_input_from_heat_generator.global_index]
        + mass_water_hds_in_kg
        * stsv.values[
            water_temperature_input_from_heat_distribution_system.global_index
        ]
    ) / (
        my_simple_heat_water_storage.water_mass_in_storage_in_kg
        + mass_water_hp_in_kg
        + mass_water_hds_in_kg
    )

    # test if calculated mean water temperature is equal to simulated water temperature
    assert (
        calculated_mean_water_temperature_in_celsius
        == my_simple_heat_water_storage.mean_water_temperature_in_water_storage_in_celsius
    )

    # test water output temperature for hp

    calculated_output_to_heat_generator_in_celsius = (
        factor_for_water_storage_portion
        * my_simple_heat_water_storage.mean_water_temperature_in_water_storage_in_celsius
        + (1 - factor_for_water_storage_portion)
        * stsv.values[
            water_temperature_input_from_heat_distribution_system.global_index
        ]
    )

    np.testing.assert_allclose(
        calculated_output_to_heat_generator_in_celsius,
        water_temperature_output_in_celsius_to_heat_generator,
        rtol=0.01,
    )

    # test water output temperature for hds
    calculated_output_to_heat_distribution_system_in_celsius = (
        factor_for_water_storage_portion
        * my_simple_heat_water_storage.mean_water_temperature_in_water_storage_in_celsius
        + (1 - factor_for_water_storage_portion)
        * stsv.values[water_temperature_input_from_heat_generator.global_index]
    )
    np.testing.assert_allclose(
        calculated_output_to_heat_distribution_system_in_celsius,
        water_temperature_output_in_celsius_to_heat_distribution_system,
        rtol=0.01,
    )
