"""Household system setup with advanced heat pump, electric car, PV and battery."""

# clean

from dataclasses import dataclass
from typing import Any, List, Optional

from dataclasses_json import dataclass_json
from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    EnergyIntensityType,
    Households,
    TransportationDeviceSets,
    TravelRouteSets,
)

from hisim import loadtypes as lt
from hisim import utils
from hisim.components import (
    advanced_battery_bslib,
    advanced_ev_battery_bslib,
    advanced_heat_pump_hplib,
    building,
    controller_l1_generic_ev_charge,
    controller_l1_heatpump,
    controller_l2_energy_management_system,
    electricity_meter,
    generic_car,
    generic_heat_pump_modular,
    generic_hot_water_storage_modular,
    generic_pv_system,
    heat_distribution_system,
    loadprofilegenerator_utsp_connector,
    simple_water_storage,
    weather,
)
from hisim.simulator import SimulationParameters
from hisim.system_setup_configuration import SystemSetupConfigBase
from hisim.units import Celsius, Quantity, Seconds, Watt

__authors__ = "Markus Blasberg"
__copyright__ = "Copyright 2023, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Markus Blasberg"
__status__ = "development"


@dataclass_json
@dataclass
class HouseholdAdvancedHpEvPvBatteryConfig(SystemSetupConfigBase):
    """Configuration for with advanced heat pump, electric car, PV and battery."""

    building_type: str
    number_of_apartments: int
    # dhw_controllable: bool  # if dhw is controlled by EMS
    # heatpump_controllable: bool  # if heatpump is controlled by EMS
    surplus_control: (
        bool  # decision on the consideration of smart control for heat pump and dhw, increase storage temperatures
    )
    surplus_control_building_temperature_modifier: bool  # increase set_room_temperature in case of surplus electricity
    surplus_control_car: bool  # decision on the consideration of smart control for EV charging
    # simulation_parameters: SimulationParameters
    # total_base_area_in_m2: float
    occupancy_config: loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig
    pv_config: generic_pv_system.PVSystemConfig
    building_config: building.BuildingConfig
    hds_controller_config: heat_distribution_system.HeatDistributionControllerConfig
    hds_config: heat_distribution_system.HeatDistributionConfig
    hp_controller_config: advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config
    hp_config: advanced_heat_pump_hplib.HeatPumpHplibConfig
    simple_hot_water_storage_config: simple_water_storage.SimpleHotWaterStorageConfig
    dhw_heatpump_config: generic_heat_pump_modular.HeatPumpConfig
    dhw_heatpump_controller_config: controller_l1_heatpump.L1HeatPumpConfig
    dhw_storage_config: generic_hot_water_storage_modular.StorageConfig
    car_config: generic_car.CarConfig
    car_battery_config: advanced_ev_battery_bslib.CarBatteryConfig
    car_battery_controller_config: controller_l1_generic_ev_charge.ChargingStationConfig
    electricity_meter_config: electricity_meter.ElectricityMeterConfig
    advanced_battery_config: advanced_battery_bslib.BatteryConfig
    electricity_controller_config: controller_l2_energy_management_system.EMSConfig

    @classmethod
    def get_default(cls):
        """Get default HouseholdAdvancedHpEvPvBatteryConfig."""

        charging_station_set = ChargingStationSets.Charging_At_Home_with_11_kW
        charging_power = float((charging_station_set.Name or "").split("with ")[1].split(" kW")[0])
        heating_reference_temperature_in_celsius: float = -7
        set_heating_threshold_outside_temperature_in_celsius: float = 16.0
        building_config = building.BuildingConfig.get_default_german_single_family_home(
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius
        )
        my_building_information = building.BuildingInformation(config=building_config)
        hds_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
            set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
            heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
        )
        my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
            config=hds_controller_config
        )

        pv_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
            rooftop_area_in_m2=my_building_information.roof_area_in_m2
        )

        household_config = HouseholdAdvancedHpEvPvBatteryConfig(
            building_type="blub",
            number_of_apartments=my_building_information.number_of_apartments,
            # dhw_controllable=False,
            # heatpump_controllable=False,
            surplus_control=False,
            surplus_control_building_temperature_modifier=False,
            surplus_control_car=False,
            # simulation_parameters=SimulationParameters.one_day_only(2022),
            # total_base_area_in_m2=121.2,
            occupancy_config=loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
                building_name="BUI1",
                data_acquisition_mode=loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_UTSP,
                household=Households.CHR01_Couple_both_at_Work,
                energy_intensity=EnergyIntensityType.EnergySaving,
                result_dir_path=utils.HISIMPATH["utsp_results"],
                travel_route_set=TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance,
                transportation_device_set=TransportationDeviceSets.Bus_and_one_30_km_h_Car,
                charging_station_set=charging_station_set,
                name="UTSPConnector",
                profile_with_washing_machine_and_dishwasher=True,
                predictive_control=False,
                predictive=False,
            ),
            pv_config=pv_config,
            building_config=building_config,
            hds_controller_config=hds_controller_config,
            hds_config=(
                heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
                    water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
                    absolute_conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
                    heating_system=hds_controller_config.heating_system,
                )
            ),
            hp_controller_config=advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config.get_default_generic_heat_pump_controller_config(
                heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type
            ),
            hp_config=(
                advanced_heat_pump_hplib.HeatPumpHplibConfig.get_scaled_advanced_hp_lib(
                    heating_load_of_building_in_watt=Quantity(
                        my_building_information.max_thermal_building_demand_in_watt, Watt
                    ),
                    heating_reference_temperature_in_celsius=Quantity(
                        heating_reference_temperature_in_celsius, Celsius
                    ),
                )
            ),
            simple_hot_water_storage_config=(
                simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
                    max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
                    temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
                    sizing_option=simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP,
                )
            ),
            dhw_heatpump_config=(
                generic_heat_pump_modular.HeatPumpConfig.get_scaled_waterheating_to_number_of_apartments(
                    number_of_apartments=my_building_information.number_of_apartments
                )
            ),
            dhw_heatpump_controller_config=controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
                name="DHWHeatpumpController"
            ),
            dhw_storage_config=(
                generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(
                    number_of_apartments=my_building_information.number_of_apartments
                )
            ),
            car_config=generic_car.CarConfig.get_default_ev_config(),
            car_battery_config=advanced_ev_battery_bslib.CarBatteryConfig.get_default_config(),
            car_battery_controller_config=(
                controller_l1_generic_ev_charge.ChargingStationConfig.get_default_config(
                    charging_station_set=charging_station_set
                )
            ),
            electricity_meter_config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
            advanced_battery_config=advanced_battery_bslib.BatteryConfig.get_scaled_battery(
                total_pv_power_in_watt_peak=pv_config.power_in_watt
            ),
            electricity_controller_config=(controller_l2_energy_management_system.EMSConfig.get_default_config_ems()),
        )
        # adjust HeatPump
        household_config.hp_config.group_id = 1  # use modulating heatpump as default
        household_config.hp_controller_config.mode = 2  # use heating and cooling as default
        # household_config.hp_config.set_thermal_output_power_in_watt = (
        #     6000  # default value leads to switching on-off very often
        # )
        household_config.hp_config.minimum_idle_time_in_seconds = Quantity(
            900, Seconds  # default value leads to switching on-off very often
        )
        household_config.hp_config.minimum_running_time_in_seconds = Quantity(
            900, Seconds  # default value leads to switching on-off very often
        )

        # set same heating threshold
        household_config.hds_controller_config.set_heating_threshold_outside_temperature_in_celsius = (
            set_heating_threshold_outside_temperature_in_celsius
        )
        household_config.hp_controller_config.set_heating_threshold_outside_temperature_in_celsius = (
            set_heating_threshold_outside_temperature_in_celsius
        )

        household_config.hp_config.flow_temperature_in_celsius = Quantity(21, Celsius)  # Todo: check value

        # set dhw storage volume, because default(volume = 230) leads to an error
        household_config.dhw_storage_config.volume = 250

        # set charging power from battery and controller to same value, to reduce error in simulation of battery
        household_config.car_battery_config.p_inv_custom = charging_power * 1e3

        if household_config.surplus_control_car:
            # lower threshold for soc of car battery in clever case. This enables more surplus charging
            household_config.car_battery_controller_config.battery_set = 0.4
        else:
            household_config.car_battery_controller_config.battery_set = 1.0

        return household_config


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """System setup with advanced hp and EV and PV and battery.

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
        - Battery
        - EMS (necessary for Battery and Electric Vehicle)
    """

    # my_config = utils.create_configuration(my_sim, HouseholdAdvancedHpEvPvBatteryConfig)

    # Todo: save file leads to use of file in next run. File was just produced to check how it looks like
    if my_sim.my_module_config:
        my_config = HouseholdAdvancedHpEvPvBatteryConfig.load_from_json(my_sim.my_module_config)
    else:
        my_config = HouseholdAdvancedHpEvPvBatteryConfig.get_default()
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

    # Build Battery
    my_advanced_battery = advanced_battery_bslib.Battery(
        my_simulation_parameters=my_simulation_parameters,
        config=my_config.advanced_battery_config,
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
                source_weight=1,
            )

            electricity_target = my_electricity_controller.add_component_output(
                source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
                source_tags=[
                    lt.ComponentType.CAR_BATTERY,
                    lt.InandOutputType.ELECTRICITY_TARGET,
                ],
                # source_weight=car_battery_controller.source_weight,
                source_weight=1,
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
            source_weight=3,
        )

        my_electricity_controller.add_component_output(
            source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                lt.ComponentType.HEAT_PUMP_BUILDING,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=3,
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

    # connect EMS with Battery
    my_electricity_controller.add_component_input_and_connect(
        source_object_name=my_advanced_battery.component_name,
        source_component_output=my_advanced_battery.AcBatteryPowerUsed,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
        source_weight=4,
    )

    electricity_to_or_from_battery_target = my_electricity_controller.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[
            lt.ComponentType.BATTERY,
            lt.InandOutputType.ELECTRICITY_TARGET,
        ],
        source_weight=4,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for Battery Control. ",
    )

    # -----------------------------------------------------------------------------------------------------------------
    # Connect Battery
    my_advanced_battery.connect_dynamic_input(
        input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput,
        src_object=electricity_to_or_from_battery_target,
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
    my_sim.add_component(my_advanced_battery)
    my_sim.add_component(my_electricity_controller)
    for car in my_cars:
        my_sim.add_component(car)
    for car_battery in my_car_batteries:
        my_sim.add_component(car_battery)
    for car_battery_controller in my_car_battery_controllers:
        my_sim.add_component(car_battery_controller)
