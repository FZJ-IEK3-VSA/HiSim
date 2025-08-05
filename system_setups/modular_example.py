"""System setup sets up a modular household according to json input file."""

# clean

import os
import shutil
from typing import Any, List, Optional, Tuple

import pandas as pd
from utspclient.helpers.lpgdata import (
    TransportationDeviceSets,
    TravelRouteSets,
    EnergyIntensityType,
)

import hisim.loadtypes as lt
import hisim.log
import hisim.utils
from hisim.components import (
    building,
    controller_l2_energy_management_system,
    loadprofilegenerator_utsp_connector,
    weather,
    generic_smart_device,
)
from hisim.modular_household import component_connections
from hisim.modular_household.interface_configs.modular_household_config import read_in_configs
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulator import SimulationParameters


def cleanup_old_result_folders():
    """Removes old result folders of previous setup_function simulations."""
    base_path = os.path.join(hisim.utils.hisim_abs_path, os.path.pardir, "system_setups", "results")
    files_in_folder = os.listdir(base_path)
    for file in files_in_folder:
        if file.startswith("setup_function"):
            full_path = os.path.join(base_path, file)
            shutil.rmtree(full_path)


def cleanup_old_lpg_requests():
    """Removes old results of loadprofilegenerator_connector_utsp."""
    if not os.path.exists(hisim.utils.HISIMPATH["utsp_results"]):
        # no old data exists, nothing to remove
        return
    folder_list = os.listdir(hisim.utils.HISIMPATH["utsp_results"])
    for folder in folder_list:
        for file in os.listdir(os.path.join(hisim.utils.HISIMPATH["utsp_results"], folder)):
            full_file_path = os.path.join(hisim.utils.HISIMPATH["utsp_results"], folder, file)
            hisim.log.information(f"Clean up old lpg request result file: {full_file_path}")
            os.remove(full_file_path)


def get_heating_reference_temperature_and_season_from_location(
    location: str,
) -> Tuple[float, List[int]]:
    """Reads in temperature of coldest day for sizing of heating system and heating season for control of the heating system.

    Both relies on the location.
    :param location: location of the building, reference temperature and heating season depend on the climate (at the location)
    :type location: str

    :return: heating reference temperature and heating season of the location,
    heating season is given by julian day of the year when heating period starts (third entry) and ends (first entry).
    :rtype: Tuple[float, List[int]]
    """

    converting_data = pd.read_csv(hisim.utils.HISIMPATH["housing_reference_temperatures"])
    # converting_data.index = converting_data["Location"]
    converting_data.set_index(inplace=True, keys="Location")

    return (
        float(converting_data.loc[location]["HeatingReferenceTemperature"]),
        [
            int(converting_data.loc[location]["HeatingSeasonEnd"]),
            int(converting_data.loc[location]["HeatingSeasonBegin"]),
        ],
    )


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: MC0001
    """Setup function emulates an household including the basic components.

    The configuration of the household is read in via the json input file "system_config.json".
    """
    # TODO: does not work in docker --> commented out for now
    cleanup_old_lpg_requests()

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60 * 60

    household_config = read_in_configs(my_sim.my_module_config)

    assert household_config.archetype_config_ is not None
    assert household_config.system_config_ is not None
    arche_type_config_ = household_config.archetype_config_
    system_config_ = household_config.system_config_

    count = 1  # initialize source_weight with one
    production: List = []  # initialize list of components involved in production
    consumption: List = []  # initialize list of components involved in consumption

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_CARPET)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.GENERATE_PDF_REPORT)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
        # my_simulation_parameters.post_processing_options.append(
        #     PostProcessingOptions.GENERATE_CSV_FOR_HOUSING_DATA_BASE
        # )
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.EXPORT_TO_CSV)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.INCLUDE_CONFIGS_IN_PDF_REPORT)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # get archetype configuration
    location = arche_type_config_.building_code.split(".")[0]
    occupancy_profile_utsp = arche_type_config_.occupancy_profile_utsp
    occupancy_profile = arche_type_config_.occupancy_profile
    building_code = arche_type_config_.building_code
    floor_area = arche_type_config_.absolute_conditioned_floor_area
    water_heating_system_installed = arche_type_config_.water_heating_system_installed  # Electricity, Hydrogen or False
    heating_system_installed = arche_type_config_.heating_system_installed
    mobility_set = arche_type_config_.mobility_set
    mobility_distance = arche_type_config_.mobility_distance

    # select if utsp is needed based on defined occupancy_profile:
    if occupancy_profile_utsp is None and occupancy_profile is None:
        raise Exception(
            "Either occupancy_profile_utsp or occupancy_profile need to be defined in archetype_config file."
        )
    if occupancy_profile_utsp is not None and occupancy_profile is not None:
        hisim.log.warning(
            "Both occupancy_profile_utsp and occupancy_profile are defined, so the connection to the UTSP is considered by default. "
        )

    utsp_connected = occupancy_profile_utsp is not None

    # get system configuration: technical equipment
    heatpump_included = system_config_.heatpump_included
    if heatpump_included:
        heating_system_installed = lt.HeatingSystems.HEAT_PUMP
        water_heating_system_installed = lt.HeatingSystems.HEAT_PUMP
    heatpump_power = system_config_.heatpump_power
    if heatpump_power is None:
        heatpump_power = 1
        hisim.log.information("Default power is used for heat pump. ")
    if heatpump_power < 1:
        raise Exception("Heat pump power cannot be smaller than default: choose values greater than one")
    controllable = system_config_.surplus_control_considered
    pv_included = system_config_.pv_included  # True or False
    pv_peak_power = system_config_.pv_peak_power or 5e3  # set default
    smart_devices_included = system_config_.smart_devices_included  # True or False
    buffer_included = system_config_.buffer_included
    buffer_volume = system_config_.buffer_volume or 1  # set default
    if buffer_volume < 1:
        raise Exception("Buffer volume cannot be smaller than default: choose values greater than one")
    battery_included = system_config_.battery_included
    battery_capacity = system_config_.battery_capacity or 5  # set default
    chp_included = system_config_.chp_included
    chp_power = system_config_.chp_power or 1  # set default
    hydrogen_setup_included = system_config_.hydrogen_setup_included
    fuel_cell_power = system_config_.fuel_cell_power or 1  # set default
    h2_storage_size = system_config_.h2_storage_size or 200  # TODO: replace default
    electrolyzer_power = system_config_.electrolyzer_power or 1  # set default
    ev_included = system_config_.ev_included
    charging_station = system_config_.charging_station

    # BASICS
    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum[location])
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_weather)

    # Build building
    (
        reference_temperature,
        heating_season,
    ) = get_heating_reference_temperature_and_season_from_location(location=location)

    my_building_config = building.BuildingConfig(
        name="Building_1",
        building_name="BUI1",
        building_code=building_code,
        building_heat_capacity_class="medium",
        initial_internal_temperature_in_celsius=23,
        heating_reference_temperature_in_celsius=reference_temperature,
        absolute_conditioned_floor_area_in_m2=floor_area,
        total_base_area_in_m2=None,
        number_of_apartments=None,
        predictive=False,
        set_heating_temperature_in_celsius=19.0,
        set_cooling_temperature_in_celsius=24.0,
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
        device_co2_footprint_in_kg=1,
        investment_costs_in_euro=1,
        maintenance_costs_in_euro_per_year=0.01,
        subsidy_as_percentage_of_investment_costs=0.0,
        lifetime_in_years=1,
    )
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_building)

    # build occupancy
    if utsp_connected:
        if mobility_set is None:
            this_mobility_set = TransportationDeviceSets.Bus_and_one_30_km_h_Car
            hisim.log.information("Default is used for mobility set, because None was defined.")
        else:
            this_mobility_set = mobility_set
        if mobility_distance is None:
            this_mobility_distance = TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance
            hisim.log.information("Default is used for mobility distance, because None was defined.")
        else:
            this_mobility_distance = mobility_distance

        my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
            name="UTSPConnector",
            data_acquisition_mode=loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_UTSP,
            household=occupancy_profile_utsp,  # type: ignore
            energy_intensity=EnergyIntensityType.EnergySaving,
            result_dir_path=hisim.utils.HISIMPATH["utsp_results"],
            travel_route_set=this_mobility_distance,
            transportation_device_set=this_mobility_set,
            charging_station_set=charging_station,
            profile_with_washing_machine_and_dishwasher=not smart_devices_included,
            predictive_control=False,
            predictive=False,
            building_name="BUI1",
        )

        my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
            config=my_occupancy_config,
            my_simulation_parameters=my_simulation_parameters,
        )

    else:
        # Build occupancy with predefined profile
        my_occupancy_config = (
            loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
        )

        my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
            config=my_occupancy_config,
            my_simulation_parameters=my_simulation_parameters,
        )

    my_building.connect_only_predefined_connections(my_weather, my_occupancy)

    """TODO: pass url and api, chose better directory or use inputs"""

    my_sim.add_component(my_occupancy)
    consumption.append(my_occupancy)

    # # add price signal
    # my_price_signal = generic_price_signal.PriceSignal(
    #     config=generic_price_signal.PriceSignalConfig.get_default_price_signal_config(),
    #     my_simulation_parameters=my_simulation_parameters
    # )
    # my_sim.add_component(my_price_signal)

    # """PV"""
    if pv_included:
        production, count = component_connections.configure_pv_system(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            my_weather=my_weather,
            production=production,
            pv_peak_power=pv_peak_power,
            count=count,
        )

    # """CARS"""
    if mobility_set is not None:
        my_cars, count = component_connections.configure_cars(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            count=count,
            ev_included=ev_included,
            my_occupancy_instance=my_occupancy,
        )
        if controllable is False:
            for car in my_cars:
                consumption.append(car)

    # """SMART DEVICES"""
    my_smart_devices: list[generic_smart_device.SmartDevice] = []
    if smart_devices_included:
        my_smart_devices, count = component_connections.configure_smart_devices(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            count=count,
            smart_devices_included=smart_devices_included,
        )

    # """SURPLUS CONTROLLER"""
    if needs_ems(
        battery_included,
        chp_included,
        hydrogen_setup_included,
        ev_included,
        heating_system_installed,
        smart_devices_included,
        water_heating_system_installed,
    ):
        my_electricity_controller_config = controller_l2_energy_management_system.EMSConfig.get_default_config_ems()
        my_electricity_controller = controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
            my_simulation_parameters=my_simulation_parameters,
            config=my_electricity_controller_config,
        )

        my_electricity_controller.add_component_inputs_and_connect(
            source_component_classes=consumption,
            source_component_field_name=consumption[0].ElectricityOutput,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            source_weight=999,
        )
        my_electricity_controller.add_component_inputs_and_connect(
            source_component_classes=production,
            source_component_field_name=production[0].ElectricityOutput,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION],
            source_weight=999,
        )

    # """ EV BATTERY """
    if ev_included:
        if mobility_set is None:
            raise Exception("If EV should be simulated mobility set needs to be defined.")
        _ = component_connections.configure_ev_batteries(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,  # noqa
            my_cars=my_cars,
            charging_station_set=charging_station,
            mobility_set=mobility_set,
            my_electricity_controller=my_electricity_controller,
            controllable=controllable,
        )  # could return ev_capacities if needed

    # """SMART CONTROLLER FOR SMART DEVICES"""
    # use clever controller if smart devices are included and do not use it if it is false
    if smart_devices_included and controllable and utsp_connected:
        component_connections.configure_smart_controller_for_smart_devices(
            my_electricity_controller=my_electricity_controller,
            my_smart_devices=my_smart_devices,
        )

    # """WATERHEATING"""
    if water_heating_system_installed in [
        lt.HeatingSystems.HEAT_PUMP,
        lt.HeatingSystems.ELECTRIC_HEATING,
    ]:
        my_boiler, count = component_connections.configure_water_heating_electric(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            my_occupancy=my_occupancy,
            my_electricity_controller=my_electricity_controller,
            my_weather=my_weather,
            water_heating_system_installed=water_heating_system_installed,
            controllable=controllable,
            count=count,
            number_of_apartments=my_building_information.number_of_apartments,
        )

    else:
        my_boiler, count = component_connections.configure_water_heating(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            my_occupancy=my_occupancy,
            water_heating_system_installed=water_heating_system_installed,
            count=count,
            number_of_apartments=my_building_information.number_of_apartments,
        )

    # """HEATING"""
    if buffer_included:
        if heating_system_installed in [
            lt.HeatingSystems.HEAT_PUMP,
            lt.HeatingSystems.ELECTRIC_HEATING,
        ]:
            (
                _,
                my_buffer,
                count,
            ) = component_connections.configure_heating_with_buffer_electric(
                my_sim=my_sim,
                my_simulation_parameters=my_simulation_parameters,
                my_building=my_building,
                my_electricity_controller=my_electricity_controller,
                my_weather=my_weather,
                heating_system_installed=heating_system_installed,
                heatpump_power=heatpump_power,
                buffer_volume=buffer_volume,
                controllable=controllable,
                heating_season=heating_season,
                count=count,
            )
        else:
            (
                _,
                my_buffer,
                count,
            ) = component_connections.configure_heating_with_buffer(
                my_sim=my_sim,
                my_simulation_parameters=my_simulation_parameters,
                my_building=my_building,
                heating_system_installed=heating_system_installed,
                buffer_volume=buffer_volume,
                heating_season=heating_season,
                count=count,
            )

    else:
        my_buffer = None
        if heating_system_installed in [
            lt.HeatingSystems.HEAT_PUMP,
            lt.HeatingSystems.ELECTRIC_HEATING,
        ]:
            _, count = component_connections.configure_heating_electric(
                my_sim=my_sim,
                my_simulation_parameters=my_simulation_parameters,
                my_building=my_building,
                my_electricity_controller=my_electricity_controller,
                my_weather=my_weather,
                heating_system_installed=heating_system_installed,
                heatpump_power=heatpump_power,
                controllable=controllable,
                heating_season=heating_season,
                count=count,
            )
        else:
            _, count = component_connections.configure_heating(
                my_sim=my_sim,
                my_simulation_parameters=my_simulation_parameters,
                my_building=my_building,
                heating_system_installed=heating_system_installed,
                heating_season=heating_season,
                count=count,
            )

    # """natural gas CHP"""
    if chp_included and not buffer_included:
        count = component_connections.configure_chp(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            my_building=my_building,
            my_boiler=my_boiler,
            my_electricity_controller=my_electricity_controller,
            chp_power=chp_power,
            controllable=controllable,
            count=count,
        )
    if chp_included and buffer_included:
        count = component_connections.configure_chp_with_buffer(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            my_buffer=my_buffer,
            my_boiler=my_boiler,
            my_electricity_controller=my_electricity_controller,
            chp_power=chp_power,
            controllable=controllable,
            count=count,
        )

    # """hydrogen storage with fuel cell and electrolyzer"""
    if hydrogen_setup_included and not buffer_included:
        count = component_connections.configure_elctrolysis_h2storage_fuelcell_system(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            my_building=my_building,
            my_boiler=my_boiler,
            my_electricity_controller=my_electricity_controller,
            fuel_cell_power=fuel_cell_power,
            h2_storage_size=h2_storage_size,
            electrolyzer_power=electrolyzer_power * pv_peak_power,
            controllable=controllable,
            count=count,
        )

    if hydrogen_setup_included and buffer_included:
        count = component_connections.configure_elctrolysis_h2storage_fuelcell_system_with_buffer(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            my_buffer=my_buffer,
            my_boiler=my_boiler,
            my_electricity_controller=my_electricity_controller,
            fuel_cell_power=fuel_cell_power,
            h2_storage_size=h2_storage_size,
            electrolyzer_power=electrolyzer_power * pv_peak_power,
            controllable=controllable,
            count=count,
        )

    # """BATTERY"""
    if battery_included and controllable:
        count = component_connections.configure_battery(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            my_electricity_controller=my_electricity_controller,
            battery_capacity=battery_capacity,
            count=count,
        )

    if needs_ems(
        battery_included,
        chp_included,
        hydrogen_setup_included,
        ev_included,
        heating_system_installed,
        smart_devices_included,
        water_heating_system_installed,
    ):
        my_sim.add_component(my_electricity_controller)


def needs_ems(  # pylint: disable=R0911
    battery_included,
    chp_included,
    hydrogen_setup_included,
    ev_included,
    heating_system_installed,
    smart_devices_included,
    water_heating_system_installed,
):  # noqa
    """Checks if a system needs an EMS."""
    if battery_included:
        return True
    if chp_included:
        return True
    if hydrogen_setup_included:
        return True
    if smart_devices_included:
        return True
    if ev_included:
        return True
    if heating_system_installed in [
        lt.HeatingSystems.HEAT_PUMP,
        lt.HeatingSystems.ELECTRIC_HEATING,
    ] or water_heating_system_installed in [
        lt.HeatingSystems.HEAT_PUMP,
        lt.HeatingSystems.ELECTRIC_HEATING,
    ]:
        return True
    return False
