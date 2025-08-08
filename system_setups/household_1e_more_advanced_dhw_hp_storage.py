"""Household system setup with advanced heat pump and diesel car."""

# clean
from typing import List, Optional, Any
from dataclasses import dataclass
from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    Households,
    TransportationDeviceSets,
    TravelRouteSets,
    EnergyIntensityType,
)

from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import more_advanced_heat_pump_hplib
from hisim.components import heat_distribution_system
from hisim.components import building
from hisim.components import simple_water_storage
from hisim.components import generic_car
from hisim.components import electricity_meter
from hisim.system_setup_configuration import SystemSetupConfigBase
from hisim import utils
from hisim.units import Quantity, Watt, Celsius, Seconds
import hisim.loadtypes as lt


__authors__ = ["Jonas Hoppe"]
__copyright__ = ""
__credits__ = [""]
__license__ = ""
__version__ = ""
__maintainer__ = ""
__status__ = ""


@dataclass
class HouseholdMoreAdvancedHPDieselCarDHWStorageOptions:
    """Set options for the system setup."""

    pass


@dataclass
class HouseholdMoreAdvancedHPDieselCarDHWStorageConfig(SystemSetupConfigBase):
    """Configuration for with more advanced heat pump and diesel car."""

    building_type: str
    number_of_apartments: int
    occupancy_config: loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig
    building_config: building.BuildingConfig
    hds_controller_config: heat_distribution_system.HeatDistributionControllerConfig
    hds_config: heat_distribution_system.HeatDistributionConfig
    sh_controller_config: more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig
    hp_config: more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig
    simple_hot_water_storage_config: simple_water_storage.SimpleHotWaterStorageConfig
    dhw_heatpump_controller_config: more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHWConfig
    dhw_storage_config: simple_water_storage.SimpleDHWStorageConfig
    car_config: generic_car.CarConfig
    electricity_meter_config: electricity_meter.ElectricityMeterConfig

    @classmethod
    def get_default_options(cls):
        """Get default options."""
        return HouseholdMoreAdvancedHPDieselCarDHWStorageOptions()

    @classmethod
    def get_default(cls) -> "HouseholdMoreAdvancedHPDieselCarDHWStorageConfig":
        """Get default HouseholdMoreAdvancedHPDieselCarConfig."""

        heating_reference_temperature_in_celsius: float = -7

        building_config = building.BuildingConfig.get_default_german_single_family_home(
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius
        )

        household_config = cls.get_scaled_default(building_config)
        household_config.hp_config.with_domestic_hot_water_preparation = True
        household_config.hp_config.set_thermal_output_power_in_watt = Quantity(
            6000, Watt  # default value leads to switching on-off very often
        )
        household_config.dhw_storage_config.volume_heating_water_storage_in_liter = (
            100  # default(volume = 230) leads to an error
        )

        return household_config

    @classmethod
    def get_scaled_default(
        cls,
        building_config: building.BuildingConfig,
        options: HouseholdMoreAdvancedHPDieselCarDHWStorageOptions = HouseholdMoreAdvancedHPDieselCarDHWStorageOptions(),
    ) -> "HouseholdMoreAdvancedHPDieselCarDHWStorageConfig":
        """Get scaled default HouseholdMoreAdvancedHPDieselCarConfig."""

        set_heating_threshold_outside_temperature_in_celsius: float = 16.0

        my_building_information = building.BuildingInformation(config=building_config)

        hds_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
            set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
            heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
            heating_reference_temperature_in_celsius=my_building_information.heating_reference_temperature_in_celsius,
        )
        my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
            config=hds_controller_config
        )
        household_config = HouseholdMoreAdvancedHPDieselCarDHWStorageConfig(
            building_type="blub",
            number_of_apartments=int(my_building_information.number_of_apartments),
            occupancy_config=loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
                building_name="BUI1",
                data_acquisition_mode=loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_UTSP,
                household=Households.CHR01_Couple_both_at_Work,
                energy_intensity=EnergyIntensityType.EnergySaving,
                result_dir_path=utils.HISIMPATH["utsp_results"],
                travel_route_set=TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance,
                transportation_device_set=TransportationDeviceSets.Bus_and_one_30_km_h_Car,
                charging_station_set=ChargingStationSets.Charging_At_Home_with_11_kW,
                name="UTSPConnector",
                profile_with_washing_machine_and_dishwasher=True,
                predictive_control=False,
                predictive=False,
            ),
            building_config=building_config,
            hds_controller_config=hds_controller_config,
            hds_config=(
                heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
                    water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
                    absolute_conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
                    heating_system=hds_controller_config.heating_system,
                )
            ),
            sh_controller_config=more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.get_default_space_heating_controller_config(
                heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type
            ),
            hp_config=more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.get_scaled_advanced_hp_lib(
                heating_load_of_building_in_watt=Quantity(
                    my_building_information.max_thermal_building_demand_in_watt, Watt
                ),
                heating_reference_temperature_in_celsius=Quantity(
                    my_building_information.heating_reference_temperature_in_celsius, Celsius
                ),
            ),
            simple_hot_water_storage_config=simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
                max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
                temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
                sizing_option=simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP,
            ),
            dhw_heatpump_controller_config=more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHWConfig.get_default_dhw_controller_config(),
            dhw_storage_config=simple_water_storage.SimpleDHWStorageConfig.get_default_simpledhwstorage_config(),
            car_config=generic_car.CarConfig.get_default_diesel_config(),
            electricity_meter_config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
        )

        # adjust HeatPump
        household_config.hp_config.group_id = 1  # use modulating heatpump as default
        household_config.sh_controller_config.mode = 2  # use heating and cooling as default
        household_config.hp_config.minimum_idle_time_in_seconds = Quantity(
            900, Seconds  # default value leads to switching on-off very often
        )
        household_config.hp_config.minimum_running_time_in_seconds = Quantity(
            900, Seconds  # default value leads to switching on-off very often
        )
        household_config.hp_config.with_domestic_hot_water_preparation = True

        # set same heating threshold
        household_config.hds_controller_config.set_heating_threshold_outside_temperature_in_celsius = (
            set_heating_threshold_outside_temperature_in_celsius
        )
        household_config.sh_controller_config.set_heating_threshold_outside_temperature_in_celsius = (
            set_heating_threshold_outside_temperature_in_celsius
        )

        household_config.hp_config.flow_temperature_in_celsius = Quantity(21, Celsius)  # Todo: check value

        return household_config


def setup_function(
    my_sim: Any,
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """System setup with more advanced hp and diesel car.

    This setup function emulates a household with some basic components. Here the residents have their
    electricity and heating needs covered by a the advanced heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Building
        - Electricity Meter
        - Advanced Heat Pump HPlib
        - Advanced Heat Pump HPlib Controller
        - Heat Distribution System
        - Heat Distribution System Controller
        - Simple Hot Water Storage

        - DHW (Heatpump, Heatpumpcontroller, Storage; copied from modular_example)
        - Car (Diesel)
    """
    if my_sim.my_module_config:
        my_config = HouseholdMoreAdvancedHPDieselCarDHWStorageConfig.load_from_json(my_sim.my_module_config)
    else:
        my_config = HouseholdMoreAdvancedHPDieselCarDHWStorageConfig.get_default()

    # Todo: save file leads to use of file in next run. File was just produced to check how it looks like
    # my_config_json = my_config.to_json()
    # with open(config_filename, "w", encoding="utf8") as system_config_file:
    #     system_config_file.write(my_config_json)

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

    # Build Building
    my_building = building.Building(
        config=my_config.building_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Distribution System
    my_heat_distribution = heat_distribution_system.HeatDistribution(
        my_simulation_parameters=my_simulation_parameters, config=my_config.hds_config
    )
    # Build Heat Pump Controller for space heating
    my_heatpump_controller_sh_config = my_config.sh_controller_config
    my_heatpump_controller_sh_config.name = "HeatPumpControllerSH"

    my_heatpump_controller_space_heating = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
            config=my_heatpump_controller_sh_config, my_simulation_parameters=my_simulation_parameters
        )
    )

    # Build Heat Pump Controller for dhw
    my_heatpump_controller_dhw_config = my_config.dhw_heatpump_controller_config

    my_heatpump_controller_dhw = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHW(
        config=my_heatpump_controller_dhw_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Heat Pump
    my_heatpump_config = my_config.hp_config
    my_heatpump_config.name = "HeatPumpHPLib"

    my_heatpump = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib(
        config=my_heatpump_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Water Storage
    my_hot_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_config.simple_hot_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    my_dhw_storage_config = my_config.dhw_storage_config
    my_dhw_storage_config.name = "DHWStorage"

    my_dhw_storage = simple_water_storage.SimpleDHWStorage(
        my_simulation_parameters=my_simulation_parameters, config=my_dhw_storage_config
    )

    # Build Diesel-Car(s)
    # get all available cars from occupancy
    my_car_information = generic_car.GenericCarInformation(my_occupancy_instance=my_occupancy)

    my_car_config = my_config.car_config
    my_car_config.name = "DieselCar"

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

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=my_config.electricity_meter_config,
    )

    # =================================================================================================================================
    # =================================================================================================================================
    # Connect Components
    my_building.connect_only_predefined_connections(my_weather)
    my_building.connect_only_predefined_connections(my_occupancy)

    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_distribution.component_name,
        my_heat_distribution.ThermalPowerDelivered,
    )

    #################################
    my_heat_distribution_controller.connect_only_predefined_connections(my_weather, my_building)

    my_heat_distribution.connect_only_predefined_connections(
        my_building, my_heat_distribution_controller, my_hot_water_storage
    )

    #################################
    my_heatpump.connect_only_predefined_connections(
        my_heatpump_controller_space_heating, my_heatpump_controller_dhw, my_weather, my_hot_water_storage
    )

    my_heatpump.connect_input(
        my_heatpump.TemperatureInputSecondary_DHW,
        my_dhw_storage.component_name,
        my_dhw_storage.WaterMeanTemperatureInStorage,
    )

    # Verknüpfung mit Luft als Umgebungswärmequelle
    if my_heatpump.parameters["Group"].iloc[0] == 1.0 or my_heatpump.parameters["Group"].iloc[0] == 4.0:
        my_heatpump.connect_input(
            my_heatpump.TemperatureInputPrimary,
            my_weather.component_name,
            my_weather.DailyAverageOutsideTemperatures,
        )
    else:
        raise KeyError(
            "Wasser oder Sole als primäres Wärmeträgermedium muss über extra Wärmenetz-Modell noch bereitgestellt werden"
        )

        # todo: Water and Brine Connection

    my_heatpump_controller_space_heating.connect_only_predefined_connections(
        my_heat_distribution_controller, my_weather, my_hot_water_storage
    )

    my_heatpump_controller_dhw.connect_input(
        my_heatpump_controller_dhw.WaterTemperatureInputFromDHWStorage,
        my_dhw_storage.component_name,
        my_dhw_storage.WaterMeanTemperatureInStorage,
    )

    #################################
    my_hot_water_storage.connect_input(
        my_hot_water_storage.WaterTemperatureFromHeatDistribution,
        my_heat_distribution.component_name,
        my_heat_distribution.WaterTemperatureOutput,
    )

    my_hot_water_storage.connect_input(
        my_hot_water_storage.WaterTemperatureFromHeatGenerator,
        my_heatpump.component_name,
        my_heatpump.TemperatureOutputSH,
    )

    my_hot_water_storage.connect_input(
        my_hot_water_storage.WaterMassFlowRateFromHeatGenerator,
        my_heatpump.component_name,
        my_heatpump.MassFlowOutputSH,
    )

    #################################

    my_dhw_storage.connect_only_predefined_connections(my_occupancy, my_heatpump)

    ################################

    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_occupancy.component_name,
        source_component_output=my_occupancy.ElectricalPowerConsumption,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )

    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_heatpump.component_name,
        source_component_output=my_heatpump.ElectricalInputPowerTotal,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[
            lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
        ],
        source_weight=999,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_building)
    my_sim.add_component(my_heatpump)
    my_sim.add_component(my_heatpump_controller_space_heating)
    my_sim.add_component(my_heatpump_controller_dhw)
    my_sim.add_component(my_heat_distribution)
    my_sim.add_component(my_heat_distribution_controller)
    my_sim.add_component(my_hot_water_storage)
    my_sim.add_component(my_dhw_storage)
    my_sim.add_component(my_electricity_meter)
    for my_car in my_cars:
        my_sim.add_component(my_car)
