"""  Household system setup with PV system and heatpump. """
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
    EnergyIntensityType,
)
from utspclient.helpers.lpgpythonbindings import JsonReference
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import generic_heat_pump
from hisim.components import electricity_meter
from hisim.utils import get_environment_variable
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
    energy_intensity: EnergyIntensityType
    result_path: str
    travel_route_set: JsonReference
    simulation_parameters: SimulationParameters
    data_acquisition_mode: loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode
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
            energy_intensity=EnergyIntensityType.EnergySaving,
            data_acquisition_mode = loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_UTSP,
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


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Basic household system setup.

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

    # Set Occupancy
    data_acquisition_mode = my_config.data_acquisition_mode
    household = my_config.household_type
    energy_intensity = my_config.energy_intensity
    result_path = my_config.result_path
    travel_route_set = my_config.travel_route_set
    transportation_device_set = my_config.transportation_device_set
    charging_station_set = my_config.charging_station_set

    # Set Photovoltaic System
    power = my_config.pv_power
    azimuth = my_config.pv_azimuth
    tilt = my_config.tilt

    # Set Heat Pump Controller
    hp_mode = 2

    # =================================================================================================================================
    # Build Components

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.january_only_with_only_plots(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
        name="UTSPConnector",
        data_acquisition_mode=data_acquisition_mode,
        household=household,
        energy_intensity=energy_intensity,
        result_dir_path=result_path,
        travel_route_set=travel_route_set,
        transportation_device_set=transportation_device_set,
        charging_station_set=charging_station_set,
        consumption=0,
        profile_with_washing_machine_and_dishwasher=True,
        predictive_control=False,
        predictive=False,
    )

    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather = weather.Weather(
        config=weather.WeatherConfig.get_default(weather.LocationEnum.AACHEN),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build PV
    my_photovoltaic_system_config = (
        generic_pv_system.PVSystemConfig.get_default_pv_system()
    )
    my_photovoltaic_system_config.power_in_watt_peak = power
    my_photovoltaic_system_config.azimuth = azimuth
    my_photovoltaic_system_config.tilt = tilt
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )

    # Build Building
    my_building = building.Building(
        config=building.BuildingConfig.get_default_german_single_family_home(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump Controller Config
    my_heat_pump_controller_config = (
        generic_heat_pump.GenericHeatPumpControllerConfig.get_default_generic_heat_pump_controller_config()
    )
    my_heat_pump_controller_config.mode = hp_mode
    # Build Heat Pump Controller
    my_heat_pump_controller = generic_heat_pump.GenericHeatPumpController(
        config=my_heat_pump_controller_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump = generic_heat_pump.GenericHeatPump(
        config=generic_heat_pump.GenericHeatPumpConfig.get_default_generic_heat_pump_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # # Connect Component Inputs with Outputs

    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_pump.component_name,
        my_heat_pump.ThermalPowerDelivered,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters

    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)
    my_sim.add_component(my_electricity_meter, connect_automatically=True)
    my_sim.add_component(my_building, connect_automatically=True)
    my_sim.add_component(my_heat_pump_controller, connect_automatically=True)
    my_sim.add_component(my_heat_pump, connect_automatically=True)
