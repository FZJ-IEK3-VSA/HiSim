"""  Household example with gas heater. """
# clean
from typing import Optional, Any
from pathlib import Path

# from dataclasses import dataclass
# from dataclasses_json import dataclass_json
# from utspclient.helpers.lpgdata import (
#     ChargingStationSets,
#     Households,
#     TransportationDeviceSets,
#     TravelRouteSets,
# )
# from utspclient.helpers.lpgpythonbindings import JsonReference
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_gas_heater
from hisim.components import controller_l1_heat_old
from hisim.components import generic_heat_water_storage
from hisim.components import building
from hisim import log
from examples.household_with_heatpump_and_pv import HouseholdPVConfig

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


# @dataclass_json
# @dataclass
# class HouseholdPVConfig:

#     """Configuration for HouseholdPV."""

#     pv_size: float
#     building_type: str
#     household_type: JsonReference
#     lpg_url: str
#     result_path: str
#     travel_route_set: JsonReference
#     simulation_parameters: SimulationParameters
#     api_key: str
#     transportation_device_set: JsonReference
#     charging_station_set: JsonReference
#     pv_azimuth: float
#     tilt: float
#     pv_power: float
#     total_base_area_in_m2: float

#     @classmethod
#     def get_default(cls):
#         """Get default HouseholdPVConfig."""

#         return HouseholdPVConfig(
#             pv_size=5,
#             building_type="blub",
#             household_type=Households.CHR01_Couple_both_at_Work,
#             lpg_url="http://134.94.131.167:443/api/v1/profilerequest",
#             api_key="OrjpZY93BcNWw8lKaMp0BEchbCc",
#             simulation_parameters=SimulationParameters.one_day_only(2022),
#             result_path="mypath",
#             travel_route_set=TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance,
#             transportation_device_set=TransportationDeviceSets.Bus_and_one_30_km_h_Car,
#             charging_station_set=ChargingStationSets.Charging_At_Home_with_11_kW,
#             pv_azimuth=180,
#             tilt=30,
#             pv_power=10000,
#             total_base_area_in_m2=121.2,
#         )


def household_gas_heater(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Basic household example.

    This setup function emulates a household with some basic components. Here the residents have their
    electricity and heating needs covered by a generic gas heater.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Building
        - Gas Heater
        - Heat Water Storage
        - Heat Controller
    """

    config_filename = "pv_hp_config.json"

    my_config: HouseholdPVConfig
    if Path(config_filename).is_file():
        with open(config_filename, encoding="utf8") as system_config_file:
            my_config = HouseholdPVConfig.from_json(system_config_file.read())  # type: ignore
        log.information(f"Read system config from {config_filename}")
    else:
        my_config = HouseholdPVConfig.get_default()

    # =================================================================================================================================
    # Set System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # Set Building
    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"
    building_class = "medium"
    initial_temperature = 23
    heating_reference_temperature = -14
    absolute_conditioned_floor_area = None

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
        url=my_config.lpg_url,
        api_key=my_config.api_key,
        household=my_config.household_type,
        result_path=my_config.result_path,
        travel_route_set=my_config.travel_route_set,
        transportation_device_set=my_config.transportation_device_set,
        charging_station_set=my_config.charging_station_set,
    )
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.Aachen
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Building
    my_building_config = building.BuildingConfig(
        building_code=building_code,
        building_heat_capacity_class=building_class,
        initial_internal_temperature_in_celsius=initial_temperature,
        heating_reference_temperature_in_celsius=heating_reference_temperature,
        name="Building1",
        total_base_area_in_m2=my_config.total_base_area_in_m2,
        absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area,
    )
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Gasheater
    my_gasheater = generic_gas_heater.GasHeater(
        config=generic_gas_heater.GasHeater.get_default_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Water Storage
    my_heat_water_storage = generic_heat_water_storage.HeatStorage(
        config=generic_heat_water_storage.HeatStorage.get_default_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Controller
    my_controller_heat = controller_l1_heat_old.ControllerHeat(
        config=controller_l1_heat_old.ControllerHeat.get_default_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(
        my_building.ThermalEnergyDelivered,
        my_heat_water_storage.component_name,
        my_heat_water_storage.RealHeatForBuilding,
    )

    my_gasheater.connect_input(
        my_gasheater.ControlSignal,
        my_controller_heat.component_name,
        my_controller_heat.ControlSignalGasHeater,
    )
    my_gasheater.connect_input(
        my_gasheater.MassflowInputTemperature,
        my_heat_water_storage.component_name,
        my_heat_water_storage.WaterOutputStorageforHeaters,
    )

    my_heat_water_storage.connect_input(
        my_heat_water_storage.ThermalInputPower1,
        my_gasheater.component_name,
        my_gasheater.ThermalOutputPower,
    )
    my_heat_water_storage.connect_input(
        my_heat_water_storage.ControlSignalChooseStorage,
        my_controller_heat.component_name,
        my_controller_heat.ControlSignalChooseStorage,
    )

    my_controller_heat.connect_input(
        my_controller_heat.StorageTemperatureHeatingWater,
        my_heat_water_storage.component_name,
        my_heat_water_storage.WaterOutputTemperatureHeatingWater,
    )
    my_controller_heat.connect_input(
        my_controller_heat.ResidenceTemperature,
        my_building.component_name,
        my_building.TemperatureMean,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_building)
    my_sim.add_component(my_gasheater)

    my_sim.add_component(my_heat_water_storage)
    my_sim.add_component(my_controller_heat)
