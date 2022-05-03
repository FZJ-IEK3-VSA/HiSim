from typing import Optional
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import advanced_battery
from hisim.components import controller_l2_energy_management_system
from hisim.components import generic_hot_water_storage
from hisim.components import generic_gas_heater

from hisim import utils
from hisim import loadtypes as lt
import os
import numpy as np

__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = "development"

def basic_household_explicit_ems(my_sim, my_simulation_parameters: Optional[SimulationParameters] = None):
    """
    This setup function emulates an household including
    the basic components. Here the residents have their
    electricity and heating needs covered by the photovoltaic
    system and the heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump
    """

    ##### System Parameters #####

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60*15

    # Set weather
    location = "Aachen"

    # Set photovoltaic system
    time = 2019
    power = 10E3
    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrateInverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"

    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"

    # Set occupancy
    occupancy_profile = "CH01"

    # Controller
    strategy = "optimize_own_consumption"

    ##### Build Components #####

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(year=year,
                                                                                 seconds_per_timestep=seconds_per_timestep)
    my_sim.SimulationParameters = my_simulation_parameters

    # Build occupancy
    my_occupancy = loadprofilegenerator_connector.Occupancy(profile_name=occupancy_profile, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather = weather.Weather(location=location, my_simulation_parameters= my_simulation_parameters)
    my_sim.add_component(my_weather)

    # Build Gas Heater
    my_gas_heater = generic_gas_heater.GasHeater(my_simulation_parameters=my_simulation_parameters)

    # Build Building
    my_building = building.Building(my_simulation_parameters= my_simulation_parameters)
    my_sim.add_component(my_building)

    # Build Storage
    my_storage = generic_hot_water_storage.HeatStorage(my_simulation_parameters=my_simulation_parameters)
    # Build Controller
    my_controller= controller_l2_energy_management_system.Controller(strategy="optimize_own_consumption", my_simulation_parameters= my_simulation_parameters)

    my_building.connect_only_predefined_connections( my_weather, my_occupancy )


    my_storage.connect_input(my_storage.ThermalDemandHeatingWater,
                              my_controller.ComponentName,
                              my_controller.ThermalDemandHeatingStorage)
    my_storage.connect_input(my_storage.ControlSignalChooseStorage,
                              my_controller.ComponentName,
                              my_controller.ControlSignalChooseStorage)

    my_building.connect_input(my_building.ThermalEnergyDelivered,
                              my_controller.ComponentName,
                              my_controller.ThermalDemandHeatingStorage)

    my_controller.connect_input(my_controller.StorageTemperatureHeatingWater,
                              my_storage.ComponentName,
                              my_storage.WaterOutputTemperatureHeatingWater)
    my_controller.connect_input(my_controller.ThermalDemandBuilding,
                              my_building.ComponentName,
                              my_building.ThermalBuildingDemand)
    my_controller.connect_input(my_controller.ResidenceTemperature,
                              my_building.ComponentName,
                              my_building.TemperatureMean)

    my_gas_heater.connect_input(my_gas_heater.ControlSignal,
                              my_controller.ComponentName,
                              my_controller.ControlSignalGasHeater)
    my_gas_heater.connect_input(my_gas_heater.MassflowInputTemperature,
                              my_storage.ComponentName,
                              my_storage.WaterOutputStorageforHeaters)
    my_storage.connect_input(my_storage.ThermalInputPower1,
                              my_gas_heater.ComponentName,
                              my_gas_heater.ThermalOutputPower)

    my_sim.add_component(my_controller)
    my_sim.add_component(my_storage)
    my_sim.add_component(my_gas_heater)

def basic_household_explicit(my_sim, my_simulation_parameters: Optional[SimulationParameters] = None):
    """
    This setup function emulates an household including
    the basic components. Here the residents have their
    electricity and heating needs covered by the photovoltaic
    system and the heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump
    """

    ##### System Parameters #####

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60*15

    # Set weather
    location = "Aachen"

    # Set photovoltaic system
    time = 2019
    power = 10E3
    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrateInverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"

    # Set occupancy
    occupancy_profile = "CH01"

    # Set Battery Parameter
    parameter=np.load(os.path.join(utils.HISIMPATH["advanced_battery"]["siemens_junelight"]))

    # Controller
    strategy = "optimize_own_consumption"

    ##### Build Components #####

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(year=year,
                                                                                 seconds_per_timestep=seconds_per_timestep)
    my_sim.SimulationParameters = my_simulation_parameters
    # Build occupancy
    my_occupancy = loadprofilegenerator_connector.Occupancy(profile_name=occupancy_profile, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather = weather.Weather(location=location, my_simulation_parameters= my_simulation_parameters)
    my_sim.add_component(my_weather)

    # Build Battery
    my_battery_1=advanced_battery.AdvancedBattery(parameter=parameter, my_simulation_parameters= my_simulation_parameters)
    my_battery_2=advanced_battery.AdvancedBattery(parameter=parameter, my_simulation_parameters= my_simulation_parameters)

    # Build Controller
    my_controller= controller_l2_energy_management_system.ControllerGeneric(strategy="optimize_own_consumption", my_simulation_parameters= my_simulation_parameters)

    my_photovoltaic_system= generic_pv_system.PVSystem(time=time,
                                          location=location,
                                          power=power,
                                          load_module_data=load_module_data,
                                          module_name=module_name,
                                          integrateInverter=integrateInverter,
                                          inverter_name=inverter_name,
                                          my_simulation_parameters=my_simulation_parameters)
    my_photovoltaic_system.connect_only_predefined_connections(my_weather)
    my_sim.add_component(my_photovoltaic_system)

    my_controller.connect_input(my_controller.ElectricityOutputPvs,
                              my_photovoltaic_system.ComponentName,
                              my_photovoltaic_system.ElectricityOutput)
    my_controller.connect_input(my_controller.ElectricityConsumptionBuilding,
                              my_occupancy.ComponentName,
                              my_occupancy.ElectricityOutput)
    my_controller.connect_input(my_controller.ElectricityToOrFromBatteryReal,
                              my_battery_1.ComponentName,
                              my_battery_1.ACBatteryPower)
    my_sim.add_component(my_controller)

    my_battery_1.connect_input(my_battery_1.LoadingPowerInput,
                              my_controller.ComponentName,
                              my_controller.ElectricityToOrFromBatteryTarget)
    my_sim.add_component(my_battery_1)

    my_controller.add_component_input_and_connect( source_component_class=my_battery_1,
                                               source_component_output=my_battery_1.ACBatteryPower,
                                               source_load_type= lt.LoadTypes.Electricity,
                                               source_unit= lt.Units.Watt,
                                               source_tags=[lt.ComponentType.Battery,lt.InandOutputType.ElectricityReal],
                                               source_weight=1)
    my_controller.add_component_input_and_connect( source_component_class=my_battery_2,
                                               source_component_output=my_battery_2.ACBatteryPower,
                                               source_load_type= lt.LoadTypes.Electricity,
                                               source_unit= lt.Units.Watt,
                                               source_tags=[lt.ComponentType.Battery,lt.InandOutputType.ElectricityReal],
                                               source_weight=2)

    electricity_target_to_battery_1 = my_controller.add_component_output(source_output_name=lt.InandOutputType.ElectricityTarget,
                                               source_tags=[lt.ComponentType.Battery],
                                               source_load_type= lt.LoadTypes.Electricity,
                                               source_unit= lt.Units.Watt)

    my_battery_1.connect_dynamic_input(input_fieldname=advanced_battery.AdvancedBattery.LoadingPowerInput,
                                        src_object=electricity_target_to_battery_1)

    electricity_target_to_battery_2 = my_controller.add_component_output(source_output_name=lt.InandOutputType.ElectricityTarget,
                                               source_tags=[lt.ComponentType.Battery],
                                               source_load_type= lt.LoadTypes.Electricity,
                                               source_unit= lt.Units.Watt)

    my_battery_2.connect_dynamic_input(input_fieldname=advanced_battery.AdvancedBattery.LoadingPowerInput,
                                        src_object=electricity_target_to_battery_2)

