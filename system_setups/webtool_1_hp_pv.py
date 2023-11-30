"""  Household system setup with advanced heat pump for webtool. """

from dataclasses import dataclass
from os import getenv
from pathlib import Path
from typing import Any, Optional

from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    EnergyIntensityType,
    Households,
    TransportationDeviceSets,
    TravelRouteSets,
)

from hisim import utils
from hisim.components import (
    advanced_heat_pump_hplib,
    building,
    controller_l1_heatpump,
    electricity_meter,
    generic_heat_pump_modular,
    generic_hot_water_storage_modular,
    generic_pv_system,
    heat_distribution_system,
    loadprofilegenerator_utsp_connector,
    simple_hot_water_storage,
    weather,
)
from hisim.components.configuration import HouseholdWarmWaterDemandConfig
from hisim.simulator import SimulationParameters
from hisim.system_setup_configuration import SystemSetupConfigBase
from system_setups.modular_example import cleanup_old_lpg_requests

__authors__ = ["Kevin Knosala", "Markus Blasberg"]
__copyright__ = "Copyright 2023, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Kevin Knosala"
__status__ = "development"


@dataclass
class WebtoolHPPVConfig(SystemSetupConfigBase):

    """Configuration with advanced heat pump and PV."""

    building_type: str
    number_of_apartments: int
    occupancy_config: loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig
    building_config: building.BuildingConfig
    hds_controller_config: heat_distribution_system.HeatDistributionControllerConfig
    hds_config: heat_distribution_system.HeatDistributionConfig
    hp_controller_config: advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config
    hp_config: advanced_heat_pump_hplib.HeatPumpHplibConfig
    pv_config: generic_pv_system.PVSystemConfig
    simple_hot_water_storage_config: simple_hot_water_storage.SimpleHotWaterStorageConfig
    dhw_heatpump_config: generic_heat_pump_modular.HeatPumpConfig
    dhw_heatpump_controller_config: controller_l1_heatpump.L1HeatPumpConfig
    dhw_storage_config: generic_hot_water_storage_modular.StorageConfig
    electricity_meter_config: electricity_meter.ElectricityMeterConfig

    @classmethod
    def get_default(cls) -> "WebtoolHPPVConfig":
        """Get default WebtoolAdvancedHPPVConfig."""
        building_config = building.BuildingConfig.get_default_german_single_family_home()
        household_config = cls.get_scaled_default(building_config)
        return household_config

    @classmethod
    def get_scaled_default(cls, building_config: building.BuildingConfig) -> "WebtoolHPPVConfig":
        """Get scaled default WebtoolAdvancedHPPVConfig."""

        # TODO: Check if the adjustments to the temperatures can be replaced by default values.
        heating_reference_temperature_in_celsius: float = -7
        set_heating_threshold_outside_temperature_in_celsius: float = 16.0
        my_building_information = building.BuildingInformation(config=building_config)
        pv_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(rooftop_area_in_m2=my_building_information.scaled_rooftop_area_in_m2)
        household_config = WebtoolHPPVConfig(
            building_type="blub",
            number_of_apartments=int(my_building_information.number_of_apartments),
            occupancy_config=loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
                url=getenv("UTSP_URL", ""),
                api_key=getenv("UTSP_API_KEY", ""),
                household=Households.CHR01_Couple_both_at_Work,
                energy_intensity=EnergyIntensityType.EnergySaving,
                result_dir_path=utils.HISIMPATH["results"],
                travel_route_set=TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance,
                transportation_device_set=TransportationDeviceSets.Bus_and_one_30_km_h_Car,
                charging_station_set=ChargingStationSets.Charging_At_Home_with_11_kW,
                name="UTSPConnector",
                consumption=0.0,
                profile_with_washing_machine_and_dishwasher=True,
                predictive_control=False,
            ),
            pv_config=pv_config,
            building_config=building_config,
            hds_controller_config=(heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config()),
            hds_config=(
                heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
                    heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt
                )
            ),
            hp_controller_config=advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config.get_default_generic_heat_pump_controller_config(),
            hp_config=advanced_heat_pump_hplib.HeatPumpHplibConfig.get_scaled_advanced_hp_lib(
                heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt
            ),
            simple_hot_water_storage_config=simple_hot_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
                heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt
            ),
            dhw_heatpump_config=generic_heat_pump_modular.HeatPumpConfig.get_scaled_waterheating_to_number_of_apartments(
                number_of_apartments=int(my_building_information.number_of_apartments)
            ),
            dhw_heatpump_controller_config=controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
                name="DHWHeatpumpController"
            ),
            dhw_storage_config=generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(
                number_of_apartments=int(my_building_information.number_of_apartments)
            ),
            electricity_meter_config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
        )

        # adjust HeatPump
        household_config.hp_config.group_id = 1  # use modulating heatpump as default
        household_config.hp_controller_config.mode = 2  # use heating and cooling as default
        household_config.hp_config.minimum_idle_time_in_seconds = 900  # default value leads to switching on-off very often
        household_config.hp_config.minimum_running_time_in_seconds = 900  # default value leads to switching on-off very often
        # set same heating threshold
        household_config.hds_controller_config.set_heating_threshold_outside_temperature_in_celsius = (
            set_heating_threshold_outside_temperature_in_celsius
        )
        household_config.hp_controller_config.set_heating_threshold_outside_temperature_in_celsius = (
            set_heating_threshold_outside_temperature_in_celsius
        )
        # set same heating reference temperature
        household_config.hds_controller_config.heating_reference_temperature_in_celsius = heating_reference_temperature_in_celsius
        household_config.hp_config.heating_reference_temperature_in_celsius = heating_reference_temperature_in_celsius
        household_config.building_config.heating_reference_temperature_in_celsius = heating_reference_temperature_in_celsius
        household_config.hp_config.flow_temperature_in_celsius = 21  # Todo: check value
        return household_config


def setup_function(
    my_sim: Any,
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """System setup with advanced hp and diesel car.

    This setup function emulates a household with some basic components. Here the residents have their
    electricity and heating needs covered by a the advanced heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Building
        - Electricity Meter
        - Advanced Heat Pump HPlib
        - Advanced Heat Pump HPlib Controller
        - Heat Distribution System
        - Heat Distribution System Controller
        - Simple Hot Water Storage
        - DHW (Heatpump, Heatpumpcontroller, Storage)
        - PV
    """

    if Path(utils.HISIMPATH["utsp_results"]).exists():
        cleanup_old_lpg_requests()
    if my_sim.my_module_config_path:
        my_config = WebtoolHPPVConfig.load_from_json(my_sim.my_module_config_path)
    else:
        my_config = WebtoolHPPVConfig.get_default()
    """
    Set Simulation Parameters
    """
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.january_only_with_all_options(year=2021, seconds_per_timestep=60)
        from hisim.postprocessingoptions import PostProcessingOptions

        my_simulation_parameters.post_processing_options = [
            PostProcessingOptions.COMPUTE_CAPEX,
            PostProcessingOptions.COMPUTE_OPEX,
            PostProcessingOptions.COMPUTE_AND_WRITE_KPIS_TO_REPORT,
            PostProcessingOptions.MAKE_RESULT_JSON_WITH_KPI_FOR_WEBTOOL,
        ]
    my_sim.set_simulation_parameters(my_simulation_parameters)
    """
    Build heat Distribution System Controller
    """
    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        config=my_config.hds_controller_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    """
    Build Occupancy
    """
    my_occupancy_config = my_config.occupancy_config
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters)
    """
    Build Weather
    """
    my_weather = weather.Weather(
        config=weather.WeatherConfig.get_default(weather.LocationEnum.AACHEN),
        my_simulation_parameters=my_simulation_parameters,
    )
    """
    Build PV
    """
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_config.pv_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    """
    Build Building
    """
    my_building = building.Building(
        config=my_config.building_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    """
    Build Heat Distribution System
    """
    my_heat_distribution = heat_distribution_system.HeatDistribution(my_simulation_parameters=my_simulation_parameters, config=my_config.hds_config)
    """
    # Build Heat Pump Controller
    """
    my_heat_pump_controller_config = my_config.hp_controller_config
    my_heat_pump_controller_config.name = "HeatPumpHplibController"
    my_heat_pump_controller = advanced_heat_pump_hplib.HeatPumpHplibController(
        config=my_heat_pump_controller_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    """
    Build Heat Pump
    """
    my_heat_pump_config = my_config.hp_config
    my_heat_pump_config.name = "HeatPumpHPLib"
    my_heat_pump = advanced_heat_pump_hplib.HeatPumpHplib(
        config=my_heat_pump_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    """
    Build Heat Water Storage
    """
    my_simple_hot_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_config.simple_hot_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    """
    Build DHW
    """
    my_dhw_heatpump_config = my_config.dhw_heatpump_config
    my_dhw_heatpump_config.power_th = (
        my_occupancy.max_hot_water_demand
        * (4180 / 3600)
        * 0.5
        * (3600 / my_simulation_parameters.seconds_per_timestep)
        * (HouseholdWarmWaterDemandConfig.ww_temperature_demand - HouseholdWarmWaterDemandConfig.freshwater_temperature)
    )
    my_dhw_heatpump_controller_config = my_config.dhw_heatpump_controller_config
    my_dhw_storage_config = my_config.dhw_storage_config
    my_dhw_storage_config.name = "DHWStorage"
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
    """
    Build Electricity Meter
    """
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=my_config.electricity_meter_config,
    )
    """
    Add Components to Simulation Parameters
    """
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)
    my_sim.add_component(my_building, connect_automatically=True)
    my_sim.add_component(my_heat_pump, connect_automatically=True)
    my_sim.add_component(my_heat_pump_controller, connect_automatically=True)
    my_sim.add_component(my_heat_distribution, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)
    my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_heatpump_controller, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_heatpump, connect_automatically=True)
    my_sim.add_component(my_electricity_meter, connect_automatically=True)
