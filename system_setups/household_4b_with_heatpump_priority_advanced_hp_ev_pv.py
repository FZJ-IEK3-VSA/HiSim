"""Household system setup with advanced heat pump, electric car, PV. Only Source_weights are different to household_4."""

# clean

from typing import List, Optional, Any
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import advanced_heat_pump_hplib
from hisim.components import heat_distribution_system
from hisim.components import building
from hisim.components import simple_water_storage
from hisim.components import generic_car
from hisim.components import generic_heat_pump_modular
from hisim.components import controller_l1_heatpump
from hisim.components import generic_hot_water_storage_modular
from hisim.components import electricity_meter
from hisim.components import generic_pv_system
from hisim.components import advanced_ev_battery_bslib
from hisim.components import controller_l1_generic_ev_charge
from hisim.components import controller_l2_energy_management_system
from hisim import loadtypes as lt
from system_setups.household_4a_with_car_priority_advanced_hp_ev_pv import HouseholdAdvancedHPEvPvConfig

__authors__ = "Markus Blasberg"
__copyright__ = "Copyright 2023, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Markus Blasberg"
__status__ = "development"


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """System setup with advanced hp and EV and PV.

    This setup function emulates a household with some basic components. Here the residents have their
    electricity and heating needs covered by a the advanced heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Building
        - PV
        - Electricity Meter
        - Advanced Heat Pump HPlib
        - Advanced Heat Pump HPlib Controller
        - Heat Distribution System
        - Heat Distribution System Controller
        - Simple Hot Water Storage

        - DHW (Heatpump, Heatpumpcontroller, Storage; copied from modular_example)
        - Car (Electric Vehicle, Electric Vehicle Battery, Electric Vehicle Battery Controller)
        - EMS (necessary for Electric Vehicle)
    """

    # my_config = utils.create_configuration(my_sim, HouseholdAdvancedHPEvPvConfig)

    # Todo: save file leads to use of file in next run. File was just produced to check how it looks like
    if my_sim.my_module_config:
        my_config = HouseholdAdvancedHPEvPvConfig.load_from_json(my_sim.my_module_config)
    else:
        my_config = HouseholdAdvancedHPEvPvConfig.get_default()
    # =================================================================================================================================
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
    my_simulation_parameters.surplus_control = (
        my_config.surplus_control_car
    )  # EV charger is controlled by simulation_parameters
    clever = my_config.surplus_control
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

    # Build PV
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_config.pv_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Building
    my_building = building.Building(
        config=my_config.building_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Distribution System
    my_heat_distribution = heat_distribution_system.HeatDistribution(
        my_simulation_parameters=my_simulation_parameters, config=my_config.hds_config
    )

    # Build Heat Pump Controller
    my_heat_pump_controller_config = my_config.hp_controller_config
    my_heat_pump_controller_config.name = "HeatPumpHplibController"

    my_heat_pump_controller = advanced_heat_pump_hplib.HeatPumpHplibController(
        config=my_heat_pump_controller_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump_config = my_config.hp_config
    my_heat_pump_config.name = "HeatPumpHPLib"

    my_heat_pump = advanced_heat_pump_hplib.HeatPumpHplib(
        config=my_heat_pump_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Water Storage
    my_simple_hot_water_storage = simple_water_storage.SimpleHotWaterStorage(
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

    # Build Electric Vehicle(s)
    # get all available cars from occupancy
    my_car_information = generic_car.GenericCarInformation(my_occupancy_instance=my_occupancy)

    my_car_config = my_config.car_config
    my_car_config.name = "ElectricCar"

    # create all cars
    my_cars: List[generic_car.Car] = []
    for idx, car_information_dict in enumerate(my_car_information.data_dict_for_car_component.values()):
        my_car_config.name = car_information_dict["car_name"] + f"_{idx}"
        my_cars.append(
            generic_car.Car(
                my_simulation_parameters=my_simulation_parameters,
                config=my_car_config,
                data_dict_with_car_information=car_information_dict,
            )
        )

    # Build Electric Vehicle Battery
    my_car_batteries: List[advanced_ev_battery_bslib.CarBattery] = []
    my_car_battery_controllers: List[controller_l1_generic_ev_charge.L1Controller] = []
    car_number = 1
    for car in my_cars:
        my_car_battery_config = my_config.car_battery_config
        my_car_battery_config.source_weight = car.config.source_weight
        my_car_battery_config.name = f"CarBattery_{car_number}"
        my_car_battery = advanced_ev_battery_bslib.CarBattery(
            my_simulation_parameters=my_simulation_parameters,
            config=my_car_battery_config,
        )
        my_car_batteries.append(my_car_battery)

        my_car_battery_controller_config = my_config.car_battery_controller_config
        my_car_battery_controller_config.source_weight = car.config.source_weight
        my_car_battery_controller_config.name = f"L1EVChargeControl_{car_number}"
        if my_config.surplus_control_car:
            # lower threshold for soc of car battery in clever case. This enables more surplus charging
            # Todo: this is just to avoid errors in case config from json-file is used
            my_car_battery_controller_config.battery_set = 0.4

        my_car_battery_controller = controller_l1_generic_ev_charge.L1Controller(
            my_simulation_parameters=my_simulation_parameters,
            config=my_car_battery_controller_config,
        )
        my_car_battery_controllers.append(my_car_battery_controller)

        car_number += 1

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=my_config.electricity_meter_config,
    )

    # Build EMS
    my_electricity_controller = controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
        my_simulation_parameters=my_simulation_parameters,
        config=my_config.electricity_controller_config,
    )

    # -----------------------------------------------------------------------------------------------------------------
    # connect Electric Vehicle
    # copied and adopted from modular_example
    for car, car_battery, car_battery_controller in zip(my_cars, my_car_batteries, my_car_battery_controllers):
        car_battery_controller.connect_only_predefined_connections(car)
        car_battery_controller.connect_only_predefined_connections(car_battery)
        car_battery.connect_only_predefined_connections(car_battery_controller)

        if my_config.surplus_control_car:
            my_electricity_controller.add_component_input_and_connect(
                source_object_name=car_battery_controller.component_name,
                source_component_output=car_battery_controller.BatteryChargingPowerToEMS,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.CAR_BATTERY,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                ],
                # source_weight=car_battery.source_weight,
                source_weight=3,
            )

            electricity_target = my_electricity_controller.add_component_output(
                source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
                source_tags=[
                    lt.ComponentType.CAR_BATTERY,
                    lt.InandOutputType.ELECTRICITY_TARGET,
                ],
                # source_weight=car_battery_controller.source_weight,
                source_weight=3,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                output_description="Target Electricity for EV Battery Controller. ",
            )

            car_battery_controller.connect_dynamic_input(
                input_fieldname=controller_l1_generic_ev_charge.L1Controller.ElectricityTarget,
                src_object=electricity_target,
            )
        else:
            my_electricity_controller.add_component_input_and_connect(
                source_object_name=car_battery_controller.component_name,
                source_component_output=car_battery_controller.BatteryChargingPowerToEMS,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                ],
                source_weight=999,
            )

    # -----------------------------------------------------------------------------------------------------------------
    # connect EMS
    # copied and adopted from household_with_advanced_hp_hws_hds_pv_battery_ems
    my_electricity_controller.add_component_input_and_connect(
        source_object_name=my_occupancy.component_name,
        source_component_output=my_occupancy.ElectricalPowerConsumption,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )

    # connect EMS with DHW
    if clever:
        my_domnestic_hot_water_heatpump_controller.connect_input(
            my_domnestic_hot_water_heatpump_controller.StorageTemperatureModifier,
            my_electricity_controller.component_name,
            my_electricity_controller.DomesticHotWaterStorageTemperatureModifier,
        )
        my_electricity_controller.add_component_input_and_connect(
            source_object_name=my_domnestic_hot_water_heatpump.component_name,
            source_component_output=my_domnestic_hot_water_heatpump.ElectricityOutput,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[
                lt.ComponentType.HEAT_PUMP_DHW,
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
            ],
            # source_weight=my_dhw_heatpump_config.source_weight,
            source_weight=2,
        )

        my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                lt.ComponentType.HEAT_PUMP_DHW,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            # source_weight=my_domnestic_hot_water_heatpump.config.source_weight,
            source_weight=2,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for dhw heat pump.",
        )

    else:
        my_electricity_controller.add_component_input_and_connect(
            source_object_name=my_domnestic_hot_water_heatpump.component_name,
            source_component_output=my_domnestic_hot_water_heatpump.ElectricityOutput,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            source_weight=999,
        )

    # connect EMS with Heatpump
    if clever:
        my_heat_pump_controller.connect_input(
            my_heat_pump_controller.SimpleHotWaterStorageTemperatureModifier,
            my_electricity_controller.component_name,
            my_electricity_controller.SpaceHeatingWaterStorageTemperatureModifier,
        )

        my_electricity_controller.add_component_input_and_connect(
            source_object_name=my_heat_pump.component_name,
            source_component_output=my_heat_pump.ElectricalInputPower,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[
                lt.ComponentType.HEAT_PUMP_BUILDING,
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
            ],
            source_weight=1,
        )

        my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                lt.ComponentType.HEAT_PUMP_BUILDING,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=1,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for Heat Pump. ",
        )

    else:
        my_electricity_controller.add_component_input_and_connect(
            source_object_name=my_heat_pump.component_name,
            source_component_output=my_heat_pump.ElectricalInputPower,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            source_weight=999,
        )

    # connect EMS BuildingTemperatureModifier with set_heating_temperature_for_building_in_celsius
    if my_config.surplus_control_building_temperature_modifier:
        my_heat_distribution_controller.connect_input(
            my_heat_distribution_controller.BuildingTemperatureModifier,
            my_electricity_controller.component_name,
            my_electricity_controller.BuildingIndoorTemperatureModifier,
        )
        my_building.connect_input(
            my_building.BuildingTemperatureModifier,
            my_electricity_controller.component_name,
            my_electricity_controller.BuildingIndoorTemperatureModifier,
        )

    # connect EMS with PV
    my_electricity_controller.add_component_input_and_connect(
        source_object_name=my_photovoltaic_system.component_name,
        source_component_output=my_photovoltaic_system.ElectricityOutput,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=999,
    )

    # -----------------------------------------------------------------------------------------------------------------
    # connect Electricity Meter
    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_electricity_controller.component_name,
        source_component_output=my_electricity_controller.TotalElectricityToOrFromGrid,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=999,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)
    my_sim.add_component(my_building, connect_automatically=True)
    my_sim.add_component(my_heat_pump, connect_automatically=True)
    my_sim.add_component(my_heat_pump_controller, connect_automatically=True)
    my_sim.add_component(my_heat_distribution, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)
    my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_heatpump_controller, connect_automatically=True)
    my_sim.add_component(my_domnestic_hot_water_heatpump, connect_automatically=True)
    my_sim.add_component(my_electricity_meter)
    my_sim.add_component(my_electricity_controller)
    for car in my_cars:
        my_sim.add_component(car)
    for car_battery in my_car_batteries:
        my_sim.add_component(car_battery)
    for car_battery_controller in my_car_battery_controllers:
        my_sim.add_component(car_battery_controller)
