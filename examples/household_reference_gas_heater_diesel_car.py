"""  Reference Household example with gas heater and diesel car. """
# Todo: clean code

from typing import List, Optional, Any
from os import listdir
from pathlib import Path
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    Households,
    TransportationDeviceSets,
    TravelRouteSets,
)
from utspclient.helpers.lpgpythonbindings import JsonReference

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
from hisim.components import sumbuilder
from hisim.components.configuration import HouseholdWarmWaterDemandConfig
from hisim import utils
from hisim import log

__authors__ = "Markus Blasberg"
__copyright__ = "Copyright 2023, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Markus Blasberg"
__status__ = "development"


# Todo: adopt Config-Class according to needs
@dataclass_json
@dataclass
class ReferenceHouseholdConfig:

    """Configuration for ReferenceHosuehold."""

    # pv_size: float
    building_type: str
    household_type: JsonReference
    lpg_url: str
    result_path: str
    travel_route_set: JsonReference
    simulation_parameters: SimulationParameters
    api_key: str
    transportation_device_set: JsonReference
    charging_station_set: JsonReference
    # pv_azimuth: float
    # tilt: float
    # pv_power: float
    total_base_area_in_m2: float

    @classmethod
    def get_default(cls):
        """Get default HouseholdPVConfig."""

        return ReferenceHouseholdConfig(
            # pv_size=5,
            building_type="blub",
            household_type=Households.CHR01_Couple_both_at_Work,
            lpg_url="http://134.94.131.167:443/api/v1/profilerequest",
            api_key="OrjpZY93BcNWw8lKaMp0BEchbCc",
            simulation_parameters=SimulationParameters.one_day_only(2022),
            result_path="mypath",
            travel_route_set=TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance,
            transportation_device_set=TransportationDeviceSets.Bus_and_one_30_km_h_Car,
            charging_station_set=ChargingStationSets.Charging_At_Home_with_11_kW,
            # pv_azimuth=180,
            # tilt=30,
            # pv_power=10000,
            total_base_area_in_m2=121.2,
        )


def household_reference_gas_heater_diesel_car(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Reference example

    This setup function emulates a household with some basic components. Here the residents have their
    electricity and heating needs covered by a generic gas heater.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Building
        - Electricity Base Load
        - Gas Heater
        - Gas Heater Controller
        - Heat Distribution System
        - Heat Distribution System Controller
        - Simple Hot Water Storage

        - DHW (Heatpump, Heatpumpcontroller, Storage; copied from modular_example)
        - Car (Diesel)
    """
    # Todo: change config with systemConfigBase.json for all components similar to modular_example
    config_filename = "reference_household_config.json"

    my_config: ReferenceHouseholdConfig
    if Path(config_filename).is_file():
        with open(config_filename, encoding="utf8") as system_config_file:
            my_config = ReferenceHouseholdConfig.from_json(system_config_file.read())  # type: ignore
        log.information(f"Read system config from {config_filename}")
    else:
        my_config = ReferenceHouseholdConfig.get_default()

    # =================================================================================================================================
    # Set System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # Set Occupancy
    url = my_config.lpg_url
    api_key = my_config.api_key
    household = my_config.household_type
    result_path = my_config.result_path
    travel_route_set = my_config.travel_route_set
    transportation_device_set = my_config.transportation_device_set
    charging_station_set = my_config.charging_station_set

    # =================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
        url=url,
        api_key=api_key,
        household=household,
        result_path=result_path,
        travel_route_set=travel_route_set,
        transportation_device_set=transportation_device_set,
        charging_station_set=charging_station_set,
        name="UTSP Connector",
    )
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather = weather.Weather(
        config=weather.WeatherConfig.get_default(weather.LocationEnum.Aachen),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Building
    my_building = building.Building(
        config=building.BuildingConfig.get_default_german_single_family_home(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Gas Heater Controller
    my_gasheater_controller_config = (
        controller_l1_generic_gas_heater.GenericGasHeaterControllerL1Config.get_default_generic_gas_heater_controller_config()
    )
    my_gasheater_controller = (
        controller_l1_generic_gas_heater.GenericGasHeaterControllerL1(
            my_simulation_parameters=my_simulation_parameters,
            config=my_gasheater_controller_config,
        )
    )

    # Build Gasheater
    my_gasheater = generic_gas_heater.GasHeater(
        config=generic_gas_heater.GenericGasHeaterConfig.get_default_gasheater_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build heat Distribution System Controller
    hdscontroller_config = (
        heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config()
    )
    my_heat_distribution_controller = (
        heat_distribution_system.HeatDistributionController(
            config=hdscontroller_config,
            my_simulation_parameters=my_simulation_parameters,
        )
    )

    # Build Heat Distribution System
    hds_config = (
        heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config()
    )

    my_heat_distribution = heat_distribution_system.HeatDistribution(
        my_simulation_parameters=my_simulation_parameters, config=hds_config
    )

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = (
        simple_hot_water_storage.SimpleHotWaterStorageConfig.get_default_simplehotwaterstorage_config()
    )
    my_simple_hot_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build DHW
    dhw_heatpump_config = (
        generic_heat_pump_modular.HeatPumpConfig.get_default_config_waterheating()
    )

    dhw_heatpump_controller_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
        name="DHWHeatpump"
    )

    dhw_heatpump_config.power_th = (
        my_occupancy.max_hot_water_demand
        * (4180 / 3600)
        * 0.5
        * (3600 / my_simulation_parameters.seconds_per_timestep)
        * (
            HouseholdWarmWaterDemandConfig.ww_temperature_demand
            - HouseholdWarmWaterDemandConfig.freshwater_temperature
        )
    )

    dhw_storage_config = (
        generic_hot_water_storage_modular.StorageConfig.get_default_config_boiler()
    )
    dhw_storage_config.name = "DHWStorage"

    dhw_storage_config.compute_default_cycle(
        temperature_difference_in_kelvin=dhw_heatpump_controller_config.t_max_heating_in_celsius
        - dhw_heatpump_controller_config.t_min_heating_in_celsius
    )

    my_domnestic_hot_water_storage = generic_hot_water_storage_modular.HotWaterStorage(
        my_simulation_parameters=my_simulation_parameters, config=dhw_storage_config
    )

    my_domnestic_hot_water_heatpump_controller = (
        controller_l1_heatpump.L1HeatPumpController(
            my_simulation_parameters=my_simulation_parameters,
            config=dhw_heatpump_controller_config,
        )
    )

    my_domnestic_hot_water_heatpump = generic_heat_pump_modular.ModularHeatPump(
        config=dhw_heatpump_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Diesel-Car
    # get names of all available cars
    filepaths = listdir(utils.HISIMPATH["utsp_results"])
    filepaths_location = [elem for elem in filepaths if "CarLocation." in elem]
    names = [elem.partition(",")[0].partition(".")[2] for elem in filepaths_location]

    my_car_config = generic_car.CarConfig.get_default_diesel_config()
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
    my_car = my_cars[0]

    # Build Base Electricity Load Profile
    my_base_electricity_load_profile = sumbuilder.ElectricityGrid(
        config=sumbuilder.ElectricityGridConfig(
            name="ElectrcityGrid_BaseLoad",
            grid=[my_occupancy, "Sum", my_domnestic_hot_water_heatpump],
            signal=None,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_distribution.component_name,
        my_heat_distribution.ThermalPowerDelivered,
    )

    my_gasheater.connect_input(
        my_gasheater.ControlSignal,
        my_gasheater_controller.component_name,
        my_gasheater_controller.ControlSignalToGasHeater,
    )

    my_gasheater.connect_input(
        my_gasheater.MassflowInputTemperature,
        my_simple_hot_water_storage.component_name,
        my_simple_hot_water_storage.WaterTemperatureToHeatGenerator,
    )

    my_gasheater_controller.connect_only_predefined_connections(
        my_simple_hot_water_storage, my_weather, my_heat_distribution_controller
    )

    my_heat_distribution_controller.connect_only_predefined_connections(
        my_weather, my_building, my_simple_hot_water_storage
    )

    my_heat_distribution.connect_only_predefined_connections(
        my_heat_distribution_controller, my_building, my_simple_hot_water_storage
    )

    my_simple_hot_water_storage.connect_input(
        my_simple_hot_water_storage.WaterTemperatureFromHeatDistributionSystem,
        my_heat_distribution.component_name,
        my_heat_distribution.WaterTemperatureOutput,
    )

    my_simple_hot_water_storage.connect_input(
        my_simple_hot_water_storage.WaterTemperatureFromHeatGenerator,
        my_gasheater.component_name,
        my_gasheater.MassflowOutputTemperature,
    )

    my_simple_hot_water_storage.connect_input(
        my_simple_hot_water_storage.WaterMassFlowRateFromHeatGenerator,
        my_gasheater.component_name,
        my_gasheater.MassflowOutput,
    )

    # connect DHW
    my_domnestic_hot_water_storage.connect_only_predefined_connections(
        my_occupancy, my_domnestic_hot_water_heatpump
    )

    my_domnestic_hot_water_heatpump_controller.connect_only_predefined_connections(
        my_domnestic_hot_water_storage
    )

    my_domnestic_hot_water_heatpump.connect_only_predefined_connections(
        my_weather, my_domnestic_hot_water_heatpump_controller
    )

    # Todo: connect heat pump and electricity consumption

    # =================================================================================================================================
    # Add Components to Simulation Parameters
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_building)
    my_sim.add_component(my_gasheater)
    my_sim.add_component(my_gasheater_controller)
    my_sim.add_component(my_heat_distribution)
    my_sim.add_component(my_heat_distribution_controller)
    my_sim.add_component(my_simple_hot_water_storage)
    my_sim.add_component(my_domnestic_hot_water_storage)
    my_sim.add_component(my_domnestic_hot_water_heatpump_controller)
    my_sim.add_component(my_domnestic_hot_water_heatpump)
    my_sim.add_component(my_car)
    my_sim.add_component(my_base_electricity_load_profile)