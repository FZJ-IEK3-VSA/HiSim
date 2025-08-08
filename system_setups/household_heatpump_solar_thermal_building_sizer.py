"""Basic household system setup. Shows how to set up a standard system."""

from typing import Optional, Any, Union, List
import os
import re
from utspclient.helpers.lpgdata import (
    Households,
)
from utspclient.helpers.lpgpythonbindings import JsonReference
from hisim.building_sizer_utils.interface_configs.modular_household_config import (
    ModularHouseholdConfig,
    read_in_configs,
)
from hisim.simulator import SimulationParameters
from hisim.components import (
    more_advanced_heat_pump_hplib,
    heat_distribution_system,
    loadprofilegenerator_utsp_connector,
    simple_water_storage,
    solar_thermal_system,
    advanced_battery_bslib,
    controller_l2_energy_management_system,
    generic_pv_system,
)
from hisim.components import weather
from hisim.components import building
from hisim.components import electricity_meter
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
from hisim.postprocessingoptions import PostProcessingOptions
from hisim import loadtypes as lt
from hisim.units import Quantity, Celsius, Watt
from hisim.loadtypes import HeatingSystems
from hisim import log

__authors__ = "Kristina Dabrock, Katharina Rieck"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Kristina Dabrock"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Kristina Dabrock"
__email__ = "k.dabrock@fz-juelich.de"
__status__ = "development"


def setup_function(
    my_sim: Any,
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """Basic household system setup.

    This setup function emulates an household including the basic components. Here the residents have their
    electricity and warm water and heating needs covered by the solar thermal system and a gas boiler.
    Furthermore, a DHW storage is installed.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - SolarThermalSystem
        - SolarThermalSystemController
        - Building
        - Heat Pump
        - Heat Pump Controller
        - Heat Distribution System
        - Heat Distribution Controller
        - DHW Water Storage
    """

    # =================================================================================================================================

    config_filename = my_sim.my_module_config
    # try reading energ system and archetype configs
    my_config = read_in_configs(my_sim.my_module_config)
    if my_config is None:
        my_config = ModularHouseholdConfig().get_default_config_for_household_heatpump_solar_thermal()
        log.warning(
            f"Could not read the modular household config from path '{config_filename}'. Using the heatpump and solar thermal household default config instead."
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
        # my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
        # my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_LINE)
        # my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_CARPET)
        # my_simulation_parameters.post_processing_options.append(PostProcessingOptions.EXPORT_TO_CSV)
        my_simulation_parameters.logging_level = 3
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # =================================================================================================================================
    # Build Components

    # Set heating systems for space heating and domestic hot water
    heating_system = energy_system_config_.heating_system
    if heating_system != HeatingSystems.HEAT_PUMP_SOLAR_THERMAL:
        raise ValueError("Heating system needs to be heat pump solar thermal for this system setup.")

    heating_reference_temperature_in_celsius = -7.0
    building_set_heating_temperature_in_celsius = 22.0
    hp_controller_mode = 2  # mode 1 for heating/off and mode 2 for heating/cooling/off

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

    # =================================================================================================================================
    # Build Basic Components

    # Building
    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home(
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
        max_thermal_building_demand_in_watt=max_thermal_building_demand_in_watt,
        set_heating_temperature_in_celsius=building_set_heating_temperature_in_celsius,
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

    # Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy_config.data_acquisition_mode = loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_LOCAL_LPG
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
            rooftop_area_in_m2=my_building_information.roof_area_in_m2,
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
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)

    # Heat Distribution Controller
    my_heat_distribution_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
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

    # Build Heat Pump Controller for hot water (heating building)
    my_heatpump_controller_sh_config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.get_default_space_heating_controller_config(
        heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type
    )
    my_heatpump_controller_sh_config.mode = hp_controller_mode

    my_heatpump_controller_sh = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
        config=my_heatpump_controller_sh_config, my_simulation_parameters=my_simulation_parameters
    )

    my_heatpump_controller_dhw_config = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHWConfig.get_default_dhw_controller_config()
    )

    # Build Heat Pump Controller for dhw
    my_heatpump_controller_dhw = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHW(
        config=my_heatpump_controller_dhw_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Heat Pump
    my_heatpump_config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.get_scaled_advanced_hp_lib(
        heating_load_of_building_in_watt=Quantity(my_building_information.max_thermal_building_demand_in_watt, Watt),
        heating_reference_temperature_in_celsius=Quantity(heating_reference_temperature_in_celsius, Celsius),
    )
    my_heatpump_config.with_domestic_hot_water_preparation = True

    my_heatpump = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib(
        config=my_heatpump_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Verknüpfung mit Luft als Umgebungswärmeqzuelle
    if my_heatpump.parameters["Group"].iloc[0] == 1.0 or my_heatpump.parameters["Group"].iloc[0] == 4.0:
        my_heatpump.connect_input(
            my_heatpump.TemperatureInputPrimary,
            my_weather.component_name,
            my_weather.DailyAverageOutsideTemperatures,
        )
    else:
        raise KeyError(
            "Wasser oder Sole als primäres Wärmeträgermedium muss über extra Wärmenetz-Modell noch bereitgestellt werden"
        )
    # Add to simulator
    my_sim.add_component(my_heatpump, connect_automatically=True)

    # Heat Water Storage
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
        max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
        temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
        sizing_option=simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP,
    )

    my_simple_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_simple_water_storage, connect_automatically=True)

    # Heat Distribution System
    my_heat_distribution_system_config = (
        heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
            water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
            absolute_conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
            heating_system=my_hds_controller_information.hds_controller_config.heating_system,
        )
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_heat_distribution_system, connect_automatically=True)

    # Solar thermal for DHW
    my_solar_thermal_system_config = solar_thermal_system.SolarThermalSystemConfig.get_default_solar_thermal_system(
        area_m2=4 * number_of_apartments,  # 4 m2 per apartment
    )
    my_solar_thermal_system = solar_thermal_system.SolarThermalSystem(
        config=my_solar_thermal_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_solar_thermal_system, connect_automatically=True)

    # Solar thermal for DHW - Controller
    my_solar_thermal_system_controller_config = (
        solar_thermal_system.SolarThermalSystemControllerConfig.get_solar_thermal_system_controller_config()
    )

    my_solar_thermal_system_controller = solar_thermal_system.SolarThermalSystemController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_solar_thermal_system_controller_config,
    )
    my_sim.add_component(my_solar_thermal_system_controller, connect_automatically=True)

    # DHW Storage (needs manual connection to solar thermal and heatpump)
    my_dhw_storage_config = simple_water_storage.SimpleDHWStorageConfig.get_scaled_dhw_storage(
        number_of_apartments=number_of_apartments
    )

    my_dhw_storage = simple_water_storage.SimpleDHWStorage(
        my_simulation_parameters=my_simulation_parameters, config=my_dhw_storage_config
    )

    my_dhw_storage.connect_input(
        my_dhw_storage.WaterConsumption,
        my_occupancy.component_name,
        my_occupancy.WaterConsumption,
    )

    # Connect solarthermal as primary heat generator
    my_dhw_storage.connect_input(
        my_dhw_storage.WaterTemperatureFromHeatGenerator,
        my_solar_thermal_system.component_name,
        my_solar_thermal_system.WaterTemperatureOutput,
    )
    my_dhw_storage.connect_input(
        my_dhw_storage.WaterMassFlowRateFromHeatGenerator,
        my_solar_thermal_system.component_name,
        my_solar_thermal_system.WaterMassFlowOutput,
    )

    # Connect gas as secondary heat generator
    my_dhw_storage.connect_input(
        my_dhw_storage.WaterTemperatureFromSecondaryHeatGenerator,
        my_heatpump.component_name,
        my_heatpump.TemperatureOutputDHW,
    )
    my_dhw_storage.connect_input(
        my_dhw_storage.WaterMassFlowRateFromSecondaryHeatGenerator,
        my_heatpump.component_name,
        my_heatpump.MassFlowOutputDHW,
    )
    my_sim.add_component(my_dhw_storage)

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )

    # use ems and battery only when PV is used
    if share_of_maximum_pv_potential != 0 and energy_system_config_.use_battery_and_ems:

        # Build EMS
        my_electricity_controller_config = controller_l2_energy_management_system.EMSConfig.get_default_config_ems()

        my_electricity_controller = controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
            my_simulation_parameters=my_simulation_parameters,
            config=my_electricity_controller_config,
        )

        # Build Battery
        my_advanced_battery_config = advanced_battery_bslib.BatteryConfig.get_scaled_battery(
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

    # when no PV is used, connect electricty meter automatically
    else:
        my_sim.add_component(my_electricity_meter, connect_automatically=True)

    # Connect Heat Pump
    my_heatpump_controller_sh.connect_only_predefined_connections(
        my_heat_distribution_controller, my_weather, my_simple_water_storage
    )
    my_heatpump_controller_dhw.connect_only_predefined_connections(my_dhw_storage)
    my_sim.add_component(my_heatpump_controller_sh)
    my_sim.add_component(my_heatpump_controller_dhw)

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
        key=SingletonDictKeyEnum.RESULT_SCENARIO_NAME,
        entry=f"{scenario_hash_string}",
    )

    if my_simulation_parameters.result_directory == "":

        ResultPathProviderSingleton().set_important_result_path_information(
            module_directory=my_sim.module_directory,  # "/storage_cluster/projects/2024-k-rieck-hisim-mass-simulations/analysis_austria_for_kristina_20_11_2024_2",
            model_name=my_sim.module_filename,
            further_result_folder_description=os.path.join(
                *[
                    further_result_folder_description,
                ]
            ),
            variant_name="_",
            scenario_hash_string=scenario_hash_string,
            sorting_option=sorting_option,
        )
