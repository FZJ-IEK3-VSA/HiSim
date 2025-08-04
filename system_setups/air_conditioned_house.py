"""Air-conditioned household."""

# clean
import os
from typing import Optional

import pandas as pd

from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulator import SimulationParameters
from hisim.simulator import Simulator
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import air_conditioner


__authors__ = "Marwa Alfouly, Sebastian Dickler, Kristina Dabrock"
__copyright__ = "Copyright 2025, HiSim - Household Infrastructure and Building Simulator"
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
    location = "Seville"

    # Build components

    """System parameters"""

    if my_simulation_parameters is None:
        # my_simulation_parameters = SimulationParameters.one_day_only_with_only_plots(year, seconds_per_timestep)
        my_simulation_parameters = SimulationParameters.full_year_with_only_plots(year, seconds_per_timestep)
        my_simulation_parameters.enable_plots_only()
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.GENERATE_PDF_REPORT)

    my_sim.set_simulation_parameters(my_simulation_parameters)

    """Building (1/2)"""
    heating_reference_temperatures = pd.read_csv(utils.HISIMPATH["housing_reference_temperatures"])
    heating_reference_temperature = heating_reference_temperatures.set_index("Location").loc[
        "ES", "HeatingReferenceTemperature"
    ]
    my_building_config = building.BuildingConfig(
        building_name="BUI1",
        name="Building",
        building_code="ES.ME.SFH.04.Gen.ReEx.001.003",
        building_heat_capacity_class="medium",
        initial_internal_temperature_in_celsius=22,
        heating_reference_temperature_in_celsius=heating_reference_temperature,
        set_heating_temperature_in_celsius=20,
        set_cooling_temperature_in_celsius=24,
        absolute_conditioned_floor_area_in_m2=None,
        total_base_area_in_m2=None,
        number_of_apartments=None,
        enable_opening_windows=False,
        max_thermal_building_demand_in_watt=None,
        floor_u_value_in_watt_per_m2_per_kelvin=None,
        floor_area_in_m2=None,
        facade_u_value_in_watt_per_m2_per_kelvin=None,
        facade_area_in_m2=None,
        roof_u_value_in_watt_per_m2_per_kelvin=None,
        roof_area_in_m2=None,
        window_u_value_in_watt_per_m2_per_kelvin=None,
        window_area_in_m2=None,
        door_u_value_in_watt_per_m2_per_kelvin=None,
        door_area_in_m2=None,
        predictive=False,
        device_co2_footprint_in_kg=None,
        investment_costs_in_euro=None,
        maintenance_costs_in_euro_per_year=None,
        subsidy_as_percentage_of_investment_costs=None,
        lifetime_in_years=None,
    )
    my_building = building.Building(
        config=my_building_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_building_information = building.BuildingInformation(config=my_building_config)

    """ Occupancy Profile """
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()

    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_occupancy)

    """Weather"""
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.SEVILLE)

    my_weather = weather.Weather(
        config=my_weather_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_weather)

    """Photovoltaic System"""
    power = 4e3
    pv_co2_footprint = power * 1e-3 * 130.7
    pv_cost = power * 1e-3 * 535.81
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig(
        building_name="BUI1",
        time=year,
        location=location,
        power_in_watt=power,
        load_module_data=False,
        module_name="Hanwha HSL60P6-PA-4-250T [2013]",
        integrate_inverter=True,
        module_database=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE,
        inverter_database=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE,
        tilt=30,
        azimuth=180,
        inverter_name="ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_",
        source_weight=-1,
        name="PVSystem",
        device_co2_footprint_in_kg=pv_co2_footprint,
        investment_costs_in_euro=pv_cost,
        maintenance_costs_in_euro_per_year=0.01 * pv_cost,
        subsidy_as_percentage_of_investment_costs=0.0,
        lifetime_in_years=25,
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
    my_air_conditioner_config = air_conditioner.AirConditionerConfig.get_scaled_air_conditioner_config(
        my_building_information.max_thermal_building_demand_in_watt,
        my_building_information.heating_reference_temperature_in_celsius,
    )
    my_air_conditioner = air_conditioner.AirConditioner(
        config=my_air_conditioner_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.add_component(my_air_conditioner, connect_automatically=True)

    """Building (2/2)"""
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_air_conditioner.component_name,
        my_air_conditioner.ThermalPowerDelivered,
    )
    my_sim.add_component(my_building, connect_automatically=True)

    """Air conditioner on-off controller"""
    my_air_conditioner_controller_config = (
        air_conditioner.AirConditionerControllerConfig.get_default_air_conditioner_controller_config()
    )
    my_air_conditioner_controller = air_conditioner.AirConditionerController(
        config=my_air_conditioner_controller_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    my_sim.add_component(my_air_conditioner_controller, connect_automatically=True)
