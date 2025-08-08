"""Test for energy management system.

Run a normal house with heatpumps, PV and battery and compare EMS outputs with KPI values.
Investigate total consumption, total grid consumption and grid injection.
"""

# clean

import os
import json
from typing import Optional
import pytest
import numpy as np
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import (
    building,
    electricity_meter,
    generic_pv_system,
    heat_distribution_system,
    advanced_battery_bslib,
    advanced_heat_pump_hplib,
    controller_l2_energy_management_system,
    generic_heat_pump_modular,
    controller_l1_heatpump,
    generic_hot_water_storage_modular,
    simple_water_storage,
)
from hisim import utils
import hisim.loadtypes as lt

from hisim.postprocessingoptions import PostProcessingOptions
from hisim.units import Quantity, Celsius, Watt

# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../system_setups/household_for_test_ems.py"


@utils.measure_execution_time
@pytest.mark.base
def test_house(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """The test should check if a normal simulation works with the ems implementation."""

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # =========================================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.one_week_only(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
        my_simulation_parameters.logging_level = 3
    # this part is copied from hisim_main
    # Build Simulator
    normalized_path = os.path.normpath(PATH)
    path_in_list = normalized_path.split(os.sep)
    if len(path_in_list) >= 1:
        path_to_be_added = os.path.join(os.getcwd(), *path_in_list[:-1])

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_for_test_ems",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # =================================================================================================================================
    # Build Components
    building_name = "BUI1"
    heating_reference_temperature_in_celsius = -7.0

    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home(building_name=building_name,
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,)
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    # Add to simulator
    my_sim.add_component(my_building, connect_automatically=True)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config(building_name=building_name,)
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )
    # Add to simulator
    my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN, building_name=building_name,)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)
    # Add to simulator
    my_sim.add_component(my_weather)

    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
        building_name=building_name,
        rooftop_area_in_m2=my_building_information.roof_area_in_m2,
        share_of_maximum_pv_potential=1.0,
        module_name="Hanwha HSL60P6-PA-4-250T [2013]",
        module_database=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE,
        inverter_name="ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_",
        inverter_database=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE)
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,)
    # Add to simulator
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)

    # Build Heat Distribution Controller
    my_heat_distribution_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
        building_name=building_name,
        set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
        set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
    )
    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_heat_distribution_controller_config,
    )
    my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
        config=my_heat_distribution_controller_config
    )
    # Add to simulator
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)

    # Build Heat Pump Controller
    my_heat_pump_controller_config = (
        advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config.get_default_generic_heat_pump_controller_config(
            building_name=building_name,
            heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type
        )
    )
    my_heat_pump_controller = advanced_heat_pump_hplib.HeatPumpHplibController(
        config=my_heat_pump_controller_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_heat_pump_controller, connect_automatically=True)

    # Build Heat Pump
    my_heat_pump_config = advanced_heat_pump_hplib.HeatPumpHplibConfig.get_scaled_advanced_hp_lib(
        building_name=building_name,
        heating_load_of_building_in_watt=Quantity(my_building_information.max_thermal_building_demand_in_watt, Watt),
        heating_reference_temperature_in_celsius=Quantity(heating_reference_temperature_in_celsius, Celsius),
    )

    my_heat_pump = advanced_heat_pump_hplib.HeatPumpHplib(
        config=my_heat_pump_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_heat_pump, connect_automatically=True)

    # Build Heat Distribution System
    my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
        building_name=building_name,
        water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
        absolute_conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
        heating_system=my_hds_controller_information.hds_controller_config.heating_system,
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_heat_distribution_system, connect_automatically=True)

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
        building_name=building_name,
        max_thermal_power_in_watt_of_heating_system=my_heat_pump_config.set_thermal_output_power_in_watt.value,
        temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
        sizing_option=simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP,
    )
    my_simple_hot_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)

    # Build DHW (this is taken from household_3_advanced_hp_diesel-car_pv_battery.py)
    my_dhw_heatpump_config = generic_heat_pump_modular.HeatPumpConfig.get_scaled_waterheating_to_number_of_apartments(
        building_name=building_name,
        number_of_apartments=my_building_information.number_of_apartments,
        default_power_in_watt=6000,
    )

    my_dhw_heatpump_controller_config = (
        controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
            name="DHWHeatpumpController"
        )
    )

    my_dhw_storage_config = (
        generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(
            building_name=building_name,
            number_of_apartments=my_building_information.number_of_apartments,
            default_volume_in_liter=450,
        )
    )
    my_dhw_storage_config.compute_default_cycle(
        temperature_difference_in_kelvin=my_dhw_heatpump_controller_config.t_max_heating_in_celsius
        - my_dhw_heatpump_controller_config.t_min_heating_in_celsius
    )

    my_domnestic_hot_water_storage = generic_hot_water_storage_modular.HotWaterStorage(
        my_simulation_parameters=my_simulation_parameters, config=my_dhw_storage_config
    )

    my_domnestic_hot_water_heatpump_controller = controller_l1_heatpump.L1HeatPumpController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_dhw_heatpump_controller_config,
    )

    my_domnestic_hot_water_heatpump = generic_heat_pump_modular.ModularHeatPump(
        config=my_dhw_heatpump_config, my_simulation_parameters=my_simulation_parameters
    )
    # Add to simulator
    my_sim.add_component(my_domnestic_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_heatpump_controller, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_heatpump, connect_automatically=True)

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(building_name=building_name,),
    )

    # Build EMS
    my_electricity_controller_config = controller_l2_energy_management_system.EMSConfig.get_default_config_ems(building_name=building_name,)

    my_electricity_controller = controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
        my_simulation_parameters=my_simulation_parameters,
        config=my_electricity_controller_config,
    )

    # Build Battery
    my_advanced_battery_config = advanced_battery_bslib.BatteryConfig.get_scaled_battery(
        building_name=building_name,
        total_pv_power_in_watt_peak=my_photovoltaic_system_config.power_in_watt
    )
    my_advanced_battery = advanced_battery_bslib.Battery(
        my_simulation_parameters=my_simulation_parameters,
        config=my_advanced_battery_config,
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
    # Compare EMS outputs and KPI values

    # Read kpi data
    with open(
        os.path.join(my_sim._simulation_parameters.result_directory, "all_kpis.json"), "r", encoding="utf-8"  # pylint: disable=W0212
    ) as file:
        jsondata = json.load(file)

    jsondata = jsondata[building_name]

    # Get general KPI values
    total_consumption_kpi_in_kilowatt_hour = jsondata["General"]["Total electricity consumption"].get("value")
    electricity_from_grid_kpi_in_kilowatt_hour = jsondata["Electricity Meter"]["Total energy from grid"].get("value")
    electricity_to_grid_kpi_in_kilowatt_hour = jsondata["Electricity Meter"]["Total energy to grid"].get("value")
    other_kpi_grid_injection_in_kilowatt_hour = jsondata["General"]["Grid injection of electricity"].get("value")

    # Get battery KPI values
    battery_charging_energy_in_kilowatt_hour = jsondata["Battery"]["Battery charging energy"].get("value")
    battery_discharging_energy_in_kilowatt_hour = jsondata["Battery"]["Battery discharging energy"].get("value")
    battery_losses_in_kilowatt_hour = jsondata["Battery"]["Battery losses"].get("value")
    print("battery charging energy ", battery_charging_energy_in_kilowatt_hour)
    print("battery discharging energy ", battery_discharging_energy_in_kilowatt_hour)
    print("battery losses ", battery_losses_in_kilowatt_hour)
    print("\n")

    # Get total consumptions of components
    residents_total_consumption_kpi_in_kilowatt_hour = jsondata["Residents"][
        "Residents' total electricity consumption"
    ].get("value")
    space_heating_heatpump_total_consumption_kpi_in_kilowatt_hour = jsondata["Heat Pump For Space Heating"][
        "Total electrical input energy of SH heat pump"
    ].get("value")
    domestic_hot_water_heatpump_total_consumption_kpi_in_kilowatt_hour = jsondata["Heat Pump For Domestic Hot Water"][
        "DHW heat pump total electricity consumption"
    ].get("value")

    sum_component_total_consumptions_in_kilowatt_hour = (
        residents_total_consumption_kpi_in_kilowatt_hour
        + space_heating_heatpump_total_consumption_kpi_in_kilowatt_hour
        + domestic_hot_water_heatpump_total_consumption_kpi_in_kilowatt_hour
        + battery_losses_in_kilowatt_hour
    )
    print("occupancy total consumption ", residents_total_consumption_kpi_in_kilowatt_hour)
    print("sh hp total consumption ", space_heating_heatpump_total_consumption_kpi_in_kilowatt_hour)
    print("dhw hp total consumption ", domestic_hot_water_heatpump_total_consumption_kpi_in_kilowatt_hour)
    print("sum of components' total consumptions ", sum_component_total_consumptions_in_kilowatt_hour)
    print("\n")

    # Get grid consumptions of components
    residents_grid_consumption_kpi_in_kilowatt_hour = jsondata["Energy Management System"][
        "Residents' electricity consumption from grid"
    ].get("value")
    space_heating_heatpump_grid_consumption_kpi_in_kilowatt_hour = jsondata["Energy Management System"][
        "Space heating heat pump electricity from grid"
    ].get("value")
    domestic_hot_water_heatpump_grid_consumption_kpi_in_kilowatt_hour = jsondata["Energy Management System"][
        "Domestic hot water heat pump electricity from grid"
    ].get("value")

    sum_component_grid_consumptions_in_kilowatt_hour = (
        residents_grid_consumption_kpi_in_kilowatt_hour
        + space_heating_heatpump_grid_consumption_kpi_in_kilowatt_hour
        + domestic_hot_water_heatpump_grid_consumption_kpi_in_kilowatt_hour
        - battery_discharging_energy_in_kilowatt_hour
    )

    print("occupancy grid consumption ", residents_grid_consumption_kpi_in_kilowatt_hour)
    print("sh hp grid consumption ", space_heating_heatpump_grid_consumption_kpi_in_kilowatt_hour)
    print("dhw hp grid consumption ", domestic_hot_water_heatpump_grid_consumption_kpi_in_kilowatt_hour)
    print("sum of components' grid consumptions ", sum_component_grid_consumptions_in_kilowatt_hour)
    print("\n")

    # Get EMS output TotalElectricityConsumption
    simulation_results_ems_total_consumption_in_watt = my_sim.results_data_frame[
        "L2EMSElectricityController - TotalElectricityConsumption [Electricity - W]"
    ]

    ems_total_consumption_in_kilowatt_hour = (
        sum(simulation_results_ems_total_consumption_in_watt) * seconds_per_timestep / 3.6e6
    )

    # Get EMS output ElectricityToOrFromGrid -> get grid consumption by filterig only values < 0
    simulation_results_ems_grid_consumption_in_watt = abs(
        my_sim.results_data_frame["L2EMSElectricityController - TotalElectricityToOrFromGrid [Electricity - W]"].loc[
            my_sim.results_data_frame["L2EMSElectricityController - TotalElectricityToOrFromGrid [Electricity - W]"]
            < 0.0
        ]
    )
    ems_grid_consumption_in_kilowatt_hour = (
        sum(simulation_results_ems_grid_consumption_in_watt) * seconds_per_timestep / 3.6e6
    )

    # Get EMS output ElectricityToOrFromGrid -> get grid injection by filterig only values > 0
    simulation_results_ems_grid_injection_in_watt = my_sim.results_data_frame[
        "L2EMSElectricityController - TotalElectricityToOrFromGrid [Electricity - W]"].loc[
        my_sim.results_data_frame["L2EMSElectricityController - TotalElectricityToOrFromGrid [Electricity - W]"] > 0.0
    ]
    ems_grid_injection_in_kilowatt_hour = (
        sum(simulation_results_ems_grid_injection_in_watt) * seconds_per_timestep / 3.6e6
    )

    # =========================================================================================================================================================
    # Test total electricity consumption
    print("ems total consumption ", ems_total_consumption_in_kilowatt_hour)
    print("kpi total consumption ", total_consumption_kpi_in_kilowatt_hour)
    print("sum of components' total consumptions ", sum_component_total_consumptions_in_kilowatt_hour)
    print("\n")
    np.testing.assert_allclose(
        ems_total_consumption_in_kilowatt_hour,
        total_consumption_kpi_in_kilowatt_hour,
        rtol=0.05,
    )
    np.testing.assert_allclose(
        total_consumption_kpi_in_kilowatt_hour,
        sum_component_total_consumptions_in_kilowatt_hour,
        rtol=0.05,
    )

    # Test grid consumption
    print("ems grid consumption ", ems_grid_consumption_in_kilowatt_hour)
    print("em grid consumption ", electricity_from_grid_kpi_in_kilowatt_hour)
    print("sum of components' grid consumptions ", sum_component_grid_consumptions_in_kilowatt_hour)
    print("\n")
    np.testing.assert_allclose(
        ems_grid_consumption_in_kilowatt_hour,
        electricity_from_grid_kpi_in_kilowatt_hour,
        rtol=0.05,
    )
    np.testing.assert_allclose(
        electricity_from_grid_kpi_in_kilowatt_hour,
        sum_component_grid_consumptions_in_kilowatt_hour,
        rtol=0.05,
    )

    # Test grid injection
    print("ems grid injection ", ems_grid_injection_in_kilowatt_hour)
    print("em grid injection ", electricity_to_grid_kpi_in_kilowatt_hour)
    print("other kpi grid injection ", other_kpi_grid_injection_in_kilowatt_hour)

    print("\n")
    np.testing.assert_allclose(
        ems_grid_consumption_in_kilowatt_hour,
        electricity_from_grid_kpi_in_kilowatt_hour,
        rtol=0.05,
    )
    np.testing.assert_allclose(
        electricity_to_grid_kpi_in_kilowatt_hour,
        other_kpi_grid_injection_in_kilowatt_hour,
        rtol=0.05,
    )
