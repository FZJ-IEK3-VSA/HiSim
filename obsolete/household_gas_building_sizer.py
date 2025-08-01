"""  Basic household new system setup. """

# clean

from typing import Optional, Any, Union, List
import re
import os
from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    Households,
)
from utspclient.helpers.lpgpythonbindings import JsonReference
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import (
    advanced_battery_bslib,
    controller_l2_energy_management_system,
    simple_water_storage,
    heat_distribution_system,
    generic_hot_water_storage_modular,
    controller_l1_heatpump,
    electricity_meter,
    advanced_ev_battery_bslib,
    controller_l1_generic_ev_charge,
    generic_car,
    generic_boiler,
    generic_heat_source,
    gas_meter,
)

from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
from hisim.postprocessingoptions import PostProcessingOptions
from hisim import loadtypes as lt
from hisim.loadtypes import HeatingSystems
from hisim.building_sizer_utils.interface_configs.modular_household_config import (
    read_in_configs,
    ModularHouseholdConfig,
)
from hisim import log

__authors__ = "Katharina Rieck"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Household system setup.

    This setup function emulates an household including the following components:

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Gas Heater
        - Gas Heater Controller
        - Heat Distribution System
        - Heat Distribution Controller
        - Heat Water Storage
        - Battery
        - Energy Management System
        - Electricity Meter
        - Electric Vehicles (including car batteries and car battery controllers)
    """

    # =================================================================================================================================
    # Set System Parameters from Config

    # household-pv-config
    config_filename = my_sim.my_module_config
    # try reading energ system and archetype configs
    my_config = read_in_configs(my_sim.my_module_config)
    if my_config is None:
        my_config = ModularHouseholdConfig().get_default_config_for_household_gas()
        log.warning(
            f"Could not read the modular household config from path '{config_filename}'. Using the gas household default config instead."
        )
    assert my_config.archetype_config_ is not None
    assert my_config.energy_system_config_ is not None
    arche_type_config_ = my_config.archetype_config_
    energy_system_config_ = my_config.energy_system_config_

    # Set Simulation Parameters
    if my_simulation_parameters is None:
        year = 2021
        seconds_per_timestep = 60 * 15
        my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)
        cache_dir_path_simuparams = "/benchtop/2024-k-rieck-hisim/hisim_inputs_cache/"
        if os.path.exists(cache_dir_path_simuparams):
            my_simulation_parameters.cache_dir_path = cache_dir_path_simuparams
        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
        )
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER
        )
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_LINE)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_CARPET)
        # my_simulation_parameters.post_processing_options.append(PostProcessingOptions.EXPORT_TO_CSV)
        # my_simulation_parameters.logging_level = 4

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # =================================================================================================================================
    # Set System Parameters

    # Set heating systems for space heating and domestic hot water
    heating_system = energy_system_config_.heating_system
    if heating_system != HeatingSystems.GAS_HEATING:
        raise ValueError("Heating system needs to be gas heater for this system setup.")

    heating_reference_temperature_in_celsius = -7.0

    # Set Weather
    weather_location = arche_type_config_.weather_location

    # Set Photovoltaic System
    azimuth = arche_type_config_.pv_azimuth
    tilt = arche_type_config_.pv_tilt
    if arche_type_config_.pv_rooftop_capacity_in_kilowatt is not None:
        pv_power_in_watt = arche_type_config_.pv_rooftop_capacity_in_kilowatt * 1000
    else:
        pv_power_in_watt = None
    share_of_maximum_pv_potential = energy_system_config_.share_of_maximum_pv_potential

    # Set Building (scale building according to total base area and not absolute floor area)
    building_code = arche_type_config_.building_code
    total_base_area_in_m2 = None
    absolute_conditioned_floor_area_in_m2 = arche_type_config_.conditioned_floor_area_in_m2
    number_of_apartments = arche_type_config_.number_of_dwellings_per_building
    if arche_type_config_.norm_heating_load_in_kilowatt is not None:
        max_thermal_building_demand_in_watt = arche_type_config_.norm_heating_load_in_kilowatt * 1000
    else:
        max_thermal_building_demand_in_watt = None

    # Set Occupancy
    # try to get profiles from cluster directory
    cache_dir_path_utsp: Optional[str] = "/benchtop/2024-k-rieck-hisim/lpg-utsp-cache"
    if cache_dir_path_utsp is not None and os.path.exists(cache_dir_path_utsp):
        pass
    # else use default specific cache_dir_path
    else:
        cache_dir_path_utsp = None

    # get household attribute jsonreferences from list of strings
    lpg_households: Union[JsonReference, List[JsonReference]]
    if isinstance(arche_type_config_.lpg_households, List):
        if len(arche_type_config_.lpg_households) == 1:
            lpg_households = getattr(Households, arche_type_config_.lpg_households[0])
        elif len(arche_type_config_.lpg_households) > 1:
            lpg_households = []
            for household_string in arche_type_config_.lpg_households:
                if hasattr(Households, household_string):
                    lpg_household = getattr(Households, household_string)
                    lpg_households.append(lpg_household)
                    print(lpg_household)
        else:
            raise ValueError("Config list with lpg household is empty.")
    else:
        raise TypeError(f"Type {type(arche_type_config_.lpg_households)} is incompatible. Should be List[str].")

    # Set Electric Vehicle
    charging_station_set = ChargingStationSets.Charging_At_Home_with_11_kW
    charging_power = float((charging_station_set.Name or "").split("with ")[1].split(" kW")[0])

    # =================================================================================================================================
    # Build Basic Components
    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home(
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
        max_thermal_building_demand_in_watt=max_thermal_building_demand_in_watt,
    )
    my_building_config.building_code = building_code
    my_building_config.total_base_area_in_m2 = total_base_area_in_m2
    my_building_config.absolute_conditioned_floor_area_in_m2 = absolute_conditioned_floor_area_in_m2
    my_building_config.number_of_apartments = number_of_apartments
    my_building_config.enable_opening_windows = True
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    # Add to simulator
    my_sim.add_component(my_building, connect_automatically=True)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy_config.data_acquisition_mode = loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_UTSP
    my_occupancy_config.household = lpg_households
    my_occupancy_config.cache_dir_path = cache_dir_path_utsp

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
    if pv_power_in_watt is None:
        my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
            rooftop_area_in_m2=my_building_information.scaled_rooftop_area_in_m2,
            share_of_maximum_pv_potential=share_of_maximum_pv_potential,
            location=weather_location,
        )
    else:
        my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_default_pv_system(
            power_in_watt=pv_power_in_watt,
            share_of_maximum_pv_potential=share_of_maximum_pv_potential,
            location=weather_location,
        )

    my_photovoltaic_system_config.azimuth = azimuth
    my_photovoltaic_system_config.tilt = tilt

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
    # my_heat_distribution_controller_config.heating_system = heat_distribution_system.HeatDistributionSystemType.RADIATOR

    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters, config=my_heat_distribution_controller_config,
    )
    my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
        config=my_heat_distribution_controller_config
    )
    # Add to simulator
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)

    # Set sizing option for Hot water Storage
    sizing_option = simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GAS_HEATER

    # Build Gas heater For Space Heating
    my_gas_heater_config = generic_boiler.GenericBoilerConfig.get_scaled_condensing_gas_boiler_config(
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt
    )
    my_gas_heater = generic_boiler.GenericBoiler(
        config=my_gas_heater_config, my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_gas_heater, connect_automatically=True)

    # Build Gas Heater Controller
    my_gas_heater_controller_config = generic_boiler.GenericBoilerControllerConfig.get_default_modulating_generic_boiler_controller_config(
        minimal_thermal_power_in_watt=my_gas_heater_config.minimal_thermal_power_in_watt, maximal_thermal_power_in_watt=my_gas_heater_config.maximal_thermal_power_in_watt
    )
    my_gas_heater_controller = generic_boiler.GenericBoilerController(
        my_simulation_parameters=my_simulation_parameters, config=my_gas_heater_controller_config,
    )
    my_sim.add_component(my_gas_heater_controller, connect_automatically=True)

    # Build Gas Heater for DHW
    my_gas_heater_for_dhw_config = generic_heat_source.HeatSourceConfig.get_default_config_waterheating(
        heating_system=lt.HeatingSystems.GAS_HEATING,
        boiler_type=my_gas_heater_config.boiler_type,
        max_warm_water_demand_in_liter=my_occupancy.max_hot_water_demand,
        scaling_factor_according_to_number_of_apartments=my_occupancy.scaling_factor_according_to_number_of_apartments,
        seconds_per_timestep=my_simulation_parameters.seconds_per_timestep,
        name="DHW" + lt.HeatingSystems.GAS_HEATING.value
    )
    my_gas_heater_controller_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
        "DHW" + lt.HeatingSystems.GAS_HEATING.value + "Controller"
    )
    my_boiler_config = generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(
        number_of_apartments=my_building_information.number_of_apartments
    )
    my_boiler_config.compute_default_cycle(
        temperature_difference_in_kelvin=my_gas_heater_controller_l1_config.t_max_heating_in_celsius
        - my_gas_heater_controller_l1_config.t_min_heating_in_celsius
    )

    my_boiler_for_dhw = generic_hot_water_storage_modular.HotWaterStorage(
        my_simulation_parameters=my_simulation_parameters, config=my_boiler_config
    )

    my_heater_controller_l1_for_dhw = controller_l1_heatpump.L1HeatPumpController(
        my_simulation_parameters=my_simulation_parameters, config=my_gas_heater_controller_l1_config
    )

    my_gas_heater_for_dhw = generic_heat_source.HeatSource(
        config=my_gas_heater_for_dhw_config, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_gas_heater_for_dhw, connect_automatically=True)
    my_sim.add_component(my_boiler_for_dhw, connect_automatically=True)
    my_sim.add_component(my_heater_controller_l1_for_dhw, connect_automatically=True)

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
        max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
        temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
        sizing_option=sizing_option,
    )
    my_simple_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config, my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_simple_water_storage, connect_automatically=True)

    # Build Heat Distribution System
    my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
        water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
        absolute_conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
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

    # Build Gas Meter
    my_gas_meter = gas_meter.GasMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=gas_meter.GasMeterConfig.get_gas_meter_default_config(),
    )
    my_sim.add_component(my_gas_meter, connect_automatically=True)

    # Build Electric Vehicle Configs and Car Battery Configs
    my_car_config = generic_car.CarConfig.get_default_ev_config()
    my_car_battery_config = advanced_ev_battery_bslib.CarBatteryConfig.get_default_config()
    my_car_battery_controller_config = controller_l1_generic_ev_charge.ChargingStationConfig.get_default_config(
        charging_station_set=charging_station_set
    )
    # set car config name
    my_car_config.name = "ElectricCar"
    # set charging power from battery and controller to same value, to reduce error in simulation of battery
    my_car_battery_config.p_inv_custom = charging_power * 1e3
    # lower threshold for soc of car battery in clever case. This enables more surplus charging. Surplus control of car
    my_car_battery_controller_config.battery_set = 0.6
    # Build Electric Vehicles
    my_car_information = generic_car.GenericCarInformation(my_occupancy_instance=my_occupancy)
    my_cars: List[generic_car.Car] = []
    my_car_batteries: List[advanced_ev_battery_bslib.CarBattery] = []
    my_car_battery_controllers: List[controller_l1_generic_ev_charge.L1Controller] = []
    # iterate over all cars
    car_number = 1
    for car_information_dict in my_car_information.data_dict_for_car_component.values():
        # Build Electric Vehicles
        my_car_config.name = f"ElectricCar_{car_number}"
        my_car = generic_car.Car(
            my_simulation_parameters=my_simulation_parameters,
            config=my_car_config,
            data_dict_with_car_information=car_information_dict,
        )
        my_cars.append(my_car)
        # Build Electric Vehicle Batteries
        my_car_battery_config.source_weight = my_car.config.source_weight
        my_car_battery_config.name = f"CarBattery_{car_number}"
        my_car_battery = advanced_ev_battery_bslib.CarBattery(
            my_simulation_parameters=my_simulation_parameters, config=my_car_battery_config,
        )
        my_car_batteries.append(my_car_battery)
        # Build Electric Vehicle Battery Controller
        my_car_battery_controller_config.source_weight = my_car.config.source_weight
        my_car_battery_controller_config.name = f"L1EVChargeControl_{car_number}"

        my_car_battery_controller = controller_l1_generic_ev_charge.L1Controller(
            my_simulation_parameters=my_simulation_parameters, config=my_car_battery_controller_config,
        )
        my_car_battery_controllers.append(my_car_battery_controller)
        car_number += 1

    # Connect Electric Vehicles and Car Batteries
    zip_car_battery_controller_lists = list(zip(my_cars, my_car_batteries, my_car_battery_controllers))
    for car, car_battery, car_battery_controller in zip_car_battery_controller_lists:
        car_battery_controller.connect_only_predefined_connections(car)
        car_battery_controller.connect_only_predefined_connections(car_battery)
        car_battery.connect_only_predefined_connections(car_battery_controller)

    # use ems and battery only when PV is used
    if share_of_maximum_pv_potential != 0 and energy_system_config_.use_battery_and_ems:

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
            source_weight=4,
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

        # -----------------------------------------------------------------------------------------------------------------
        # Connect Electric Vehicle and Car Battery with EMS for surplus control

        for car, car_battery, car_battery_controller in list(zip_car_battery_controller_lists):

            my_electricity_controller.add_component_input_and_connect(
                source_object_name=car_battery_controller.component_name,
                source_component_output=car_battery_controller.BatteryChargingPowerToEMS,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.CAR_BATTERY, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
                source_weight=5,
            )

            electricity_target = my_electricity_controller.add_component_output(
                source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
                source_tags=[lt.ComponentType.CAR_BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
                source_weight=5,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                output_description="Target Electricity for EV Battery Controller. ",
            )

            car_battery_controller.connect_dynamic_input(
                input_fieldname=controller_l1_generic_ev_charge.L1Controller.ElectricityTarget,
                src_object=electricity_target,
            )

        # =================================================================================================================================
        # Add Remaining Components to Simulation Parameters

        my_sim.add_component(my_electricity_meter)
        my_sim.add_component(my_advanced_battery)
        my_sim.add_component(my_electricity_controller, connect_automatically=True)

    # when no PV is used, connect electricty meter automatically
    else:
        my_sim.add_component(my_electricity_meter, connect_automatically=True)

    # Connect Electric Vehicles and Car Batteries
    for car in my_cars:
        my_sim.add_component(car)
    for car_battery in my_car_batteries:
        my_sim.add_component(car_battery)
    for car_battery_controller in my_car_battery_controllers:
        my_sim.add_component(car_battery_controller)

    # Set Results Path
    # if config_filename is given, get hash number and sampling mode for result path
    if config_filename is not None:
        config_filename_splitted = config_filename.split("/")

        try:
            scenario_hash_string = re.findall(r"\-?\d+", config_filename_splitted[-1])[0]
            sorting_option = SortingOptionEnum.MASS_SIMULATION_WITH_HASH_ENUMERATION
        except Exception:
            scenario_hash_string = "-"
            sorting_option = SortingOptionEnum.MASS_SIMULATION_WITH_INDEX_ENUMERATION
        try:
            further_result_folder_description = config_filename_splitted[-2]
        except Exception:
            further_result_folder_description = "-"

    # if config_filename is not given, make result path with index enumeration
    else:
        scenario_hash_string = "default_scenario"
        sorting_option = SortingOptionEnum.MASS_SIMULATION_WITH_INDEX_ENUMERATION
        further_result_folder_description = "default_config"

    SingletonSimRepository().set_entry(
        key=SingletonDictKeyEnum.RESULT_SCENARIO_NAME, entry=f"{scenario_hash_string}",
    )

    if my_simulation_parameters.result_directory == "":

        ResultPathProviderSingleton().set_important_result_path_information(
            module_directory=my_sim.module_directory,  # "/storage_cluster/projects/2024_waage/01_hisim_results",
            model_name=my_sim.module_filename,
            further_result_folder_description=os.path.join(*[further_result_folder_description,]),
            variant_name="_",
            scenario_hash_string=scenario_hash_string,
            sorting_option=sorting_option,
        )
