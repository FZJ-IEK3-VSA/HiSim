"""Test if components deliver similar results for different time resolutions.

Here we test the cluster household.
"""

# clean

import os
from typing import Dict, List, Tuple
import pandas as pd
import pytest
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.components import (
    building,
    weather,
    loadprofilegenerator_utsp_connector,
    generic_pv_system,
    advanced_heat_pump_hplib,
    advanced_battery_bslib,
    controller_l2_energy_management_system,
    simple_water_storage,
    heat_distribution_system,
    generic_heat_pump_modular,
    generic_hot_water_storage_modular,
    controller_l1_heatpump,
    electricity_meter,
)
from hisim.units import Quantity, Celsius, Watt
from hisim import loadtypes as lt


def values_are_similar(lst: List, relative_tolerance: float = 0.05) -> bool:
    """Function to check if values are similar within a certain tolerance (rel tolerance = 5%, absolute tolerance = 0.1)."""
    return all(abs(x - lst[0]) / x <= relative_tolerance for x in lst) or all(abs(x - lst[0]) <= 0.1 for x in lst)


# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../system_setups/household_test_timeresolutions.py"


@utils.measure_execution_time
@pytest.mark.base
def test_cluster_house_for_several_time_resolutions():
    """Test cluster house for several time resolutions."""

    opex_consumption_dict: Dict = {}
    yearly_results_dict: Dict = {}
    # do not use seconds per timestep = 60 because test takes then too long
    for seconds_per_timestep in [60 * 15, 60 * 30, 60 * 60]:
        print("\n")
        print("Seconds per timestep ", seconds_per_timestep)
        # run simulation of cluster house
        result_dict, opex_consumption_dict = run_cluster_house(
            seconds_per_timestep=seconds_per_timestep,
            yearly_result_dict=yearly_results_dict,
            opex_consumptions_dict=opex_consumption_dict,
        )

    # go through all results and compare if aggregated results are all the same
    print("\n")
    print("Yearly results including KPIs")
    for key, values in result_dict.items():
        # for these components the outputs must be identical as they are predefined input data
        if loadprofilegenerator_utsp_connector.UtspLpgConnector.get_classname() in key:
            assert values_are_similar(lst=values)
        if generic_pv_system.PVSystem.get_classname() in key:
            assert values_are_similar(lst=values)
        if weather.Weather.get_classname() in key:
            assert values_are_similar(lst=values)
        if not values_are_similar(lst=values):
            print(key, values, "not all similar. ")
    # go through all opex consumptions and compare if results are all the same
    print("\n")
    print("Opex consumtions in kWh")
    for key, values in opex_consumption_dict.items():
        if not values_are_similar(lst=values):
            print(key, values, "not all similar. ")

    print(
        "Please make sure that your data is correctly resampled. "
        "In some cases different time resolutions can lead to different calculation results."
    )


def run_cluster_house(
    seconds_per_timestep: int, yearly_result_dict: Dict, opex_consumptions_dict: Dict
) -> Tuple[Dict, Dict]:  # noqa: too-many-statements
    """The test should check if a normal simulation works with the electricity grid implementation."""

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    # Build Simulation Parameters

    my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )

    # this part is copied from hisim_main
    # Build Simulator
    normalized_path = os.path.normpath(PATH)
    path_in_list = normalized_path.split(os.sep)
    if len(path_in_list) >= 1:
        path_to_be_added = os.path.join(os.getcwd(), *path_in_list[:-1])

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_test_timeresolutions",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # =================================================================================================================================
    # Set System Parameters

    # Set Heat Pump Controller
    hp_controller_mode = 2  # mode 1 for heating/off and mode 2 for heating/cooling/off
    heating_reference_temperature_in_celsius = -7.0

    # Set Weather
    weather_location = "AACHEN"

    # =================================================================================================================================
    # Build Basic Components
    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home(
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius
    )
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    # Add to simulator
    my_sim.add_component(my_building, connect_automatically=True)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy_config.data_acquisition_mode = (
        loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_PREDEFINED_PROFILE
    )

    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )
    # Add to simulator
    my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather_location)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)
    # Add to simulator
    my_sim.add_component(my_weather)

    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
        rooftop_area_in_m2=my_building_information.roof_area_in_m2,
        share_of_maximum_pv_potential=1,
        location=weather_location,
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config, my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)

    # Build Heat Distribution Controller
    my_heat_distribution_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
        set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
        set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
    )

    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters, config=my_heat_distribution_controller_config,
    )
    my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
        config=my_heat_distribution_controller_config
    )
    # Add to simulator
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)

    # Set sizing option for Hot water Storage
    sizing_option = simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP

    # Build Heat Pump Controller
    my_heat_pump_controller_config = advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config.get_default_generic_heat_pump_controller_config(
        heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type
    )
    my_heat_pump_controller_config.mode = hp_controller_mode

    my_heat_pump_controller = advanced_heat_pump_hplib.HeatPumpHplibController(
        config=my_heat_pump_controller_config, my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_heat_pump_controller, connect_automatically=True)

    # Build Heat Pump
    my_heat_pump_config = advanced_heat_pump_hplib.HeatPumpHplibConfig.get_scaled_advanced_hp_lib(
        heating_load_of_building_in_watt=Quantity(my_building_information.max_thermal_building_demand_in_watt, Watt),
        heating_reference_temperature_in_celsius=Quantity(heating_reference_temperature_in_celsius, Celsius),
    )

    my_heat_pump = advanced_heat_pump_hplib.HeatPumpHplib(
        config=my_heat_pump_config, my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_heat_pump, connect_automatically=True)

    # Build DHW (this is taken from household_3_advanced_hp_diesel-car_pv_battery.py)
    my_dhw_heatpump_config = generic_heat_pump_modular.HeatPumpConfig.get_scaled_waterheating_to_number_of_apartments(
        number_of_apartments=my_building_information.number_of_apartments, default_power_in_watt=6000,
    )
    my_dhw_heatpump_controller_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
        name="DHWHeatpumpController"
    )
    my_dhw_storage_config = generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(
        number_of_apartments=my_building_information.number_of_apartments, default_volume_in_liter=450,
    )
    my_dhw_storage_config.compute_default_cycle(
        temperature_difference_in_kelvin=my_dhw_heatpump_controller_config.t_max_heating_in_celsius
        - my_dhw_heatpump_controller_config.t_min_heating_in_celsius
    )
    my_domnestic_hot_water_storage = generic_hot_water_storage_modular.HotWaterStorage(
        my_simulation_parameters=my_simulation_parameters, config=my_dhw_storage_config
    )
    my_domnestic_hot_water_heatpump_controller = controller_l1_heatpump.L1HeatPumpController(
        my_simulation_parameters=my_simulation_parameters, config=my_dhw_heatpump_controller_config,
    )
    my_domnestic_hot_water_heatpump = generic_heat_pump_modular.ModularHeatPump(
        config=my_dhw_heatpump_config, my_simulation_parameters=my_simulation_parameters
    )
    # Add to simulator
    my_sim.add_component(my_domnestic_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_heatpump_controller, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_heatpump, connect_automatically=True)

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
        max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
        temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
        sizing_option=sizing_option,
    )
    my_simple_hot_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config, my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)

    # Build Heat Distribution System
    my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
        water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
        absolute_conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
        heating_system=my_hds_controller_information.hds_controller_config.heating_system,
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config, my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_heat_distribution_system, connect_automatically=True)

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )

    # Build EMS
    my_electricity_controller_config = controller_l2_energy_management_system.EMSConfig.get_default_config_ems()

    my_electricity_controller = controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
        my_simulation_parameters=my_simulation_parameters, config=my_electricity_controller_config,
    )

    # Build Battery
    my_advanced_battery_config = advanced_battery_bslib.BatteryConfig.get_scaled_battery(
        total_pv_power_in_watt_peak=my_photovoltaic_system_config.power_in_watt
    )
    my_advanced_battery = advanced_battery_bslib.Battery(
        my_simulation_parameters=my_simulation_parameters, config=my_advanced_battery_config,
    )

    # -----------------------------------------------------------------------------------------------------------------
    # Add outputs to EMS
    loading_power_input_for_battery_in_watt = my_electricity_controller.add_component_output(
        source_output_name="LoadingPowerInputForBattery_",
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=5,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for Battery Control. ",
    )

    # -----------------------------------------------------------------------------------------------------------------
    # Connect Battery
    my_advanced_battery.connect_dynamic_input(
        input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput,
        src_object=loading_power_input_for_battery_in_watt,
    )

    # -----------------------------------------------------------------------------------------------------------------
    # Connect Electricity Meter
    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_electricity_controller.component_name,
        source_component_output=my_electricity_controller.TotalElectricityToOrFromGrid,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=999,
    )

    # =================================================================================================================================
    # Add Remaining Components to Simulation Parameters

    my_sim.add_component(my_electricity_meter)
    my_sim.add_component(my_advanced_battery)
    my_sim.add_component(my_electricity_controller, connect_automatically=True)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Get yearly results from scenario preparation
    yearly_results_path = os.path.join(
        my_simulation_parameters.result_directory, "result_data_for_scenario_evaluation", "yearly_365_days.csv"
    )
    yearly_results = pd.read_csv(yearly_results_path, usecols=["variable", "value"])
    # Get opex consumptions
    opex_results_path = os.path.join(my_simulation_parameters.result_directory, "operational_costs_co2_footprint.csv")

    opex_df = pd.read_csv(opex_results_path, index_col=0, sep=";")

    # append results to result dictionaries
    if yearly_result_dict == {} and opex_consumptions_dict == {}:
        yearly_result_dict.update(
            {yearly_results["variable"][i]: [yearly_results["value"][i]] for i in range(len(yearly_results))}
        )
        for j in range(len(opex_df)):
            opex_consumptions_dict.update({opex_df.index[j]: [opex_df["Total energy consumption [kWh]"].iloc[j]]})
    else:
        for i in range(len(yearly_results)):
            yearly_result_dict[yearly_results["variable"][i]].append(yearly_results["value"][i])

        for j in range(len(opex_df)):
            opex_consumptions_dict[opex_df.index[j]].append(opex_df["Total energy consumption [kWh]"].iloc[j])

    return yearly_result_dict, opex_consumptions_dict
