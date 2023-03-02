""" Shows a single household with only heating. """
# clean
from typing import Optional, Any
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building
from hisim.components import controller_l1_heat_old
from hisim.components import generic_heat_water_storage
from hisim.components import generic_gas_heater


__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021-2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


def basic_household_only_heating(my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None) -> None:
    """ Gas heater + buffer storage.

    This setup function emulates an household including
    the basic components. Here the residents have their
    heating needs covered by a gas heater and a heating
    water storage. The controller_l2_ems controls according
    to the storage tempreature the gas heater.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - GasHeater
        - HeatingStorage
        - Controller2EMS
    """

    # =================================================================================================================================
    # Set System Parameters

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60 * 15

    # =================================================================================================================================
    # Build Components

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(year=year, seconds_per_timestep=seconds_per_timestep)
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build occupancy
    my_occupancy = loadprofilegenerator_connector.Occupancy(config=loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01(),
                                                            my_simulation_parameters=my_simulation_parameters)

    # Build Weather
    my_weather = weather.Weather(config=weather.WeatherConfig.get_default(weather.LocationEnum.Aachen),
                                 my_simulation_parameters=my_simulation_parameters)

    # Build Gas Heater
    my_gas_heater = generic_gas_heater.GasHeater(config=generic_gas_heater.GenericGasHeaterConfig.get_default_gasheater_config(),
                                                 my_simulation_parameters=my_simulation_parameters)

    # Build Building
    my_building = building.Building(config=building.BuildingConfig.get_default_german_single_family_home(),
                                    my_simulation_parameters=my_simulation_parameters)
    # my_building_controller = building.BuildingController(config=building.BuildingController.get_default_config(),
    #                                                      my_simulation_parameters=my_simulation_parameters)

    # Build Storage
    my_storage = generic_heat_water_storage.HeatStorage(config=generic_heat_water_storage.HeatStorageConfig.get_default_heat_storage_config(),
                                                        my_simulation_parameters=my_simulation_parameters)

    my_storage_controller = generic_heat_water_storage.HeatStorageController(
        config=generic_heat_water_storage.HeatStorageControllerConfig.get_default_heat_storage_controller_config(), my_simulation_parameters=my_simulation_parameters)

    # Build Controller
    my_controller_heat = controller_l1_heat_old.ControllerHeat(
        config=controller_l1_heat_old.ControllerHeatConfig.get_default_controller_heat_l1(), my_simulation_parameters=my_simulation_parameters)

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(my_building.ThermalPowerDelivered, my_storage.component_name, my_storage.RealHeatForBuilding)

    my_storage.connect_input(my_storage.ThermalDemandHeatingWater, my_storage_controller.component_name,
                             my_storage_controller.RealThermalDemandHeatingWater)
    my_storage.connect_input(my_storage.ControlSignalChooseStorage, my_controller_heat.component_name, my_controller_heat.ControlSignalChooseStorage)
    my_storage.connect_input(my_storage.ThermalInputPower1, my_gas_heater.component_name, my_gas_heater.ThermalOutputPower)

    my_storage_controller.connect_input(my_storage_controller.TemperatureHeatingStorage, my_storage.component_name,
                                        my_storage.WaterOutputTemperatureHeatingWater)
    my_storage_controller.connect_input(my_storage_controller.BuildingTemperature, my_building.component_name, my_building.TemperatureMeanThermalMass)
    my_storage_controller.connect_input(my_storage_controller.ReferenceMaxHeatBuildingDemand, my_building.component_name,
                                        my_building.ReferenceMaxHeatBuildingDemand)
    # my_storage_controller.connect_input(my_storage_controller.RealHeatBuildingDemand, my_building_controller.component_name,
    #                                     my_building_controller.RealHeatBuildingDemand)

    my_controller_heat.connect_input(my_controller_heat.StorageTemperatureHeatingWater, my_storage.component_name,
                                     my_storage.WaterOutputTemperatureHeatingWater)

    my_controller_heat.connect_input(my_controller_heat.ResidenceTemperature, my_building.component_name, my_building.TemperatureMeanThermalMass)

    my_gas_heater.connect_input(my_gas_heater.ControlSignal, my_controller_heat.component_name, my_controller_heat.ControlSignalGasHeater)
    my_gas_heater.connect_input(my_gas_heater.MassflowInputTemperature, my_storage.component_name, my_storage.WaterOutputStorageforHeaters)

    # =================================================================================================================================
    # Add Components to Simulation Parameters

    # my_sim.add_component(my_building_controller)
    my_sim.add_component(my_controller_heat)
    my_sim.add_component(my_storage_controller)

    my_sim.add_component(my_storage)
    my_sim.add_component(my_gas_heater)
    my_sim.add_component(my_building)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
