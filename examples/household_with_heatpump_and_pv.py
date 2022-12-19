"""  Household example with PV system and heatpump. """
# clean
from typing import Optional, Any
from dataclasses import dataclass
from pathlib import Path
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
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import generic_heat_pump
from hisim.components import sumbuilder
from hisim import log

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


@dataclass_json
@dataclass
class HouseholdPVConfig:

    """Configuration for HouseholdPV."""

    pv_size: float
    building_type: str
    household_type: JsonReference
    lpg_url: str
    result_path: str
    travel_route_set: JsonReference
    simulation_parameters: SimulationParameters
    api_key: str
    transportation_device_set: JsonReference
    charging_station_set: JsonReference
    pv_azimuth: float
    tilt: float
    pv_power: float
    total_base_area_in_m2: float

    @classmethod
    def get_default(cls):
        """Get default HouseholdPVConfig."""

        return HouseholdPVConfig(
            pv_size=5,
            building_type="blub",
            household_type=Households.CHR01_Couple_both_at_Work,
            lpg_url="http://134.94.131.167:443/api/v1/profilerequest",
            api_key="OrjpZY93BcNWw8lKaMp0BEchbCc",
            simulation_parameters=SimulationParameters.one_day_only(2022),
            result_path="mypath",
            travel_route_set=TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance,
            transportation_device_set=TransportationDeviceSets.Bus_and_one_30_km_h_Car,
            charging_station_set=ChargingStationSets.Charging_At_Home_with_11_kW,
            pv_azimuth=180,
            tilt=30,
            pv_power=10000,
            total_base_area_in_m2=121.2,
        )


def household_pv_hp(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Basic household example.

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

    # Set Weather
    location = "Aachen"

    # Set Photovoltaic System
    time = 2019
    power = my_config.pv_power
    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrate_inverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    name = "PVSystem"
    azimuth = my_config.pv_azimuth
    tilt = my_config.tilt
    source_weight = -1

    # Set Building
    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"
    building_class = "medium"
    initial_temperature = 23
    heating_reference_temperature = -14
    absolute_conditioned_floor_area = None

    # Set Heat Pump Controller
    t_air_heating = 16.0
    t_air_cooling = 24.0
    offset = 0.5
    hp_mode = 2

    # Set Heat Pump
    hp_manufacturer = "Viessmann Werke GmbH & Co KG"
    hp_name = "Vitocal 300-A AWO-AC 301.B07"
    hp_min_operation_time = 60
    hp_min_idle_time = 15

    # =================================================================================================================================
    # Build Components

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build occupancy
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

    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig(
        time=time,
        location=location,
        power=power,
        load_module_data=load_module_data,
        module_name=module_name,
        integrate_inverter=integrate_inverter,
        tilt=tilt,
        azimuth=azimuth,
        inverter_name=inverter_name,
        source_weight=source_weight,
        name=name,
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Electricity Grid
    my_base_electricity_load_profile = sumbuilder.ElectricityGrid(
        name="BaseLoad",
        grid=[my_occupancy, "Subtract", my_photovoltaic_system],
        my_simulation_parameters=my_simulation_parameters,
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

    # Build Heat Pump Controller
    my_heat_pump_controller = generic_heat_pump.HeatPumpController(
        t_air_heating=t_air_heating,
        t_air_cooling=t_air_cooling,
        offset=offset,
        mode=hp_mode,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump = generic_heat_pump.GenericHeatPump(
        manufacturer=hp_manufacturer,
        name=hp_name,
        min_operation_time=hp_min_operation_time,
        min_idle_time=hp_min_idle_time,
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Connect Component Inputs with Outputs
    my_photovoltaic_system.connect_only_predefined_connections(my_weather)

    my_building.connect_only_predefined_connections(my_weather)
    my_building.connect_only_predefined_connections(my_occupancy)
    my_building.connect_input(
        my_building.ThermalEnergyDelivered,
        my_heat_pump.component_name,
        my_heat_pump.ThermalEnergyDelivered,
    )

    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.TemperatureMean,
        my_building.component_name,
        my_building.TemperatureMean,
    )
    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.ElectricityInput,
        my_base_electricity_load_profile.component_name,
        my_base_electricity_load_profile.ElectricityOutput,
    )

    my_heat_pump.connect_input(
        my_heat_pump.State,
        my_heat_pump_controller.component_name,
        my_heat_pump_controller.State,
    )
    my_heat_pump.connect_input(
        my_heat_pump.TemperatureOutside,
        my_weather.component_name,
        my_weather.TemperatureOutside,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters

    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_base_electricity_load_profile)
    my_sim.add_component(my_building)
    my_sim.add_component(my_heat_pump_controller)
    my_sim.add_component(my_heat_pump)
