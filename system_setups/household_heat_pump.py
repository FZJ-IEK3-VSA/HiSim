"""  Household system setup with advanced heat pump. """

from dataclasses import dataclass
import os
import re
from typing import List, Optional, Any
from dataclasses_json import dataclass_json
from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    Households,
    TransportationDeviceSets,
    TravelRouteSets,
    EnergyIntensityType,
)
from hisim.system_setup_configuration import SystemSetupConfigBase
from hisim.simulator import SimulationParameters
from hisim.component import DisplayConfig
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
from hisim.components import generic_pv_system
from hisim.components import electricity_meter
from hisim.components import controller_l2_energy_management_system, advanced_battery_bslib
from hisim import utils, loadtypes
from hisim.units import Quantity, Watt, Celsius
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum

__authors__ = ["Katharina Rieck", "Kevin Knosala", "Markus Blasberg"]
__copyright__ = "Copyright 2023, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Katharina Rieck", "Kevin Knosala"
__status__ = "development"


@dataclass
class HouseholdHeatPumpOptions:
    """Set options for the system setup."""

    photovoltaic: bool = False
    energy_management_and_battery: bool = False
    diesel_car: bool = False
    heat_distribution_system: int = 2  # 2 = Floorheating, 1 = Radiator
    heat_pump_controller_mode: int = 2  # 2 = cooling possible, 1 = cooling not possible


@dataclass_json
@dataclass
class HouseholdHeatPumpConfig(SystemSetupConfigBase):
    """Configuration for household with advanced heat pump."""

    building_type: str
    number_of_apartments: int

    options: HouseholdHeatPumpOptions
    building_config: building.BuildingConfig

    occupancy_config: loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig
    hds_controller_config: heat_distribution_system.HeatDistributionControllerConfig
    hds_config: heat_distribution_system.HeatDistributionConfig
    hp_controller_config: advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config
    hp_config: advanced_heat_pump_hplib.HeatPumpHplibConfig
    simple_hot_water_storage_config: simple_water_storage.SimpleHotWaterStorageConfig
    dhw_heatpump_config: generic_heat_pump_modular.HeatPumpConfig
    dhw_heatpump_controller_config: controller_l1_heatpump.L1HeatPumpConfig
    dhw_storage_config: generic_hot_water_storage_modular.StorageConfig
    electricity_meter_config: electricity_meter.ElectricityMeterConfig
    weather_location: str
    # heating_system: int
    # heat_pump_controller_mode: int

    # Optional components
    pv_config: Optional[generic_pv_system.PVSystemConfig]
    car_config: Optional[generic_car.CarConfig]

    @classmethod
    def get_default_options(cls):
        """Get default options."""
        return HouseholdHeatPumpOptions()

    @classmethod
    def get_default(cls) -> "HouseholdHeatPumpConfig":
        """Get default HouseholdHeatPumpConfig."""
        building_config = building.BuildingConfig.get_default_german_single_family_home()
        household_config = cls.get_scaled_default(building_config, options=HouseholdHeatPumpOptions())
        return household_config

    @classmethod
    def get_scaled_default(
        cls, building_config: building.BuildingConfig, options: HouseholdHeatPumpOptions = HouseholdHeatPumpOptions()
    ) -> "HouseholdHeatPumpConfig":
        """Get scaled HouseholdHeatPumpConfig.

        - Simulation Parameters
        - Components
            - Occupancy (Residents' Demands)
            - Weather
            - Building
            - Electricity Meter
            - Advanced Heat Pump
            - Advanced Heat Pump Controller
            - Heat Distribution System
            - Heat Distribution System Controller
            - Simple Hot Water Storage
            - Car (Diesel or EV)
        """

        set_heating_threshold_outside_temperature_in_celsius: Quantity[float, Celsius] = Quantity(16.0, Celsius)
        weather_location = "AACHEN"
        # heating_system = 2
        # heat_pump_controller_mode = 2

        my_building_information = building.BuildingInformation(config=building_config)

        hds_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
            set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
            heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
            heating_reference_temperature_in_celsius=my_building_information.heating_reference_temperature_in_celsius,
            heating_system=options.heat_distribution_system,
        )
        my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
            config=hds_controller_config
        )

        # initialize HouseholdHeatPumpConfig
        household_config = HouseholdHeatPumpConfig(
            building_type="residential",
            number_of_apartments=my_building_information.number_of_apartments,
            # heating_system=heating_system,
            # heat_pump_controller_mode=heat_pump_controller_mode,
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
            pv_config=generic_pv_system.PVSystemConfig.get_scaled_pv_system(
                rooftop_area_in_m2=my_building_information.roof_area_in_m2, location=weather_location
            )
            if options.photovoltaic
            else None,
            options=options,
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
                heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type,
                mode=options.heat_pump_controller_mode,
            ),
            hp_config=(
                advanced_heat_pump_hplib.HeatPumpHplibConfig.get_scaled_advanced_hp_lib(
                    heating_load_of_building_in_watt=Quantity(
                        my_building_information.max_thermal_building_demand_in_watt, Watt
                    ),
                    heating_reference_temperature_in_celsius=Quantity(
                        my_building_information.heating_reference_temperature_in_celsius, Celsius
                    ),
                )
            ),
            simple_hot_water_storage_config=(
                simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
                    max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
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
            car_config=generic_car.CarConfig.get_default_diesel_config() if options.diesel_car else None,
            electricity_meter_config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
            weather_location=weather_location,
        )

        # set same heating threshold
        household_config.hds_controller_config.set_heating_threshold_outside_temperature_in_celsius = (
            set_heating_threshold_outside_temperature_in_celsius.value
        )
        household_config.hp_controller_config.set_heating_threshold_outside_temperature_in_celsius = (
            set_heating_threshold_outside_temperature_in_celsius.value
        )

        return household_config


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Generates a household with advanced heat pump."""

    """
    Load system setup parameters from json or take defaults.
    """
    if my_sim.my_module_config:
        my_config = HouseholdHeatPumpConfig.load_from_json(my_sim.my_module_config)
    else:
        my_config = HouseholdHeatPumpConfig.get_default()

    """
    Set simulation parameters
    """
    year = 2021
    seconds_per_timestep = 60
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )

    my_sim.set_simulation_parameters(my_simulation_parameters)

    """
    Build system
    """
    # Heat Distribution System Controller
    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        config=my_config.hds_controller_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Occupancy
    my_occupancy_config = my_config.occupancy_config
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Weather
    my_weather = weather.Weather(
        config=weather.WeatherConfig.get_default(location_entry=my_config.weather_location),
        my_simulation_parameters=my_simulation_parameters,
        my_display_config=DisplayConfig.show("Wetter"),
    )

    # Building
    my_building = building.Building(
        config=my_config.building_config,
        my_simulation_parameters=my_simulation_parameters,
        my_display_config=DisplayConfig.show("Gebäude"),
    )

    # Advanced Heat Pump Controller
    my_heat_pump_controller = advanced_heat_pump_hplib.HeatPumpHplibController(
        config=my_config.hp_controller_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Advanced Heat Pump
    my_heat_pump = advanced_heat_pump_hplib.HeatPumpHplib(
        config=my_config.hp_config,
        my_simulation_parameters=my_simulation_parameters,
        my_display_config=DisplayConfig.show("Wärmepumpe"),
    )

    # Heat Distribution System
    my_heat_distribution = heat_distribution_system.HeatDistribution(
        my_simulation_parameters=my_simulation_parameters, config=my_config.hds_config
    )

    # Heat Water Storage
    my_simple_hot_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_config.simple_hot_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
        my_display_config=DisplayConfig.show("Wärmespeicher"),
    )

    # DHW
    my_dhw_heatpump_config = my_config.dhw_heatpump_config
    my_dhw_heatpump_controller_config = my_config.dhw_heatpump_controller_config
    my_dhw_storage_config = my_config.dhw_storage_config
    my_dhw_storage_config.name = "DHWStorage"
    my_dhw_storage_config.compute_default_cycle(
        temperature_difference_in_kelvin=my_dhw_heatpump_controller_config.t_max_heating_in_celsius
        - my_dhw_heatpump_controller_config.t_min_heating_in_celsius
    )
    my_domestic_hot_water_storage = generic_hot_water_storage_modular.HotWaterStorage(
        my_simulation_parameters=my_simulation_parameters,
        config=my_dhw_storage_config,
        my_display_config=DisplayConfig.show("Warmwasserspeicher"),
    )
    my_domestic_hot_water_heatpump_controller = controller_l1_heatpump.L1HeatPumpController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_dhw_heatpump_controller_config,
    )
    my_domestic_hot_water_heatpump = generic_heat_pump_modular.ModularHeatPump(
        config=my_dhw_heatpump_config,
        my_simulation_parameters=my_simulation_parameters,
        my_display_config=DisplayConfig.show("Warmwasser-Wärmepumpe"),
    )

    if my_config.car_config:
        # Diesel-Car(s)
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
                    my_display_config=DisplayConfig.show(f"Diesel-PKW {idx}"),
                )
            )
    else:
        my_cars = []

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=my_config.electricity_meter_config,
        my_display_config=DisplayConfig.show("Stromzähler"),
    )

    # Initialize PV
    if my_config.options.photovoltaic and my_config.pv_config is not None:

        # Build Photovoltaic
        my_photovoltaic_system = generic_pv_system.PVSystem(
            config=my_config.pv_config,
            my_simulation_parameters=my_simulation_parameters,
            my_display_config=DisplayConfig.show("Photovoltaik"),
        )
        # Initialize EMS and battery
        if my_config.options.energy_management_and_battery:
            # Build EMS
            my_electricity_controller_config = controller_l2_energy_management_system.EMSConfig.get_default_config_ems()
            my_electricity_controller = controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
                my_simulation_parameters=my_simulation_parameters,
                config=my_electricity_controller_config,
            )

            # Build Battery
            my_advanced_battery_config = advanced_battery_bslib.BatteryConfig.get_scaled_battery(
                total_pv_power_in_watt_peak=my_config.pv_config.power_in_watt
            )
            my_advanced_battery = advanced_battery_bslib.Battery(
                my_simulation_parameters=my_simulation_parameters,
                config=my_advanced_battery_config,
            )

            # -----------------------------------------------------------------------------------------------------------------
            # Add outputs to EMS
            loading_power_input_for_battery_in_watt = my_electricity_controller.add_component_output(
                source_output_name="LoadingPowerInputForBattery_",
                source_tags=[loadtypes.ComponentType.BATTERY, loadtypes.InandOutputType.ELECTRICITY_TARGET],
                source_weight=4,
                source_load_type=loadtypes.LoadTypes.ELECTRICITY,
                source_unit=loadtypes.Units.WATT,
                output_description="Target electricity for Battery Control. ",
            )

            # -----------------------------------------------------------------------------------------------------------------
            # Connect Battery
            my_advanced_battery.connect_dynamic_input(
                input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput,
                src_object=loading_power_input_for_battery_in_watt,
            )

            # -----------------------------------------------------------------------------------------------------------------
            # Connect Electricity Meter
            my_electricity_meter.add_component_input_and_connect(
                source_object_name=my_electricity_controller.component_name,
                source_component_output=my_electricity_controller.TotalElectricityToOrFromGrid,
                source_load_type=loadtypes.LoadTypes.ELECTRICITY,
                source_unit=loadtypes.Units.WATT,
                source_tags=[loadtypes.InandOutputType.ELECTRICITY_PRODUCTION],
                source_weight=999,
            )

            # =================================================================================================================================
            # Add Remaining Components to Simulation Parameters

            my_sim.add_component(my_electricity_meter)
            my_sim.add_component(my_advanced_battery)
            my_sim.add_component(my_electricity_controller, connect_automatically=True)
            my_sim.add_component(my_photovoltaic_system, connect_automatically=True)

        # when no EMS and battery is used, connect only PV and Electricity Meter automatically
        else:
            my_sim.add_component(my_photovoltaic_system, connect_automatically=True)
            my_sim.add_component(my_electricity_meter, connect_automatically=True)

    # when no PV is used, connect electricty meter automatically
    else:
        my_sim.add_component(my_electricity_meter, connect_automatically=True)

    # =================================================================================================================================
    # Add Components to Simulation Parameters
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_building, connect_automatically=True)
    my_sim.add_component(my_heat_pump, connect_automatically=True)
    my_sim.add_component(my_heat_pump_controller, connect_automatically=True)
    my_sim.add_component(my_heat_distribution, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)
    my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_domestic_hot_water_storage, connect_automatically=True)
    my_sim.add_component(my_domestic_hot_water_heatpump_controller, connect_automatically=True)
    my_sim.add_component(my_domestic_hot_water_heatpump, connect_automatically=True)

    if my_config.options.diesel_car:
        for my_car in my_cars:
            my_sim.add_component(my_car)

    # Set result directory, this is useful when you run the system setup with different module configurations
    try:
        scenario_hash_string = re.findall(r"\-?\d+", my_sim.my_module_config)[-1]
        sorting_option = SortingOptionEnum.MASS_SIMULATION_WITH_HASH_ENUMERATION
    except Exception:
        scenario_hash_string = ""
        sorting_option = SortingOptionEnum.MASS_SIMULATION_WITH_INDEX_ENUMERATION
    ResultPathProviderSingleton().set_important_result_path_information(
        module_directory=my_sim.module_directory,
        model_name=my_sim.module_filename,
        further_result_folder_description=os.path.join(
            *[
                f"location-{my_config.weather_location}",
                f"hds-type-{my_config.hds_controller_config.heating_system}",
                f"PV-{my_config.options.photovoltaic}-EMS-and-Batt-{my_config.options.energy_management_and_battery}-hp-mode-{my_config.hp_controller_config.mode}",
            ]
        ),
        variant_name="_",
        sorting_option=sorting_option,
        scenario_hash_string=scenario_hash_string,
    )
