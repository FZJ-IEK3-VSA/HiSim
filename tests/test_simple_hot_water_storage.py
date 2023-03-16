"""Test for simple hot water storage."""
# clean
import pytest
from hisim import component as cp
from hisim.components import simple_hot_water_storage
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from tests import functions_for_testing as fft


@pytest.mark.base
def test_simple_storage():
    """Test for simple hot water storage."""

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    # Set Simple Heat Water Storage
    hws_name = "SimpleHeatWaterStorage"
    volume_heating_water_storage_in_liter = 100
    mean_water_temperature_in_storage_in_celsius = 50
    cool_water_temperature_in_storage_in_celsius = 50
    hot_water_temperature_in_storage_in_celsius = 50

    # ===================================================================================================================
    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_hot_water_storage.SimpleHotWaterStorageConfig(
        name=hws_name,
        volume_heating_water_storage_in_liter=volume_heating_water_storage_in_liter,
        mean_water_temperature_in_storage_in_celsius=mean_water_temperature_in_storage_in_celsius,
        cool_water_temperature_in_storage_in_celsius=cool_water_temperature_in_storage_in_celsius,
        hot_water_temperature_in_storage_in_celsius=hot_water_temperature_in_storage_in_celsius,
    )
    my_simple_heat_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
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

    water_mass_flow_rate_from_heat_distribution_system = cp.ComponentOutput(
        "FakeWaterMassFlowRateFromHds",
        "WaterMassFlowRateFromHeatDistributionSystem",
        lt.LoadTypes.WARM_WATER,
        lt.Units.KG_PER_SEC,
    )

    water_mass_flow_rate_from_heat_generator = cp.ComponentOutput(
        "FakeWaterMassFlowRateFromHeatGenerator",
        "WaterMassFlowRateFromHeatGenerator",
        lt.LoadTypes.WARM_WATER,
        lt.Units.KG_PER_SEC,
    )

    # connect fake inputs to simple hot water storage
    my_simple_heat_water_storage.water_temperature_heat_distribution_system_input_channel.source_output = (
        water_temperature_input_from_heat_distribution_system
    )
    my_simple_heat_water_storage.water_temperature_heat_generator_input_channel.source_output = (
        water_temperature_input_from_heat_generator
    )
    my_simple_heat_water_storage.water_mass_flow_rate_heat_distrution_system_input_channel.source_output = (
        water_mass_flow_rate_from_heat_distribution_system
    )
    my_simple_heat_water_storage.water_mass_flow_rate_heat_generator_input_channel.source_output = (
        water_mass_flow_rate_from_heat_generator
    )

    number_of_outputs = fft.get_number_of_outputs(
        [
            water_temperature_input_from_heat_distribution_system,
            water_temperature_input_from_heat_generator,
            water_mass_flow_rate_from_heat_distribution_system,
            water_mass_flow_rate_from_heat_generator,
            my_simple_heat_water_storage,
        ]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [
            water_temperature_input_from_heat_distribution_system,
            water_temperature_input_from_heat_generator,
            water_mass_flow_rate_from_heat_distribution_system,
            water_mass_flow_rate_from_heat_generator,
            my_simple_heat_water_storage,
        ]
    )

    stsv.values[water_temperature_input_from_heat_distribution_system.global_index] = 48
    stsv.values[water_temperature_input_from_heat_generator.global_index] = 52
    stsv.values[water_mass_flow_rate_from_heat_distribution_system.global_index] = 0.787
    stsv.values[water_mass_flow_rate_from_heat_generator.global_index] = 0.59

    # Simulate for timestep 300
    timestep = 300

    my_simple_heat_water_storage.i_restore_state()

    # calculate mixing factors for different seconds per timestep
    factors_for_water_storage_portion = []
    seconds_per_timesteps_to_test = [60, 60 * 15, 60 * 30, 60 * 60, 60 * 120]
    for i in seconds_per_timesteps_to_test:
        if i <= 3600:
            factors_for_water_storage_portion.append(i / 3600)
        elif i > 3600:
            factors_for_water_storage_portion.append(1.0)

    # simulate simple hot water storage for different seconds per timestep
    for index, seconds_per_timestep in enumerate(seconds_per_timesteps_to_test):
        log.information("sec per timestep " + str(seconds_per_timestep))
        my_simple_heat_water_storage.seconds_per_timestep = seconds_per_timestep

        my_simple_heat_water_storage.i_simulate(timestep, stsv, False)

        water_temperature_output_in_celsius_to_heat_distribution_system = stsv.values[4]
        water_temperature_output_in_celsius_to_heat_generator = stsv.values[5]

        # test mean water temperature calculation in storage
        mass_water_hds_in_kg = (
            stsv.values[water_mass_flow_rate_from_heat_distribution_system.global_index]
            * seconds_per_timestep
        )
        mass_water_hg_in_kg = (
            stsv.values[water_mass_flow_rate_from_heat_generator.global_index]
            * seconds_per_timestep
        )

        calculated_mean_water_temperature_in_celsius = (
            my_simple_heat_water_storage.water_mass_in_storage_in_kg
            * my_simple_heat_water_storage.start_water_temperature_in_storage_in_celsius
            + mass_water_hg_in_kg
            * stsv.values[water_temperature_input_from_heat_generator.global_index]
            + mass_water_hds_in_kg
            * stsv.values[
                water_temperature_input_from_heat_distribution_system.global_index
            ]
        ) / (
            my_simple_heat_water_storage.water_mass_in_storage_in_kg
            + mass_water_hg_in_kg
            + mass_water_hds_in_kg
        )
        # test if calculated mean water temperature is equal to simulated water temperature
        assert (
            calculated_mean_water_temperature_in_celsius
            == my_simple_heat_water_storage.mean_water_temperature_in_water_storage_in_celsius
        )

        # test mixing factors and water output temperatures

        # test water output temperature for hp
        assert (
            factors_for_water_storage_portion[index]
            * my_simple_heat_water_storage.mean_water_temperature_in_water_storage_in_celsius
            + (1 - factors_for_water_storage_portion[index])
            * stsv.values[
                water_temperature_input_from_heat_distribution_system.global_index
            ]
        ) == water_temperature_output_in_celsius_to_heat_generator

        # test water output temperature for hds
        assert (
            factors_for_water_storage_portion[index]
            * my_simple_heat_water_storage.mean_water_temperature_in_water_storage_in_celsius
            + (1 - factors_for_water_storage_portion[index])
            * stsv.values[water_temperature_input_from_heat_generator.global_index]
        ) == water_temperature_output_in_celsius_to_heat_distribution_system
