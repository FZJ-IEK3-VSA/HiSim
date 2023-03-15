"""  Household example with gas heater. """
# clean
from typing import Optional, Any
from pathlib import Path

from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_gas_heater_with_controller
from hisim.components import heat_distribution_system
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
        - Gas Heater Controller
        - Heat Distribution System
        - Heat Distribution System Controller
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
        name="UTSP Connector"
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
    my_gasheater = generic_gas_heater_with_controller.GasHeaterWithController(
        config=generic_gas_heater_with_controller.GenericGasHeaterWithControllerConfig.get_default_gasheater_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Gas Heater Controller
    my_gasheater_controller = generic_gas_heater_with_controller.GasHeaterController(
        my_simulation_parameters=my_simulation_parameters,
        set_heating_temperature_water_boiler_in_celsius=60.0,
        offset=2.0,
        mode=1
    )

    hds_config = heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config()

    # Build Heat Distribution System
    my_heat_distribution = heat_distribution_system.HeatDistribution(
        my_simulation_parameters=my_simulation_parameters, config=hds_config
    )

    # Build heat Distribution System Controller
    hdscontroller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config()
    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(config=hdscontroller_config, my_simulation_parameters=my_simulation_parameters)

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_distribution.component_name,
        my_heat_distribution.ThermalPowerDelivered,
    )

    my_gasheater.connect_input(
        my_gasheater.State,
        my_gasheater_controller.component_name,
        my_gasheater_controller.State,
    )

    my_gasheater.connect_input(
        my_gasheater.CooledWaterTemperatureBoilerInput,
        my_heat_distribution.component_name,
        my_heat_distribution.WaterTemperatureOutput,
    )
    my_gasheater.connect_input(
        my_gasheater.ReferenceMaxHeatBuildingDemand,
        my_building.component_name,
        my_building.ReferenceMaxHeatBuildingDemand,
    )

    # my_gasheater.connect_input(
    #     my_gasheater.InitialResidenceTemperature,
    #     my_building.component_name,
    #     my_building.InitialInternalTemperature,
    # )
    my_gasheater.connect_input(
        my_gasheater.ResidenceTemperature,
        my_building.component_name,
        my_building.TemperatureMeanThermalMass,
    )

    my_gasheater_controller.connect_input(
        my_gasheater_controller.MeanWaterTemperatureGasHeaterControllerInput,
        my_gasheater.component_name,
        my_gasheater.MeanWaterTemperatureBoilerOutput,
    )

    my_heat_distribution.connect_input(
        my_heat_distribution.State,
        my_heat_distribution_controller.component_name,
        my_heat_distribution_controller.State,
    )

    my_heat_distribution.connect_input(
        my_heat_distribution.WaterTemperatureInput,
        my_gasheater.component_name,
        my_gasheater.HeatedWaterTemperatureBoilerOutput,
    )

    my_heat_distribution.connect_input(
        my_heat_distribution.MaxWaterMassFlowRate,
        my_gasheater.component_name,
        my_gasheater.MaxMassFlow,
    )
    my_heat_distribution.connect_input(
        my_heat_distribution.ResidenceTemperature,
        my_building.component_name,
        my_building.TemperatureMeanThermalMass,
    )

    my_heat_distribution_controller.connect_input(
        my_heat_distribution_controller.ResidenceTemperature,
        my_building.component_name,
        my_building.TemperatureMeanThermalMass,
    )

    my_heat_distribution_controller.connect_input(
        my_heat_distribution_controller.ControlSignalFromHeater,
        my_gasheater.component_name,
        my_gasheater.ControlSignalfromHeaterToDistribution,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_building)
    my_sim.add_component(my_gasheater)
    my_sim.add_component(my_gasheater_controller)
    my_sim.add_component(my_heat_distribution)
    my_sim.add_component(my_heat_distribution_controller)
