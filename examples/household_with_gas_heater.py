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
        - Heat Water Storage Controller
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
    seconds_per_timestep = 60 * 15

    # Set Occupancy
    name = "UTSPConnector"
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
        name=name,
        url=url,
        api_key=api_key,
        household=household,
        result_path=result_path,
        travel_route_set=travel_route_set,
        transportation_device_set=transportation_device_set,
        charging_station_set=charging_station_set,
        consumption=0,
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

    # Build Gasheater
    my_gasheater = generic_gas_heater.GasHeater(
        config=generic_gas_heater.GenericGasHeaterConfig.get_default_gasheater_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Water Storage und Heat Water Storage Controller
    my_heat_water_storage = generic_heat_water_storage.HeatStorage(
        config=generic_heat_water_storage.HeatStorageConfig.get_default_heat_storage_config(),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_heat_water_storage_controller = generic_heat_water_storage.HeatStorageController(
        config=generic_heat_water_storage.HeatStorageControllerConfig.get_default_heat_storage_controller_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Controller
    my_controller_heat = controller_l1_heat_old.ControllerHeat(
        config=controller_l1_heat_old.ControllerHeatConfig.get_default_controller_heat_l1(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
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

    my_heat_water_storage.connect_input(
        my_heat_water_storage.ThermalDemandHeatingWater,
        my_heat_water_storage_controller.component_name,
        my_heat_water_storage_controller.RealThermalDemandHeatingWater,
    )

    my_heat_water_storage_controller.connect_input(
        my_heat_water_storage_controller.TemperatureHeatingStorage,
        my_heat_water_storage.component_name,
        my_heat_water_storage.WaterOutputTemperatureHeatingWater,
    )
    my_heat_water_storage_controller.connect_input(
        my_heat_water_storage_controller.BuildingTemperature,
        my_building.component_name,
        my_building.TemperatureIndoorAir,
    )
    # my_heat_water_storage_controller.connect_input(
    #     my_heat_water_storage_controller.ReferenceMaxHeatBuildingDemand,
    #     my_building.component_name,
    #     my_building.ReferenceMaxHeatBuildingDemand,
    # )

    my_controller_heat.connect_input(
        my_controller_heat.StorageTemperatureHeatingWater,
        my_heat_water_storage.component_name,
        my_heat_water_storage.WaterOutputTemperatureHeatingWater,
    )
    my_controller_heat.connect_input(
        my_controller_heat.ResidenceTemperature,
        my_building.component_name,
        my_building.TemperatureMeanThermalMass,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_building)
    my_sim.add_component(my_gasheater)

    my_sim.add_component(my_heat_water_storage)
    my_sim.add_component(my_heat_water_storage_controller)
    my_sim.add_component(my_controller_heat)
