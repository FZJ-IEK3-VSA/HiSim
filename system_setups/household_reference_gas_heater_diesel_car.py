"""  Reference Household system setup with gas heater and diesel car. """

# clean

from dataclasses import dataclass
from os import listdir
from typing import List, Optional, Any
from pathlib import Path
from dataclasses_json import dataclass_json
from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    Households,
    TransportationDeviceSets,
    TravelRouteSets,
    EnergyIntensityType,
)
from hisim.system_setup_configuration import SystemSetupConfigBase
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_gas_heater
from hisim.components import controller_l1_generic_gas_heater
from hisim.components import heat_distribution_system
from hisim.components import building
from hisim.components import simple_hot_water_storage
from hisim.components import generic_car
from hisim.components import generic_heat_pump_modular
from hisim.components import controller_l1_heatpump
from hisim.components import generic_hot_water_storage_modular
from hisim.components import electricity_meter
from hisim import utils

from system_setups.modular_example import cleanup_old_lpg_requests

__authors__ = "Markus Blasberg"
__copyright__ = "Copyright 2023, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Markus Blasberg"
__status__ = "development"


@dataclass_json
@dataclass
class ReferenceHouseholdConfig(SystemSetupConfigBase):

    """Configuration for ReferenceHosuehold."""

    building_type: str
    number_of_apartments: int
    # simulation_parameters: SimulationParameters
    # total_base_area_in_m2: float
    occupancy_config: loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig
    building_config: building.BuildingConfig
    hds_controller_config: heat_distribution_system.HeatDistributionControllerConfig
    hds_config: heat_distribution_system.HeatDistributionConfig
    gasheater_controller_config: controller_l1_generic_gas_heater.GenericGasHeaterControllerL1Config
    gasheater_config: generic_gas_heater.GenericGasHeaterConfig
    simple_hot_water_storage_config: simple_hot_water_storage.SimpleHotWaterStorageConfig
    dhw_heatpump_config: generic_heat_pump_modular.HeatPumpConfig
    dhw_heatpump_controller_config: controller_l1_heatpump.L1HeatPumpConfig
    dhw_storage_config: generic_hot_water_storage_modular.StorageConfig
    car_config: generic_car.CarConfig
    electricity_meter_config: electricity_meter.ElectricityMeterConfig

    @classmethod
    def get_default(cls):
        """Get default HouseholdPVConfig."""

        heating_reference_temperature_in_celsius: float = -7
        set_heating_threshold_outside_temperature_in_celsius: float = 16.0
        building_config = building.BuildingConfig.get_default_german_single_family_home(
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius
        )
        my_building_information = building.BuildingInformation(config=building_config)
        hds_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
            set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
            heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
        )
        my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
            config=hds_controller_config
        )

        household_config = ReferenceHouseholdConfig(
            building_type="blub",
            number_of_apartments=my_building_information.number_of_apartments,
            # simulation_parameters=SimulationParameters.one_day_only(2022),
            # total_base_area_in_m2=121.2,
            occupancy_config=loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
                data_acquisition_mode=loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_UTSP,
                household=Households.CHR01_Couple_both_at_Work,
                energy_intensity=EnergyIntensityType.EnergySaving,
                result_dir_path=utils.HISIMPATH["utsp_results"],
                travel_route_set=TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance,
                transportation_device_set=TransportationDeviceSets.Bus_and_one_30_km_h_Car,
                charging_station_set=ChargingStationSets.Charging_At_Home_with_11_kW,
                name="UTSPConnector",
                consumption=0.0,
                profile_with_washing_machine_and_dishwasher=True,
                predictive_control=False,
                predictive=False,
            ),
            building_config=building_config,
            hds_controller_config=hds_controller_config,
            hds_config=(
                heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
                    temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
                    water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
                )
            ),
            gasheater_controller_config=(
                controller_l1_generic_gas_heater.GenericGasHeaterControllerL1Config.get_scaled_generic_gas_heater_controller_config(
                    heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt
                )
            ),
            gasheater_config=generic_gas_heater.GenericGasHeaterConfig.get_scaled_gasheater_config(
                heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt
            ),
            simple_hot_water_storage_config=(
                simple_hot_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
                    max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
                    heating_system_name="GasHeater",
                    water_mass_flow_rate_from_hds_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
                )
            ),
            dhw_heatpump_config=(
                generic_heat_pump_modular.HeatPumpConfig.get_scaled_waterheating_to_number_of_apartments(
                    number_of_apartments=my_building_information.number_of_apartments
                )
            ),
            dhw_heatpump_controller_config=controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
                name="DHWHeatpumpController"
            ),
            dhw_storage_config=(
                generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(
                    number_of_apartments=my_building_information.number_of_apartments
                )
            ),
            car_config=generic_car.CarConfig.get_default_diesel_config(),
            electricity_meter_config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
        )

        # set same heating threshold
        household_config.hds_controller_config.set_heating_threshold_outside_temperature_in_celsius = (
            set_heating_threshold_outside_temperature_in_celsius
        )
        household_config.gasheater_controller_config.set_heating_threshold_outside_temperature_in_celsius = (
            set_heating_threshold_outside_temperature_in_celsius
        )

        # set dhw storage volume, because default(volume = 230) leads to an error
        household_config.dhw_storage_config.volume = 250

        return household_config


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Reference system setup.

    This setup function emulates a household with some basic components. Here the residents have their
    electricity and heating needs covered by a generic gas heater.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Building
        - Electricity Meter
        - Gas Heater
        - Gas Heater Controller
        - Heat Distribution System
        - Heat Distribution System Controller
        - Simple Hot Water Storage

        - DHW (Heatpump, Heatpumpcontroller, Storage; copied from modular_example)
        - Car (Diesel)
    """

    # cleanup old lpg requests, mandatory to change number of cars
    if Path(utils.HISIMPATH["utsp_results"]).exists():
        cleanup_old_lpg_requests()
    else:
        Path(utils.HISIMPATH["utsp_results"]).mkdir(parents=False, exist_ok=False)

    # my_config = utils.create_configuration(my_sim, ReferenceHouseholdConfig)

    # Todo: save file leads to use of file in next run. File was just produced to check how it looks like
    if my_sim.my_module_config_path:
        my_config = ReferenceHouseholdConfig.load_from_json(my_sim.my_module_config_path)
    else:
        my_config = ReferenceHouseholdConfig.get_default()
    # =================================================================================
    # Set System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # =================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build heat Distribution System Controller
    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        config=my_config.hds_controller_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Occupancy
    my_occupancy_config = my_config.occupancy_config
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather = weather.Weather(
        config=weather.WeatherConfig.get_default(weather.LocationEnum.AACHEN),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Building
    my_building = building.Building(
        config=my_config.building_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Gas Heater Controller
    my_gasheater_controller = controller_l1_generic_gas_heater.GenericGasHeaterControllerL1(
        my_simulation_parameters=my_simulation_parameters,
        config=my_config.gasheater_controller_config,
    )

    # Build Gasheater
    my_gasheater = generic_gas_heater.GasHeater(
        config=my_config.gasheater_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Distribution System
    my_heat_distribution = heat_distribution_system.HeatDistribution(
        my_simulation_parameters=my_simulation_parameters, config=my_config.hds_config
    )

    # Build Heat Water Storage
    my_simple_hot_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_config.simple_hot_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build DHW
    my_dhw_heatpump_config = my_config.dhw_heatpump_config
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

    # Build Diesel-Car(s)
    # get names of all available cars
    filepaths = listdir(utils.HISIMPATH["utsp_results"])
    filepaths_location = [elem for elem in filepaths if "CarLocation." in elem]
    names = [elem.partition(",")[0].partition(".")[2] for elem in filepaths_location]

    my_car_config = my_config.car_config
    my_car_config.name = "DieselCar"

    # create all cars
    my_cars: List[generic_car.Car] = []
    for car in names:
        my_car_config.name = car
        my_cars.append(
            generic_car.Car(
                my_simulation_parameters=my_simulation_parameters,
                config=my_car_config,
                occupancy_config=my_occupancy_config,
            )
        )

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=my_config.electricity_meter_config,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_building, connect_automatically=True)
    my_sim.add_component(my_gasheater, connect_automatically=True)
    my_sim.add_component(my_gasheater_controller, connect_automatically=True)
    my_sim.add_component(my_heat_distribution, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)
    my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_heatpump_controller, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_heatpump, connect_automatically=True)
    my_sim.add_component(my_electricity_meter, connect_automatically=True)
    for my_car in my_cars:
        my_sim.add_component(my_car)
