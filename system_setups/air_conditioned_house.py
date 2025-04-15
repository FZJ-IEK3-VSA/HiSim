"""Air-conditioned household."""

# clean
import os
from typing import Optional

from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulator import SimulationParameters
from hisim.simulator import Simulator
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import air_conditioner


__authors__ = "Marwa Alfouly, Sebastian Dickler, Kristina Dabrock"
__copyright__ = (
    "Copyright 2025, HiSim - Household Infrastructure and Building Simulator"
)
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Kristina Dabrock"
__email__ = "k.dabrock@fz-juelich.de"
__status__ = "development"


def setup_function(
    my_sim: Simulator,
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:
    """Household Model.

    This setup function emulates an air-conditioned house.
    Here the residents have their electricity covered by a photovoltaic system, a battery, and the electric grid.
    Thermal load of the building is covered with an air conditioner.

    Connected components are:
        - Occupancy (Residents' Demands)
        - Weather
        - Price signal
        - Photovoltaic System
        - Building
        - Air conditioner and controller

    Analyzed region: Southern Europe.

    Representative locations selected according to climate zone with
    focus on the Mediterranean climate as it has the highest cooling demand.

    TABULA code for the exemplary single-family household is chosen according to the construction year class:

        1. Seville: ES.ME.SFH.05.Gen.ReEx.001.001 , construction year: 1980 - 2006 okay
        2. Madrid:  ES.ME.SFH.05.Gen.ReEx.001.001 , construction year: 1980 - 2006 okay
        3. Milan: IT.MidClim.SFH.06.Gen.ReEx.001.001 , construction year: 1976 - 1990 okay
        4. Belgrade: RS.N.SFH.06.Gen.ReEx.001.002, construction year: 1981-1990 okay
        5. Ljubljana: SI.N.SFH.04.Gen.ReEx.001.003, construction year: 1981-2001   okay

        For the following two locations building were selected for a different construction class, because at earlier
        years the building are of old generation (compared to the other 5 locations)

        6. Athens: GR.ZoneB.SFH.03.Gen.ReEx.001.001 , construction year: 2001 - 2010
        7. Cyprus: CY.N.SFH.03.Gen.ReEx.001.003, , construction year: 2007 - 2013

    """

    # Delete all files in cache:
    dir_cache = "..//hisim//inputs//cache"
    if os.path.isdir(dir_cache):
        for file in os.listdir(dir_cache):
            os.remove(os.path.join(dir_cache, file))

    # Set general simulation parameters
    year = 2021
    seconds_per_timestep = 60

    # Set general controller parameters
    # Temperature comfort range in °C
    min_comfort_temp = 21.0
    max_comfort_temp = 24.0

    # Set weather
    location = "Seville"

    # Set PV system
    time = 2019
    power = 4e3
    load_module_data = False
    module_name = "Hanwha HSL60P6-PA-4-250T [2013]"
    integrate_inverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    name = "PVSystem"
    azimuth = 180
    tilt = 30
    source_weight = -1
    pv_co2_footprint = power * 1e-3 * 130.7
    pv_cost = power * 1e-3 * 535.81
    pv_maintenance_cost_as_percentage_of_investment = 0.01
    pv_lifetime = 25

    # Set building
    building_code = "CY.N.SFH.03.Gen.ReEx.001.003"
    building_class = "medium"
    initial_temperature = 21
    heating_reference_temperature = -14
    absolute_conditioned_floor_area_in_m2 = None
    total_base_area_in_m2 = None
    number_of_apartments = None
    enable_opening_windows: bool = False

    # Set air conditioner
    ac_manufacturer = "Samsung"  # Other option: "Panasonic" , Further options are avilable in the smart_devices file
    ac_model = "AC120HBHFKH/SA - AC120HCAFKH/SA"  # "AC120HBHFKH/SA - AC120HCAFKH/SA"     #Other option: "CS-TZ71WKEW + CU-TZ71WKE"#
    min_operation_time = 60 * 30  # Unit: seconds
    min_idle_time = 60 * 15  # Unit: seconds

    # Build components

    """System parameters"""

    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.one_day_only_with_only_plots(
            year, seconds_per_timestep
        )
        # my_simulation_parameters = SimulationParameters.one_day_only_with_only_plots(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.one_day_only_with_all_options(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.one_week_only(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.one_week_with_only_plots(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.three_months_only(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.three_months_with_plots_only(year, seconds_per_timestep)
        # my_simulation_parameters = SimulationParameters.full_year_all_options(year, seconds_per_timestep)
        my_simulation_parameters.enable_all_options()

    my_sim.set_simulation_parameters(my_simulation_parameters)

    """Building (1/2)"""
    my_building_config = building.BuildingConfig(
        building_name="BUI1",
        name="Building",
        building_code=building_code,
        building_heat_capacity_class=building_class,
        initial_internal_temperature_in_celsius=initial_temperature,
        heating_reference_temperature_in_celsius=heating_reference_temperature,
        set_heating_temperature_in_celsius=min_comfort_temp,
        set_cooling_temperature_in_celsius=max_comfort_temp,
        absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area_in_m2,
        total_base_area_in_m2=total_base_area_in_m2,
        number_of_apartments=number_of_apartments,
        enable_opening_windows=enable_opening_windows,
        max_thermal_building_demand_in_watt=None,
        predictive=False,
    )
    my_building = building.Building(
        config=my_building_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    """ Occupancy Profile """
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()

    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_occupancy)

    """Weather"""
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.SEVILLE
    )

    my_weather = weather.Weather(
        config=my_weather_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_weather)

    """Photovoltaic System"""
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig(
        building_name="BUI1",
        time=time,
        location=location,
        power_in_watt=power,
        load_module_data=load_module_data,
        module_name=module_name,
        integrate_inverter=integrate_inverter,
        module_database=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE,
        inverter_database=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE,
        tilt=tilt,
        azimuth=azimuth,
        inverter_name=inverter_name,
        source_weight=source_weight,
        name=name,
        co2_footprint=pv_co2_footprint,
        cost=pv_cost,
        maintenance_cost_as_percentage_of_investment=pv_maintenance_cost_as_percentage_of_investment,
        lifetime=pv_lifetime,
        share_of_maximum_pv_potential=1.0,
        predictive=False,
        predictive_control=False,
        prediction_horizon=None,
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_photovoltaic_system.connect_only_predefined_connections(my_weather)
    my_sim.add_component(my_photovoltaic_system)

    """Air Conditioner"""
    my_air_conditioner_config = air_conditioner.AirConditionerConfig(
        building_name="BUI1",
        name="AirConditioner",
        model_name=ac_model,
        manufacturer=ac_manufacturer,
        cost=0,
        lifetime=0,
        co2_emissions_kg_co2_eq=0
    )
    my_air_conditioner = air_conditioner.AirConditioner(
        config=my_air_conditioner_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_air_conditioner, connect_automatically=True)

    """Building (2/2)"""
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(
        my_building.ThermalPowerDelivered, my_air_conditioner.component_name, my_air_conditioner.ThermalPowerDelivered,
    )
    my_sim.add_component(my_building, connect_automatically=True)


    """Air conditioner on-off controller"""
    my_air_conditioner_controller_config = (
        air_conditioner.AirConditionerControllerConfig(
            building_name="BU1",
            name="AirConditionerController",
            heating_set_temperature_deg_c=min_comfort_temp,
            cooling_set_temperature_deg_c=max_comfort_temp,
            minimum_idle_time_s=min_idle_time,
            minimum_runtime_s=min_operation_time,
            offset=2,
            temperature_difference_full_power_deg_c=3
        )
    )
    my_air_conditioner_controller = air_conditioner.AirConditionerController(
        config=my_air_conditioner_controller_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    my_sim.add_component(my_air_conditioner_controller, connect_automatically=True)

