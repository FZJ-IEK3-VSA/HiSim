"""Basic household system setup. Shows how to set up a standard system."""

from typing import Optional, Any
from hisim.building_sizer_utils.interface_configs.modular_household_config import (
    ModularHouseholdConfig,
    read_in_configs,
)
from hisim.simulator import SimulationParameters
from hisim.components import (
    gas_meter,
    generic_boiler,
    heat_distribution_system,
    loadprofilegenerator_utsp_connector,
    simple_water_storage,
    solar_thermal_system,
)
from hisim.components import weather
from hisim.components import building
from hisim.components import electricity_meter
from hisim import loadtypes, log


__authors__ = "Kristina Dabrock"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Kristina Dabrock"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Kristina Dabrock"
__email__ = "k.dabrock@fz-juelich.de"
__status__ = "development"


def setup_function(
    my_sim: Any,
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """Basic household system setup.

    This setup function emulates an household including the basic components. Here the residents have their
    electricity and warm water and heating needs covered by the solar thermal system and a gas boiler.
    Furthermore, a DHW storage is installed.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - SolarThermalSystem
        - SolarThermalSystemController
        - Building
        - Gas Heater
        - Gas Heater Controller
        - Heat Distribution System
        - Heat Distribution Controller
        - DHW Water Storage
    """

    # =================================================================================================================================
    # Set System Parameters
    year = 2021
    seconds_per_timestep = 60 * 15

    config_filename = my_sim.my_module_config
    # try reading energ system and archetype configs
    my_config = read_in_configs(my_sim.my_module_config)
    if my_config is None:
        my_config = ModularHouseholdConfig().get_default_config_for_household_gas_solar_thermal()
        log.warning(
            f"Could not read the modular household config from path '{config_filename}'. Using the gas ans solar thermal household default config instead."
        )
    assert my_config.archetype_config_ is not None
    assert my_config.energy_system_config_ is not None
    arche_type_config_ = my_config.archetype_config_

    # Set Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_with_only_plots(
            year=year, seconds_per_timestep=seconds_per_timestep
        )

        # TODO: Set postprocessing options

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # =================================================================================================================================
    # Build Components

    # Set heating systems for space heating and domestic hot water
    heating_reference_temperature_in_celsius = -7.0

    # Set Building (scale building according to total base area and not absolute floor area)
    number_of_apartments = arche_type_config_.number_of_dwellings_per_building

    # =================================================================================================================================
    # Build Basic Components

    # Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home()
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(
        config=my_building_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)
    my_weather = weather.Weather(
        config=my_weather_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Heat Distribution Controller
    my_heat_distribution_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
        set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
        set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
    )

    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_heat_distribution_controller_config,
    )
    my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
        config=my_heat_distribution_controller_config
    )

    # Gas Heater (for space heating and DHW) - Component
    my_gas_heater_config = generic_boiler.GenericBoilerConfig.get_scaled_condensing_gas_boiler_config(
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt
    )
    my_gas_heater = generic_boiler.GenericBoiler(
        config=my_gas_heater_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Gas Heater (for space heating and DHW) - Controller
    my_gas_heater_controller_config = (
        generic_boiler.GenericBoilerControllerConfig.get_default_modulating_generic_boiler_controller_config(
            minimal_thermal_power_in_watt=my_gas_heater_config.minimal_thermal_power_in_watt,
            maximal_thermal_power_in_watt=my_gas_heater_config.maximal_thermal_power_in_watt,
            with_domestic_hot_water_preparation=True,
        )
    )
    my_gas_heater_controller = generic_boiler.GenericBoilerController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_gas_heater_controller_config,
    )

    # Heat Water Storage
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
        max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
        temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
        sizing_option=simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GAS_HEATER,
    )

    my_simple_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Heat Distribution System
    my_heat_distribution_system_config = (
        heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
            water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
            absolute_conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
            heating_system=my_hds_controller_information.hds_controller_config.heating_system,
        )
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Solar thermal for DHW
    my_solar_thermal_system_config = solar_thermal_system.SolarThermalSystemConfig.get_default_solar_thermal_system(
        area_m2=4
    )
    my_solar_thermal_system = solar_thermal_system.SolarThermalSystem(
        config=my_solar_thermal_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Gas Heater (for DHW) - Controller
    my_solar_thermal_system_controller_config = (
        solar_thermal_system.SolarThermalSystemControllerConfig.get_solar_thermal_system_controller_config()
    )

    my_solar_thermal_system_controller = solar_thermal_system.SolarThermalSystemController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_solar_thermal_system_controller_config,
    )

    # DHW Storage
    my_dhw_storage_config = simple_water_storage.SimpleDHWStorageConfig.get_scaled_dhw_storage(
        number_of_apartments=number_of_apartments
    )

    my_dhw_storage = simple_water_storage.SimpleDHWStorage(
        my_simulation_parameters=my_simulation_parameters,
        config=my_dhw_storage_config,
    )

    # Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )

    # Gas Meter
    my_gas_meter = gas_meter.GasMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=gas_meter.GasMeterConfig.get_gas_meter_default_config(),
    )

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    my_electricity_meter.add_component_input_and_connect(
        source_object_name=my_occupancy.component_name,
        source_component_output=my_occupancy.ElectricalPowerConsumption,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )

    my_dhw_storage.connect_input(
        my_dhw_storage.WaterConsumption,
        my_occupancy.component_name,
        my_occupancy.WaterConsumption,
    )

    # Connect solarthermal as primary heat generator
    my_dhw_storage.connect_input(
        my_dhw_storage.WaterTemperatureFromHeatGenerator,
        my_solar_thermal_system.component_name,
        my_solar_thermal_system.WaterTemperatureOutput,
    )
    my_dhw_storage.connect_input(
        my_dhw_storage.WaterMassFlowRateFromHeatGenerator,
        my_solar_thermal_system.component_name,
        my_solar_thermal_system.WaterMassFlowOutput,
    )

    # Connect gas as secondary heat generator
    my_dhw_storage.connect_input(
        my_dhw_storage.WaterTemperatureFromSecondaryHeatGenerator,
        my_gas_heater.component_name,
        my_gas_heater.WaterOutputTemperatureDhw,
    )
    my_dhw_storage.connect_input(
        my_dhw_storage.WaterMassFlowRateFromSecondaryHeatGenerator,
        my_gas_heater.component_name,
        my_gas_heater.WaterOutputMassFlowDhw,
    )

    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    # =================================================================================================================================
    # Add Components to Simulation Parameters

    my_sim.add_component(my_building, connect_automatically=True)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_heat_distribution_system, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)
    my_sim.add_component(my_dhw_storage, connect_automatically=False)
    my_sim.add_component(my_simple_water_storage, connect_automatically=True)
    my_sim.add_component(my_electricity_meter, connect_automatically=True)
    my_sim.add_component(my_gas_heater, connect_automatically=True)
    my_sim.add_component(my_gas_heater_controller, connect_automatically=True)
    my_sim.add_component(my_gas_meter, connect_automatically=True)
    my_sim.add_component(my_solar_thermal_system, connect_automatically=True)
    my_sim.add_component(my_solar_thermal_system_controller, connect_automatically=True)
