"""Shows a single household with only heating."""

# clean
from typing import Optional, Any
from hisim.simulator import SimulationParameters
from hisim.config import SizingContext
from hisim.components import (
    electricity_meter,
    gas_meter,
    heat_distribution_system,
    loadprofilegenerator_utsp_connector,
    simple_water_storage,
)
from hisim.components import weather
from hisim.components import building
from hisim.components import generic_boiler


__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021-2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


def setup_function(my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None) -> None:
    """Gas heater + buffer storage.

    This setup function emulates an household including
    the basic components. Here the residents have their
    heating needs covered by a gas heater and a heating
    water storage. The controller_l2_ems controls according
    to the storage tempreature the gas heater.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - GasHeater and its controller
        - HeatingStorage
        - Heat distribution system and its controller
        - Electricity meter
        - Gas meter
    """

    # =================================================================================================================================
    # Set System Parameters

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60 * 15
    heating_reference_temperature_in_celsius = -12.2

    # =================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_with_only_plots(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Building. The weather config comes first because the building config copies its identity
    # (weather_identity); the weather component itself is added further down.
    my_weather_config = weather.WeatherConfig.get_default(weather.LocationEnum.AACHEN)
    my_building_config = building.BuildingConfig.preset_standard("Building")
    my_building_config.weather_identity = my_weather_config.identity()
    my_building = building.Building(
        config=my_building_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_building_information = my_building.my_building_information

    # Build occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather = weather.Weather(
        config=my_weather_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Distribution
    my_heat_distribution_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
        set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
        set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
    )
    my_heat_distribution_controller_config.heating_system = heat_distribution_system.HeatDistributionSystemType.RADIATOR

    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_heat_distribution_controller_config,
    )
    my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
        config=my_heat_distribution_controller_config
    )
    my_heat_distribution_system_config = (
        heat_distribution_system.HeatDistributionConfig.preset_standard("HeatDistributionSystem").resolve(
            SizingContext(
                water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kg_per_second,
                conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
                heat_distribution_system_type=my_hds_controller_information.hds_controller_config.heating_system,
            )
        )
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Gas Heater
    my_gas_heater_config = generic_boiler.GenericBoilerConfig.preset_condensing_gas_12kw("CondensingGasBoiler")
    my_gas_heater = generic_boiler.GenericBoiler(
        config=my_gas_heater_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_gas_heater_controller_config = (
        generic_boiler.GenericBoilerControllerConfig.get_default_modulating_generic_boiler_controller_config(
            minimal_thermal_power_in_watt=my_gas_heater_config.minimal_thermal_power_in_watt,
            maximal_thermal_power_in_watt=my_gas_heater_config.maximal_thermal_power_in_watt,
            with_domestic_hot_water_preparation=False,
        )
    )
    my_gas_heater_controller = generic_boiler.GenericBoilerController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_gas_heater_controller_config,
    )

    # Build Storage
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
        max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
        sizing_option=simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GAS_HEATER,
    )

    my_simple_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Meters
    # Both meters exist for the sake of the KPI layer, which derives every energy-balance
    # figure from a meter rather than from the components themselves. Without the electricity
    # meter the grid exchange is unknown, the self-sufficiency chain in kpi_preparation.py has
    # nothing to work from, and the run dies in post-processing.
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )
    my_gas_meter = gas_meter.GasMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=gas_meter.GasMeterConfig.get_gas_meter_default_config(),
    )

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    my_building.connect_only_predefined_connections(my_weather, my_occupancy)

    # =================================================================================================================================
    # Add Components to Simulation Parameters

    my_sim.add_component(my_gas_heater_controller, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)
    my_sim.add_component(my_heat_distribution_system, connect_automatically=True)
    my_sim.add_component(my_simple_water_storage, connect_automatically=True)
    my_sim.add_component(my_gas_heater, connect_automatically=True)
    my_sim.add_component(my_building)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
    # Both meters are wired by their own default connections and nothing else. Feeding a meter
    # explicitly *and* registering it automatically is how household_gas_solar_thermal came to
    # count the occupancy's electricity twice (P3 finding F-4): the simulator does not
    # de-duplicate two feeds of one source, so the sum is genuinely taken twice.
    my_sim.add_component(my_electricity_meter, connect_automatically=True)
    my_sim.add_component(my_gas_meter, connect_automatically=True)
