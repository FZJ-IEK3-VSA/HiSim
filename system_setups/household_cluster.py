"""  Basic household new system setup. """

# clean

from typing import Optional, Any, Union, List
import re
import os
from enum import Enum
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    Households,
)
from utspclient.helpers.lpgpythonbindings import JsonReference
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import (
    advanced_heat_pump_hplib,
    advanced_battery_bslib,
    controller_l2_energy_management_system,
    simple_hot_water_storage,
    heat_distribution_system,
    generic_heat_pump_modular,
    generic_hot_water_storage_modular,
    controller_l1_heatpump,
    electricity_meter,
    advanced_ev_battery_bslib,
    controller_l1_generic_ev_charge,
    generic_car,
    controller_l1_generic_gas_heater,
    generic_gas_heater,
    generic_heat_source,
    gas_meter,
)
from hisim.component import ConfigBase
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
from hisim.postprocessingoptions import PostProcessingOptions
from hisim import loadtypes as lt
from hisim import log
from hisim.units import Quantity, Celsius, Watt

__authors__ = "Katharina Rieck"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


class HeatingSystemType(Enum):

    """Enum class for heating system types."""

    HEAT_PUMP = "Heat Pump"
    GAS_HEATER = "Gas Heater"


@dataclass_json
@dataclass
class BuildingPVWeatherConfig(ConfigBase):

    """Configuration for BuildingPv."""

    name: str
    building_id: str
    pv_azimuth: float
    pv_tilt: float
    pv_rooftop_capacity_in_kilowatt: Optional[float]
    share_of_maximum_pv_potential: float
    building_code: str
    conditioned_floor_area_in_m2: float
    number_of_dwellings_per_building: int
    norm_heating_load_in_kilowatt: Optional[float]
    lpg_households: List[str]
    weather_location: str

    @classmethod
    def get_default(cls):
        """Get default BuildingPVConfig."""

        return BuildingPVWeatherConfig(
            name="BuildingPVConfig",
            building_id="default_building",
            pv_azimuth=180,
            pv_tilt=30,
            pv_rooftop_capacity_in_kilowatt=None,
            share_of_maximum_pv_potential=1,
            building_code="DE.N.SFH.05.Gen.ReEx.001.002",
            conditioned_floor_area_in_m2=121.2,
            number_of_dwellings_per_building=1,
            norm_heating_load_in_kilowatt=None,
            lpg_households=["CHR01_Couple_both_at_Work"],
            weather_location="AACHEN",
        )


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Household system setup.

    This setup function emulates an household including the following components:

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump
        - Heat Pump Controller
        - Heat Distribution System
        - Heat Distribution Controller
        - Heat Water Storage
        - Battery
        - Energy Management System
        - Domestic water heat pump
        - Electricity Meter
        - Electric Vehicles (including car batteries and car battery controllers)
    """

    # =================================================================================================================================
    # Set System Parameters from Config

    # household-pv-config
    config_filename = my_sim.my_module_config

    my_config: BuildingPVWeatherConfig
    if isinstance(config_filename, str) and os.path.exists(config_filename.rstrip("\r")):
        with open(config_filename.rstrip("\r"), encoding="unicode_escape") as system_config_file:
            my_config = BuildingPVWeatherConfig.from_json(system_config_file.read())  # type: ignore

        log.information(f"Read system config from {config_filename}")
        log.information("Config values: " + f"{my_config.to_dict}" + "\n")
    else:
        my_config = BuildingPVWeatherConfig.get_default()
        log.information("No module config path from the simulator was given. Use default config.")
        my_sim.my_module_config = my_config.to_dict()

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60 * 15

    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
        )
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS_AND_WRITE_TO_REPORT)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_ALL_KPIS_TO_JSON)
        # my_simulation_parameters.post_processing_options.append(PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER)
        # my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.EXPORT_TO_CSV)
        my_simulation_parameters.logging_level = 4

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # =================================================================================================================================
    # Set System Parameters

    # Set heating systems for space heating and domestic hot water
    space_heating_system = HeatingSystemType.GAS_HEATER
    domestic_hot_water_heating_system = HeatingSystemType.GAS_HEATER
    # Set Heat Pump Controller
    hp_controller_mode = 2  # mode 1 for heating/off and mode 2 for heating/cooling/off
    heating_reference_temperature_in_celsius = -7.0
    # Set gas meter (default is False, is set true when gas heaters are used)
    use_gas_meter: bool = False

    # Set Weather
    weather_location = my_config.weather_location

    # Set Photovoltaic System
    azimuth = my_config.pv_azimuth
    tilt = my_config.pv_tilt
    if my_config.pv_rooftop_capacity_in_kilowatt is not None:
        pv_power_in_watt = my_config.pv_rooftop_capacity_in_kilowatt * 1000
    else:
        pv_power_in_watt = None
    share_of_maximum_pv_potential = 1  # my_config.share_of_maximum_pv_potential

    # Set Building (scale building according to total base area and not absolute floor area)
    building_code = my_config.building_code
    total_base_area_in_m2 = None
    absolute_conditioned_floor_area_in_m2 = my_config.conditioned_floor_area_in_m2
    number_of_apartments = my_config.number_of_dwellings_per_building
    if my_config.norm_heating_load_in_kilowatt is not None:
        max_thermal_building_demand_in_watt = my_config.norm_heating_load_in_kilowatt * 1000
    else:
        max_thermal_building_demand_in_watt = None

    # Set Occupancy
    # try to get profiles from cluster directory
    cache_dir_path: Optional[str] = "/fast/home/k-rieck/lpg-utsp-data"
    if cache_dir_path is not None and os.path.exists(cache_dir_path):
        pass
    # else use default specific cache_dir_path
    else:
        cache_dir_path = None

    # get household attribute jsonreferences from list of strings
    lpg_households: Union[JsonReference, List[JsonReference]]
    if isinstance(my_config.lpg_households, List):
        if len(my_config.lpg_households) == 1:
            lpg_households = getattr(Households, my_config.lpg_households[0])
        elif len(my_config.lpg_households) > 1:
            lpg_households = []
            for household_string in my_config.lpg_households:
                if hasattr(Households, household_string):
                    lpg_household = getattr(Households, household_string)
                    lpg_households.append(lpg_household)
                    print(lpg_household)
        else:
            raise ValueError("Config list with lpg household is empty.")
    else:
        raise TypeError(f"Type {type(my_config.lpg_households)} is incompatible. Should be List[str].")

    # Set Electric Vehicle
    charging_station_set = ChargingStationSets.Charging_At_Home_with_11_kW
    charging_power = float((charging_station_set.Name or "").split("with ")[1].split(" kW")[0])

    # =================================================================================================================================
    # Build Basic Components
    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home(
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
        max_thermal_building_demand_in_watt=max_thermal_building_demand_in_watt,
    )
    my_building_config.building_code = building_code
    my_building_config.total_base_area_in_m2 = total_base_area_in_m2
    my_building_config.absolute_conditioned_floor_area_in_m2 = absolute_conditioned_floor_area_in_m2
    my_building_config.number_of_apartments = number_of_apartments
    my_building_config.enable_opening_windows = True
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    # Add to simulator
    my_sim.add_component(my_building, connect_automatically=True)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy_config.data_acquisition_mode = loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_UTSP
    my_occupancy_config.household = lpg_households
    my_occupancy_config.cache_dir_path = cache_dir_path

    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )
    # Add to simulator
    my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather_location)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)
    # Add to simulator
    my_sim.add_component(my_weather)

    # Build PV
    if pv_power_in_watt is None:
        my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
            rooftop_area_in_m2=my_building_information.scaled_rooftop_area_in_m2,
            share_of_maximum_pv_potential=share_of_maximum_pv_potential,
            location=weather_location,
        )
    else:
        my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_default_pv_system(
            power_in_watt=pv_power_in_watt,
            share_of_maximum_pv_potential=share_of_maximum_pv_potential,
            location=weather_location,
        )

    my_photovoltaic_system_config.azimuth = azimuth
    my_photovoltaic_system_config.tilt = tilt

    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config, my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)

    # Build Heat Distribution Controller
    my_heat_distribution_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
        set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
        set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
    )
    # my_heat_distribution_controller_config.heating_system = heat_distribution_system.HeatDistributionSystemType.RADIATOR

    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters, config=my_heat_distribution_controller_config,
    )
    my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
        config=my_heat_distribution_controller_config
    )
    # Add to simulator
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)

    if space_heating_system == HeatingSystemType.HEAT_PUMP:
        # Set sizing option for Hot water Storage
        sizing_option = simple_hot_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP

        # Build Heat Pump Controller
        my_heat_pump_controller_config = advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config.get_default_generic_heat_pump_controller_config(
            heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type
        )
        my_heat_pump_controller_config.mode = hp_controller_mode

        my_heat_pump_controller = advanced_heat_pump_hplib.HeatPumpHplibController(
            config=my_heat_pump_controller_config, my_simulation_parameters=my_simulation_parameters,
        )
        # Add to simulator
        my_sim.add_component(my_heat_pump_controller, connect_automatically=True)

        # Build Heat Pump
        my_heat_pump_config = advanced_heat_pump_hplib.HeatPumpHplibConfig.get_scaled_advanced_hp_lib(
            heating_load_of_building_in_watt=Quantity(
                my_building_information.max_thermal_building_demand_in_watt, Watt
            ),
            heating_reference_temperature_in_celsius=Quantity(heating_reference_temperature_in_celsius, Celsius),
        )

        my_heat_pump = advanced_heat_pump_hplib.HeatPumpHplib(
            config=my_heat_pump_config, my_simulation_parameters=my_simulation_parameters,
        )
        # Add to simulator
        my_sim.add_component(my_heat_pump, connect_automatically=True)

    elif space_heating_system == HeatingSystemType.GAS_HEATER:
        # Set sizing option for Hot water Storage
        sizing_option = simple_hot_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GAS_HEATER
        # Set gas meter
        use_gas_meter = True

        # Build Gas Heater Controller
        my_gas_heater_controller_config = controller_l1_generic_gas_heater.GenericGasHeaterControllerL1Config.get_scaled_generic_gas_heater_controller_config(
            heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt
        )
        my_gas_heater_controller = controller_l1_generic_gas_heater.GenericGasHeaterControllerL1(
            my_simulation_parameters=my_simulation_parameters, config=my_gas_heater_controller_config,
        )
        my_sim.add_component(my_gas_heater_controller, connect_automatically=True)

        # Build Gas heater For Space Heating
        my_gas_heater_config = generic_gas_heater.GenericGasHeaterConfig.get_scaled_gasheater_config(
            heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt
        )
        my_gas_heater = generic_gas_heater.GasHeater(
            config=my_gas_heater_config, my_simulation_parameters=my_simulation_parameters,
        )
        my_sim.add_component(my_gas_heater, connect_automatically=True)
    else:
        raise ValueError(f"Space heating system {space_heating_system} not recognized.")

    if domestic_hot_water_heating_system == HeatingSystemType.HEAT_PUMP:

        # Build DHW (this is taken from household_3_advanced_hp_diesel-car_pv_battery.py)
        my_dhw_heatpump_config = generic_heat_pump_modular.HeatPumpConfig.get_scaled_waterheating_to_number_of_apartments(
            number_of_apartments=my_building_information.number_of_apartments, default_power_in_watt=6000,
        )
        my_dhw_heatpump_controller_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
            name="DHWHeatpumpController"
        )
        my_dhw_storage_config = generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(
            number_of_apartments=my_building_information.number_of_apartments, default_volume_in_liter=450,
        )
        my_dhw_storage_config.compute_default_cycle(
            temperature_difference_in_kelvin=my_dhw_heatpump_controller_config.t_max_heating_in_celsius
            - my_dhw_heatpump_controller_config.t_min_heating_in_celsius
        )
        my_domnestic_hot_water_storage = generic_hot_water_storage_modular.HotWaterStorage(
            my_simulation_parameters=my_simulation_parameters, config=my_dhw_storage_config
        )
        my_domnestic_hot_water_heatpump_controller = controller_l1_heatpump.L1HeatPumpController(
            my_simulation_parameters=my_simulation_parameters, config=my_dhw_heatpump_controller_config,
        )
        my_domnestic_hot_water_heatpump = generic_heat_pump_modular.ModularHeatPump(
            config=my_dhw_heatpump_config, my_simulation_parameters=my_simulation_parameters
        )
        # Add to simulator
        my_sim.add_component(my_domnestic_hot_water_storage, connect_automatically=True)
        my_sim.add_component(my_domnestic_hot_water_heatpump_controller, connect_automatically=True)
        my_sim.add_component(my_domnestic_hot_water_heatpump, connect_automatically=True)

    elif domestic_hot_water_heating_system == HeatingSystemType.GAS_HEATER:
        # Set gas meter
        use_gas_meter = True
        # Build Gas Heater for DHW
        my_gas_heater_for_dhw_config = generic_heat_source.HeatSourceConfig.get_default_config_waterheating_with_gas(
            max_warm_water_demand_in_liter=my_occupancy.max_hot_water_demand,
            scaling_factor_according_to_number_of_apartments=my_occupancy.scaling_factor_according_to_number_of_apartments,
            seconds_per_timestep=seconds_per_timestep,
        )
        my_gas_heater_controller_l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
            "DHW" + lt.HeatingSystems.GAS_HEATING.value
        )
        my_boiler_config = generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(
            number_of_apartments=my_building_information.number_of_apartments
        )
        my_boiler_config.compute_default_cycle(
            temperature_difference_in_kelvin=my_gas_heater_controller_l1_config.t_max_heating_in_celsius
            - my_gas_heater_controller_l1_config.t_min_heating_in_celsius
        )

        my_boiler_for_dhw = generic_hot_water_storage_modular.HotWaterStorage(
            my_simulation_parameters=my_simulation_parameters, config=my_boiler_config
        )

        my_heater_controller_l1_for_dhw = controller_l1_heatpump.L1HeatPumpController(
            my_simulation_parameters=my_simulation_parameters, config=my_gas_heater_controller_l1_config
        )

        my_gas_heater_for_dhw = generic_heat_source.HeatSource(
            config=my_gas_heater_for_dhw_config, my_simulation_parameters=my_simulation_parameters
        )
        my_sim.add_component(my_gas_heater_for_dhw, connect_automatically=True)
        my_sim.add_component(my_boiler_for_dhw, connect_automatically=True)
        my_sim.add_component(my_heater_controller_l1_for_dhw, connect_automatically=True)
    else:
        raise ValueError(f"Heating system for domestic hot water {domestic_hot_water_heating_system} not recognized.")

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_hot_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
        max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
        temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
        sizing_option=sizing_option,
    )
    my_simple_hot_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config, my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)

    # Build Heat Distribution System
    my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
        water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
        absolute_conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config, my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_heat_distribution_system, connect_automatically=True)

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )
    if use_gas_meter:
        # Build Gas Meter
        my_gas_meter = gas_meter.GasMeter(
            my_simulation_parameters=my_simulation_parameters,
            config=gas_meter.GasMeterConfig.get_gas_meter_default_config(),
        )
        my_sim.add_component(my_gas_meter, connect_automatically=True)

    # Build Electric Vehicle Configs and Car Battery Configs
    my_car_config = generic_car.CarConfig.get_default_ev_config()
    my_car_battery_config = advanced_ev_battery_bslib.CarBatteryConfig.get_default_config()
    my_car_battery_controller_config = controller_l1_generic_ev_charge.ChargingStationConfig.get_default_config(
        charging_station_set=charging_station_set
    )
    # set car config name
    my_car_config.name = "ElectricCar"
    # set charging power from battery and controller to same value, to reduce error in simulation of battery
    my_car_battery_config.p_inv_custom = charging_power * 1e3
    # lower threshold for soc of car battery in clever case. This enables more surplus charging. Surplus control of car
    my_car_battery_controller_config.battery_set = 0.4

    # Build Electric Vehicles
    my_car_information = generic_car.GenericCarInformation(my_occupancy_instance=my_occupancy)
    my_cars: List[generic_car.Car] = []
    my_car_batteries: List[advanced_ev_battery_bslib.CarBattery] = []
    my_car_battery_controllers: List[controller_l1_generic_ev_charge.L1Controller] = []
    # iterate over all cars
    car_number = 1
    for car_information_dict in my_car_information.data_dict_for_car_component.values():
        # Build Electric Vehicles
        my_car_config.name = f"ElectricCar_{car_number}"
        my_car = generic_car.Car(
            my_simulation_parameters=my_simulation_parameters,
            config=my_car_config,
            data_dict_with_car_information=car_information_dict,
        )
        my_cars.append(my_car)
        # Build Electric Vehicle Batteries
        my_car_battery_config.source_weight = my_car.config.source_weight
        my_car_battery_config.name = f"CarBattery_{car_number}"
        my_car_battery = advanced_ev_battery_bslib.CarBattery(
            my_simulation_parameters=my_simulation_parameters, config=my_car_battery_config,
        )
        my_car_batteries.append(my_car_battery)
        # Build Electric Vehicle Battery Controller
        my_car_battery_controller_config.source_weight = my_car.config.source_weight
        my_car_battery_controller_config.name = f"L1EVChargeControl_{car_number}"

        my_car_battery_controller = controller_l1_generic_ev_charge.L1Controller(
            my_simulation_parameters=my_simulation_parameters, config=my_car_battery_controller_config,
        )
        my_car_battery_controllers.append(my_car_battery_controller)
        car_number += 1

    # Connect Electric Vehicles and Car Batteries
    zip_car_battery_controller_lists = zip(my_cars, my_car_batteries, my_car_battery_controllers)
    for car, car_battery, car_battery_controller in zip_car_battery_controller_lists:
        car_battery_controller.connect_only_predefined_connections(car)
        car_battery_controller.connect_only_predefined_connections(car_battery)
        car_battery.connect_only_predefined_connections(car_battery_controller)

    # use ems and battery only when PV is used
    if share_of_maximum_pv_potential != 0:
        # Build EMS
        my_electricity_controller_config = controller_l2_energy_management_system.EMSConfig.get_default_config_ems()

        my_electricity_controller = controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
            my_simulation_parameters=my_simulation_parameters, config=my_electricity_controller_config,
        )

        # Build Battery
        my_advanced_battery_config = advanced_battery_bslib.BatteryConfig.get_scaled_battery(
            total_pv_power_in_watt_peak=my_photovoltaic_system_config.power_in_watt
        )
        my_advanced_battery = advanced_battery_bslib.Battery(
            my_simulation_parameters=my_simulation_parameters, config=my_advanced_battery_config,
        )

        # -----------------------------------------------------------------------------------------------------------------
        # Add outputs to EMS
        loading_power_input_for_battery_in_watt = my_electricity_controller.add_component_output(
            source_output_name="LoadingPowerInputForBattery_",
            source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
            source_weight=4,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
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
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION],
            source_weight=999,
        )

        # -----------------------------------------------------------------------------------------------------------------
        # Connect Electric Vehicle and Car Battery with EMS for surplus control
        for car, car_battery, car_battery_controller in zip_car_battery_controller_lists:

            my_electricity_controller.add_component_input_and_connect(
                source_object_name=car_battery_controller.component_name,
                source_component_output=car_battery_controller.BatteryChargingPowerToEMS,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.CAR_BATTERY, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
                source_weight=5,
            )

            electricity_target = my_electricity_controller.add_component_output(
                source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
                source_tags=[lt.ComponentType.CAR_BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
                source_weight=5,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                output_description="Target Electricity for EV Battery Controller. ",
            )

            car_battery_controller.connect_dynamic_input(
                input_fieldname=controller_l1_generic_ev_charge.L1Controller.ElectricityTarget,
                src_object=electricity_target,
            )

        # =================================================================================================================================
        # Add Remaining Components to Simulation Parameters

        my_sim.add_component(my_electricity_meter)
        my_sim.add_component(my_advanced_battery)
        my_sim.add_component(my_electricity_controller, connect_automatically=True)

    # when no PV is used, connect electricty meter automatically
    else:
        my_sim.add_component(my_electricity_meter, connect_automatically=True)

    # Connect Electric Vehicles and Car Batteries
    for car in my_cars:
        my_sim.add_component(car)
    for car_battery in my_car_batteries:
        my_sim.add_component(car_battery)
    for car_battery_controller in my_car_battery_controllers:
        my_sim.add_component(car_battery_controller)

    # Set Results Path
    # if config_filename is given, get hash number and sampling mode for result path
    if config_filename is not None:
        config_filename_splitted = config_filename.split("/")
        try:
            scenario_hash_string = re.findall(r"\-?\d+", config_filename_splitted[-1])[0]
        except Exception:
            scenario_hash_string = "-"
        try:
            further_result_folder_description = config_filename_splitted[-2]
        except Exception:
            further_result_folder_description = "-"

        sorting_option = SortingOptionEnum.MASS_SIMULATION_WITH_HASH_ENUMERATION

    # if config_filename is not given, make result path with index enumeration
    else:
        scenario_hash_string = "default_scenario"
        sorting_option = SortingOptionEnum.MASS_SIMULATION_WITH_INDEX_ENUMERATION
        further_result_folder_description = "default_config"

    SingletonSimRepository().set_entry(
        key=SingletonDictKeyEnum.RESULT_SCENARIO_NAME, entry=f"{scenario_hash_string}",
    )

    ResultPathProviderSingleton().set_important_result_path_information(
        module_directory=my_sim.module_directory,  # "/storage_cluster/projects/2024_waage/01_hisim_results"
        model_name=my_sim.module_filename,
        further_result_folder_description=os.path.join(
            *[
                further_result_folder_description,
                f"PV-{share_of_maximum_pv_potential}"
                f"-SH-{space_heating_system.name}-DHW-{domestic_hot_water_heating_system.name}",
                f"weather-location-{weather_location}",
            ]
        ),
        variant_name="_",
        scenario_hash_string=scenario_hash_string,
        sorting_option=sorting_option,
    )
