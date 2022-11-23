"""  Basic household example. Shows how to set up a standard system. """

from typing import Optional, Any
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import generic_heat_pump
from hisim.components import sumbuilder
from hisim import log
from hisim import utils
from dataclasses_json import dataclass_json
from dataclasses import dataclass
import os
from pathlib import Path
from hisim import loadtypes
__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"
from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    Households,
    HouseTypes,
    LoadTypes,
    TransportationDeviceSets,
    TravelRouteSets,
)
from utspclient.helpers.lpgpythonbindings import CalcOption, JsonReference

@dataclass_json
@dataclass
class HouseholdPVConfig:
    PVSize: float
    BuildingType: str
    HouseholdType: JsonReference
    LPGUrl: str
    ResultPath: str
    TravelRouteSet: JsonReference
    simulation_parameters: SimulationParameters
    APIKey: str
    transportation_device_set: JsonReference
    charging_station_set: JsonReference
    PV_azimuth: float
    Tilt: float
    PV_Power: float
    @classmethod
    def get_default (cls):
        return HouseholdPVConfig(PVSize=5,
                                 BuildingType="blub",
                                 HouseholdType= Households.CHR01_Couple_both_at_Work,
                                 LPGUrl="http://134.94.131.167:443/api/v1/profilerequest",
                            APIKey="OrjpZY93BcNWw8lKaMp0BEchbCc",
                                 simulation_parameters=SimulationParameters.one_day_only(2022),
                                 ResultPath="mypath", TravelRouteSet=TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance,
                                 transportation_device_set=TransportationDeviceSets.Bus_and_one_30_km_h_Car,
                                 charging_station_set=ChargingStationSets.Charging_At_Home_with_11_kW,
                                PV_azimuth=180,
                                 Tilt=30,
                                 PV_Power=10000)



def household_pv_hp(my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None) -> None:  # noqa: too-many-statements
    """ Basic household example.

    This setup function emulates a household with some basic components. Here the residents have their
    electricity and heating needs covered by the photovoltaic system and the heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump
    """

    config_filename = "pv_hp_config.json"

    my_config: HouseholdPVConfig
    if Path(config_filename).is_file():
        with open(config_filename, encoding='utf8') as system_config_file:
            my_config = HouseholdPVConfig.from_json(system_config_file.read())  # type: ignore
        log.information(f"Read system config from {config_filename}")
    else:
        my_config = HouseholdPVConfig.get_default()

    # System Parameters #

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60

    # Set weather
    location = "Aachen"

    # Set photovoltaic system
    time = 2019
    power = my_config.PV_Power
    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrate_inverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    name = 'PVSystem'
    azimuth = my_config.PV_azimuth
    tilt = my_config.Tilt
    source_weight = -1

    # Set occupancy
    occupancy_profile = "CH01"

    # Set building
    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"
    building_class = "medium"
    initial_temperature = 23
    heating_reference_temperature = -14

    # Set heat pump controller
    t_air_heating = 16.0
    t_air_cooling = 24.0
    offset = 0.5
    hp_mode = 2

    # Set heat pump
    hp_manufacturer = "Viessmann Werke GmbH & Co KG"
    hp_name = "Vitocal 300-A AWO-AC 301.B07"
    hp_min_operation_time = 60
    hp_min_idle_time = 15

    # Build Components #

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(year=year,
                                                                              seconds_per_timestep=seconds_per_timestep)
    my_sim.set_simulation_parameters(my_simulation_parameters)
    # Build occupancy
    lpgurl = "http://"
    api_key = "asdf"
    result_path = os.path.join(utils.get_input_directory(), "lpg_profiles")
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(url=my_config.LPGUrl,
                                                                                     api_key=my_config.APIKey,
                                                                                     household=my_config.HouseholdType,
                                                                                     result_path=my_config.ResultPath,
                                                                                     travel_route_set=my_config.TravelRouteSet,
                                                                                     transportation_device_set=my_config.transportation_device_set,
                                                                                     charging_station_set=my_config.charging_station_set
                                                                                     )

    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.Aachen)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_weather)

    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig(time=time, location=location, power=power, load_module_data=load_module_data,
                                                                     module_name=module_name, integrate_inverter=integrate_inverter, tilt=tilt,
                                                                     azimuth=azimuth, inverter_name=inverter_name, source_weight=source_weight,
                                                                     name=name)
    my_photovoltaic_system = generic_pv_system.PVSystem(config=my_photovoltaic_system_config, my_simulation_parameters=my_simulation_parameters)


    my_photovoltaic_system.connect_only_predefined_connections(my_weather)
    my_sim.add_component(my_photovoltaic_system)

    # electricity grid
    my_base_electricity_load_profile = sumbuilder.ElectricityGrid(name="BaseLoad", grid=[my_occupancy, "Subtract", my_photovoltaic_system],
                                                                  my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_base_electricity_load_profile)

    my_building_config = building.BuildingConfig(building_code=building_code, building_heat_capacity_class=building_class, initial_internal_temperature_in_celsius=initial_temperature,
                                                 heating_reference_temperature_in_celsius=heating_reference_temperature, name="Building1")
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    my_building.connect_only_predefined_connections(my_weather)
    my_building.connect_only_predefined_connections(my_occupancy)
    my_sim.add_component(my_building)

    my_heat_pump_controller = generic_heat_pump.HeatPumpController(t_air_heating=t_air_heating, t_air_cooling=t_air_cooling, offset=offset,
                                                                   mode=hp_mode, my_simulation_parameters=my_simulation_parameters)
    my_heat_pump_controller.connect_input(my_heat_pump_controller.TemperatureMean, my_building.component_name, my_building.TemperatureMean)
    my_heat_pump_controller.connect_input(my_heat_pump_controller.ElectricityInput, my_base_electricity_load_profile.component_name,
                                          my_base_electricity_load_profile.ElectricityOutput)
    my_sim.add_component(my_heat_pump_controller)

    my_heat_pump = generic_heat_pump.GenericHeatPump(manufacturer=hp_manufacturer, name=hp_name, min_operation_time=hp_min_operation_time,
                                                     min_idle_time=hp_min_idle_time, my_simulation_parameters=my_simulation_parameters)
    my_heat_pump.connect_input(my_heat_pump.State, my_heat_pump_controller.component_name, my_heat_pump_controller.State)
    my_heat_pump.connect_input(my_heat_pump.TemperatureOutside, my_weather.component_name, my_weather.TemperatureOutside)

    my_sim.add_component(my_heat_pump)

    my_building.connect_input(my_building.ThermalEnergyDelivered, my_heat_pump.component_name, my_heat_pump.ThermalEnergyDelivered)
