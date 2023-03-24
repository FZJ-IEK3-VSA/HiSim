"""Example sets up a modular household according to json input file."""

import json
import os
import shutil
from os import path
from typing import Any, List, Optional, Tuple

import pandas as pd
from utspclient.helpers.lpgdata import TransportationDeviceSets, TravelRouteSets

import hisim.loadtypes as lt
import hisim.log
import hisim.utils
from hisim.components import (
    building,
    controller_l2_energy_management_system,
    generic_price_signal,
    loadprofilegenerator_connector,
    loadprofilegenerator_utsp_connector,
    weather,
)
from hisim.modular_household import component_connections
from hisim.modular_household.interface_configs.modular_household_config import (
    read_in_configs
)
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulator import SimulationParameters


def cleanup_old_result_folders():
    """ Removes old result folders of previous modular_household_explicit simulations. """
    base_path = os.path.join(
        hisim.utils.hisim_abs_path, os.path.pardir, "examples", "results"
    )
    files_in_folder = os.listdir(base_path)
    for file in files_in_folder:
        if file.startswith("modular_household_explicit"):
            full_path = os.path.join(base_path, file)
            shutil.rmtree(full_path)


def get_heating_reference_temperature_and_season_from_location(location: str) -> Tuple[float, List[int]]:
    """ Reads in temperature of coldest day for sizing of heating system and heating season for control of the heating system.

    Both relies on the location.
    :param location: location of the building, reference temperature and heating season depend on the climate (at the location)
    :type location: str

    :return: heating reference temperature and heating season of the location,
    heating season is given by julian day of the year when heating period starts (third entry) and ends (first entry).
    :rtype: Tuple[float, List[int]]
    """

    converting_data = pd.read_csv(hisim.utils.HISIMPATH["housing_reference_temperatures"])
    converting_data.index = converting_data["Location"]
    return (float(converting_data.loc[location]["HeatingReferenceTemperature"]),
            [int(converting_data.loc[location]['HeatingSeasonEnd']),
             int(converting_data.loc[location]['HeatingSeasonBegin'])])


def modular_household_explicit(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: MC0001
    """Setup function emulates an household including the basic components.

    The configuration of the household is read in via the json input file "system_config.json".
    """
    # TODO: does not work in docker --> commented out for now
    # cleanup_old_result_folders()

    # Set simulation parameters
    year = 2019
    seconds_per_timestep = 60 * 15

    # read the modular household config file
    household_config = read_in_configs("modular_example_config.json")
    assert household_config.archetype_config_ is not None
    assert household_config.system_config_ is not None
    arche_type_config_ = household_config.archetype_config_
    system_config_ = household_config.system_config_

    count = 1  # initialize source_weight with one
    production: List = []  # initialize list of components involved in production
    consumption: List = []  # initialize list of components involved in consumption

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_CARPET)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.GENERATE_PDF_REPORT)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.GENERATE_CSV_FOR_HOUSING_DATA_BASE)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.INCLUDE_CONFIGS_IN_PDF_REPORT)
        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.COMPUTE_AND_WRITE_KPIS_TO_REPORT
        )
        # my_simulation_parameters.post_processing_options.append(
        #     PostProcessingOptions.MAKE_NETWORK_CHARTS
        # )

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # get archetype configuration
    location = arche_type_config_.building_code.split(".")[0]
    occupancy_profile_utsp = arche_type_config_.occupancy_profile_utsp
    occupancy_profile = arche_type_config_.occupancy_profile
    building_code = arche_type_config_.building_code
    floor_area = arche_type_config_.absolute_conditioned_floor_area
    water_heating_system_installed = (
        arche_type_config_.water_heating_system_installed
    )  # Electricity, Hydrogen or False
    heating_system_installed = arche_type_config_.heating_system_installed
    mobility_set = arche_type_config_.mobility_set
    mobility_distance = arche_type_config_.mobility_distance

    # select if utsp is needed based on defined occupancy_profile:
    if occupancy_profile_utsp is None and occupancy_profile is None:
        raise Exception('Either occupancy_profile_utsp or occupancy_profile need to be defined in archetype_config file.')
    if occupancy_profile_utsp is not None and occupancy_profile is not None:
        hisim.log.warning("Both occupancy_profile_utsp and occupancy_profile are defined, so the connection to the UTSP is considered by default. ")
    if occupancy_profile_utsp is not None:
        occupancy_profile = occupancy_profile_utsp
        utsp_connected = True
    else:
        utsp_connected = False
    del occupancy_profile_utsp

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
        raise Exception(
            "Heat pump power cannot be smaller than default: choose values greater than one"
        )
    clever = my_simulation_parameters.surplus_control
    pv_included = system_config_.pv_included  # True or False
    if pv_included:
        pv_peak_power = system_config_.pv_peak_power
    smart_devices_included = system_config_.smart_devices_included  # True or False
    buffer_included = system_config_.buffer_included
    buffer_volume = system_config_.buffer_volume
    if buffer_volume is None:
        buffer_volume = 1
        hisim.log.information("Default volume is used for buffer storage. ")
    elif buffer_volume < 1:
        raise Exception(
            "Buffer volume cannot be smaller than default: choose values greater than one"
        )
    battery_included = system_config_.battery_included
    if battery_included:
        battery_capacity = system_config_.battery_capacity
    chp_included = system_config_.chp_included
    if chp_included:
        chp_power = system_config_.chp_power
    h2_storage_included = system_config_.h2_storage_included
    if h2_storage_included:
        h2_storage_size = system_config_.h2_storage_size
    electrolyzer_included = system_config_.electrolyzer_included
    if electrolyzer_included:
        electrolyzer_power = system_config_.electrolyzer_power
    ev_included = system_config_.ev_included
    charging_station = system_config_.charging_station

    # BASICS
    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum[location]
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_weather)

    # Build building
    reference_temperature, heating_season = get_heating_reference_temperature_and_season_from_location(
        location=location
    )

    my_building_config = building.BuildingConfig(
        name="Building_1",
        building_code=building_code,
        building_heat_capacity_class="medium",
        initial_internal_temperature_in_celsius=23,
        heating_reference_temperature_in_celsius=reference_temperature,
        absolute_conditioned_floor_area_in_m2=floor_area,
        total_base_area_in_m2=None,
    )
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_building)

    # build occupancy
    if utsp_connected:
        if mobility_set is None:
            this_mobility_set = TransportationDeviceSets.Bus_and_one_30_km_h_Car
            hisim.log.information(
                "Default is used for mobility set, because None was defined."
            )
        else:
            this_mobility_set = mobility_set
        if mobility_distance is None:
            this_mobility_distance = (
                TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance
            )
            hisim.log.information(
                "Default is used for mobility distance, because None was defined."
            )
        else:
            this_mobility_distance = mobility_distance

        my_occupancy_config = (
            loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
                name="UTSPConnector",
                url=arche_type_config_.url,
                api_key=arche_type_config_.api_key,
                household=occupancy_profile,
                result_path=hisim.utils.HISIMPATH["results"],
                travel_route_set=this_mobility_distance,
                transportation_device_set=this_mobility_set,
                charging_station_set=charging_station,
            )
        )

        my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
            config=my_occupancy_config,
            my_simulation_parameters=my_simulation_parameters,
        )
    else:
        # Build occupancy
        my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig(
            "Occupancy", occupancy_profile or "", location, int(my_building.buildingdata["n_Apartment"])
        )
        my_occupancy = loadprofilegenerator_connector.Occupancy(
            config=my_occupancy_config,
            my_simulation_parameters=my_simulation_parameters,
        )

    my_building.connect_only_predefined_connections(my_weather, my_occupancy)

    """TODO: pass url and api, chose better directory or use inputs"""

    my_sim.add_component(my_occupancy)
    consumption.append(my_occupancy)

    # load economic parameters:
    economic_parameters_file = path.join(
        hisim.utils.HISIMPATH["modular_household"], "EconomicParameters.json"
    )
    with open(file=economic_parameters_file, mode="r", encoding="utf-8") as inputfile:
        economic_parameters = json.load(inputfile)
    # (
    #     pv_cost,
    #     smart_devices_cost,
    #     battery_cost,
    #     surplus_controller_cost,
    #     heatpump_cost,
    #     buffer_cost,
    #     chp_cost,
    #     h2_storage_cost,
    #     electrolyzer_cost,
    #     ev_cost,
    # ) = [0] * 10

    # add price signal
    my_price_signal = generic_price_signal.PriceSignal(
        config=generic_price_signal.PriceSignalConfig.get_default_price_signal_config(),
        my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_price_signal)

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
        # pv_cost = pv_cost + preprocessing.calculate_pv_investment_cost(economic_parameters, pv_included,
        #                                                                pv_peak_power)
        # production, count = component_connections.configure_pv_system(
        #     my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_weather=my_weather, production=production,
        #     pv_peak_power=pv_peak_power, count=count)
        # pv_cost = pv_cost + preprocessing.calculate_pv_investment_cost(economic_parameters, pv_included, pv_peak_power)

    # """CARS"""
    if mobility_set is not None:
        my_cars, count = component_connections.configure_cars(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            count=count,
            ev_included=ev_included,
            occupancy_config=my_occupancy_config,
        )
        if clever is False:
            for car in my_cars:
                consumption.append(car)

    # """SMART DEVICES"""
    if utsp_connected:
        my_smart_devices, count = component_connections.configure_smart_devices(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            count=count,
            smart_devices_included=smart_devices_included,
        )
        if not smart_devices_included or clever is False:
            for device in my_smart_devices:
                consumption.append(device)

    # """SURPLUS CONTROLLER"""
    if needs_ems(
        battery_included,
        chp_included,
        ev_included,
        heating_system_installed,
        smart_devices_included,
        water_heating_system_installed,
    ):
        my_electricity_controller_config = (
            controller_l2_energy_management_system.EMSConfig.get_default_config_ems()
        )
        my_electricity_controller = (
            controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
                my_simulation_parameters=my_simulation_parameters,
                config=my_electricity_controller_config,
            )
        )

        my_electricity_controller.add_component_inputs_and_connect(
            source_component_classes=consumption,
            outputstring="ElectricityOutput",
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            source_weight=999,
        )
        my_electricity_controller.add_component_inputs_and_connect(
            source_component_classes=production,
            outputstring="ElectricityOutput",
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION],
            source_weight=999,
        )

        # surplus_controller_cost = (
        #     preprocessing.calculate_surplus_controller_investment_cost(
        #         economic_parameters
        #     )
        # )

    if not needs_ems(
        battery_included,
        chp_included,
        ev_included,
        heating_system_installed,
        smart_devices_included,
        water_heating_system_installed,
    ):
        if economic_parameters["surpluscontroller_bought"]:
            hisim.log.information(
                "Error: Surplus Controller is bought but not needed/included"
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
            clever=clever,
        )  # could return ev_capacities if needed
        # """TODO: repair! """
        # for capacity in ev_capacities:
        #     ev_cost = ev_cost + preprocessing.calculate_electric_vehicle_investment_cost(economic_parameters, ev_included, ev_capacity=capacity)

    # """SMART CONTROLLER FOR SMART DEVICES"""
    # use clever controller if smart devices are included and do not use it if it is false
    if smart_devices_included and clever and utsp_connected:
        component_connections.configure_smart_controller_for_smart_devices(
            my_electricity_controller=my_electricity_controller,
            my_smart_devices=my_smart_devices,
        )
        # """ TODO: repair! """
        # smart_devices_cost = preprocessing.calculate_smart_devices_investment_cost(economic_parameters, smart_devices_included)

    # """WATERHEATING"""
    if water_heating_system_installed in [
        lt.HeatingSystems.HEAT_PUMP,
        lt.HeatingSystems.ELECTRIC_HEATING,
    ]:
        count = component_connections.configure_water_heating_electric(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            my_occupancy=my_occupancy,
            my_electricity_controller=my_electricity_controller,
            my_weather=my_weather,
            water_heating_system_installed=water_heating_system_installed,
            number_of_households=int(my_building.buildingdata["n_Apartment"]),
            controlable=clever,
            count=count,
        )
        """TODO: add heat pump cost. """

    else:
        count = component_connections.configure_water_heating(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            my_occupancy=my_occupancy,
            water_heating_system_installed=water_heating_system_installed,
            number_of_households=int(my_building.buildingdata["n_Apartment"]),
            count=count,
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
                controlable=clever,
                heating_season=heating_season,
                count=count,
            )
            """TODO: repair! """
            # heatpump_cost = heatpump_cost + preprocessing.calculate_heating_investment_cost(economic_parameters, heatpump_included, my_heater.power_th)
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

        # buffer_cost = preprocessing.calculate_buffer_investment_cost(
        #     economic_parameters, buffer_included, buffer_volume
        # )

    else:
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
                controlable=clever,
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

    # """BATTERY"""
    if battery_included and clever:
        count = component_connections.configure_battery(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            my_electricity_controller=my_electricity_controller,
            battery_capacity=battery_capacity,
            count=count,
        )
        # """TODO: repair! """
        # battery_cost = preprocessing.calculate_battery_investment_cost(economic_parameters, battery_included, battery_capacity)

    # """CHP + H2 STORAGE + ELECTROLYSIS"""
    if chp_included and h2_storage_included and electrolyzer_included and clever:
        (
            my_chp,
            count,
        ) = component_connections.configure_elctrolysis_h2storage_chp_system(
            my_sim=my_sim,
            my_simulation_parameters=my_simulation_parameters,
            my_building=my_building,
            my_electricity_controller=my_electricity_controller,
            chp_power=chp_power,
            h2_storage_size=h2_storage_size,
            electrolyzer_power=electrolyzer_power,
            count=count,
        )
        if buffer_included:
            my_buffer.connect_only_predefined_connections(my_chp)
        else:
            my_building.connect_input(
                input_fieldname=my_building.ThermalPowerDelivered,
                src_object_name=my_chp.component_name,
                src_field_name=my_chp.ThermalPowerDelivered,
            )

        # chp_cost = preprocessing.calculate_chp_investment_cost(
        #     economic_parameters, chp_included, chp_power
        # )
        # h2_storage_cost = preprocessing.calculate_h2storage_investment_cost(
        #     economic_parameters, h2_storage_included, h2_storage_size
        # )
        # electrolyzer_cost = preprocessing.calculate_electrolyzer_investment_cost(
        #     economic_parameters, electrolyzer_included, electrolyzer_power
        # )

    if needs_ems(
        battery_included,
        chp_included,
        ev_included,
        heating_system_installed,
        smart_devices_included,
        water_heating_system_installed,
    ):
        my_sim.add_component(my_electricity_controller)

    # co2_cost = 1000  # CO2 von Herstellung der Komponenten plus CO2 für den Stromverbrauch der Komponenten
    # surplus_controller_cost = 400

    # investment_cost = preprocessing.total_investment_cost_threshold_exceedance_check(
    #     economic_parameters,
    #     pv_cost,
    #     smart_devices_cost,
    #     battery_cost,
    #     surplus_controller_cost,
    #     heatpump_cost,
    #     buffer_cost,
    #     chp_cost,
    #     h2_storage_cost,
    #     electrolyzer_cost,
    #     ev_cost,
    # )
    # preprocessing.investment_cost_per_component_exceedance_check(
    #     economic_parameters,
    #     pv_cost,
    #     smart_devices_cost,
    #     battery_cost,
    #     surplus_controller_cost,
    #     heatpump_cost,
    #     buffer_cost,
    #     chp_cost,
    #     h2_storage_cost,
    #     electrolyzer_cost,
    #     ev_cost,
    # )

    # hisim.log.information(
    #     "total investment_cost"
    #     + str(investment_cost)
    #     + "pv_cost"
    #     + str(pv_cost)
    #     + "smart_devices_cost"
    #     + str(smart_devices_cost)
    #     + "battery_cost"
    #     + str(battery_cost)
    #     + "surplus_controller_cost"
    #     + str(surplus_controller_cost)
    #     + "heatpump_cost"
    #     + str(heatpump_cost)
    #     + "buffer_cost"
    #     + str(buffer_cost)
    #     + "chp_cost"
    #     + str(chp_cost)
    #     + "h2_storage_cost"
    #     + str(h2_storage_cost)
    #     + "electrolyzer_cost"
    #     + str(electrolyzer_cost)
    #     + "ev_cost"
    #     + str(ev_cost)
    # )


def needs_ems(
    battery_included,
    chp_included,
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
