"""Example sets up a modular household according to json input file."""

import json
from os import path
import os
import shutil
from typing import Any, List, Optional

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
from hisim.modular_household import component_connections, preprocessing
from hisim.modular_household.interface_configs.system_config import SystemConfig
from hisim.modular_household.interface_configs.archetype_config import ArcheTypeConfig
from hisim.modular_household.interface_configs.modular_household_config import (
    ModularHouseholdConfig,
)
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulator import SimulationParameters


def read_in_configs(pathname: str) -> ModularHouseholdConfig:
    """Reads in ModularHouseholdConfig file and loads default if file cannot be found."""
    try:
        with open(pathname, encoding="utf8") as config_file:
            household_config: ModularHouseholdConfig = ModularHouseholdConfig.from_json(config_file.read())  # type: ignore
        hisim.log.information(f"Read modular household config from {pathname}")
    except Exception:
        household_config = ModularHouseholdConfig()
        hisim.log.warning(
            f"Could not read the modular household config from '{pathname}'. Using a default config instead."
        )

    # set default configs
    if household_config.system_config_ is None:
        household_config.system_config_ = SystemConfig()
    if household_config.archetype_config_ is None:
        household_config.archetype_config_ = ArcheTypeConfig()

    return household_config


def cleanup_old_result_folders():
    """
    Removes old result folders of previous modular_household_explicit
    simulations.
    """
    base_path = os.path.join(
        hisim.utils.hisim_abs_path, os.path.pardir, "examples", "results"
    )
    files_in_folder = os.listdir(base_path)
    for file in files_in_folder:
        if file.startswith("modular_household_explicit"):
            full_path = os.path.join(base_path, file)
            shutil.rmtree(full_path)


def modular_household_explicit(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: MC0001
    """Setup function emulates an household including the basic components.

    The configuration of the household is read in via the json input file "system_config.json".
    """
    # TODO: does not work in docker --> commented out for now
    # cleanup_old_result_folders()

    # Set simulation parameters
    year = 2018
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
    heater: List = []  # initialize list of components used for heating

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.january_only(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
        # my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_CARPET)
        # my_simulation_parameters.post_processing_options.append(PostProcessingOptions.GENERATE_PDF_REPORT)
        my_simulation_parameters.post_processing_options.append(
            PostProcessingOptions.COMPUTE_KPI
        )
        # my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
        # my_simulation_parameters.skip_finished_results = False

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # get archetype configuration
    location = weather.LocationEnum[arche_type_config_.location.value]
    occupancy_profile = arche_type_config_.occupancy_profile
    building_code = arche_type_config_.building_code
    water_heating_system_installed = (
        arche_type_config_.water_heating_system_installed
    )  # Electricity, Hydrogen or False
    heating_system_installed = arche_type_config_.heating_system_installed
    mobility_set = arche_type_config_.mobility_set
    mobility_distance = arche_type_config_.mobility_distance

    # get system configuration: technical equipment
    heatpump_included = system_config_.heatpump_included
    if heatpump_included:
        heating_system_installed = lt.HeatingSystems.HEAT_PUMP
        water_heating_system_installed = lt.HeatingSystems.HEAT_PUMP
    clever = my_simulation_parameters.surplus_control
    pv_included = system_config_.pv_included  # True or False
    if pv_included:
        pv_peak_power = system_config_.pv_peak_power
    smart_devices_included = system_config_.smart_devices_included  # True or False
    buffer_included = system_config_.buffer_included
    if buffer_included:
        buffer_volume = system_config_.buffer_volume
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
    utsp_connected = system_config_.utsp_connect

    """BASICS"""
    if utsp_connected:
        my_occupancy_config = (
            loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
                url=system_config_.url,
                api_key=system_config_.api_key,
                household=occupancy_profile,
                result_path=hisim.utils.HISIMPATH["results"],
                travel_route_set=mobility_distance,
                transportation_device_set=mobility_set,
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
            "Occupancy", occupancy_profile.Name or ""
        )
        my_occupancy = loadprofilegenerator_connector.Occupancy(
            config=my_occupancy_config,
            my_simulation_parameters=my_simulation_parameters,
        )

    """TODO: pass url and api, chose bettery directory or use inputs"""

    my_sim.add_component(my_occupancy)
    consumption.append(my_occupancy)

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=location)
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_weather)

    # Build building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home()
    my_building_config.building_code = building_code.value
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_sim.add_component(my_building)

    # load economic parameters:
    economic_parameters_file = path.join(
        hisim.utils.HISIMPATH["modular_household"], "EconomicParameters.json"
    )
    with open(file=economic_parameters_file, mode="r", encoding="utf-8") as inputfile:
        economic_parameters = json.load(inputfile)
    (
        pv_cost,
        smart_devices_cost,
        battery_cost,
        surplus_controller_cost,
        heatpump_cost,
        buffer_cost,
        chp_cost,
        h2_storage_cost,
        electrolyzer_cost,
        ev_cost,
    ) = [0] * 10

    # add price signal
    my_price_signal = generic_price_signal.PriceSignal(
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
        my_electricity_controller = (
            controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
                my_simulation_parameters=my_simulation_parameters
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

        surplus_controller_cost = (
            preprocessing.calculate_surplus_controller_investment_cost(
                economic_parameters
            )
        )

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
        #     print(capacity)
        #     ev_cost = ev_cost + preprocessing.calculate_electric_vehicle_investment_cost(economic_parameters, ev_included, ev_capacity=capacity)

    # """SMART CONTROLLER FOR SMART DEVICES"""
    # use clever controller if smart devices are included and do not use it if it is false
    if smart_devices_included and clever:
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
            count=count,
        )

    # """HEATING"""
    if buffer_included:
        if heating_system_installed in [
            lt.HeatingSystems.HEAT_PUMP,
            lt.HeatingSystems.ELECTRIC_HEATING,
        ]:
            (
                my_heater,
                my_buffer,
                count,
            ) = component_connections.configure_heating_with_buffer_electric(
                my_sim=my_sim,
                my_simulation_parameters=my_simulation_parameters,
                my_building=my_building,
                my_electricity_controller=my_electricity_controller,
                my_weather=my_weather,
                heating_system_installed=heating_system_installed,
                buffer_volume=buffer_volume,
                controlable=clever,
                count=count,
            )
            """TODO: repair! """
            # heatpump_cost = heatpump_cost + preprocessing.calculate_heating_investment_cost(economic_parameters, heatpump_included, my_heater.power_th)
        else:
            (
                my_heater,
                my_buffer,
                count,
            ) = component_connections.configure_heating_with_buffer(
                my_sim=my_sim,
                my_simulation_parameters=my_simulation_parameters,
                my_building=my_building,
                heating_system_installed=heating_system_installed,
                buffer_volume=buffer_volume,
                count=count,
            )

        buffer_cost = preprocessing.calculate_buffer_investment_cost(
            economic_parameters, buffer_included, buffer_volume
        )

    else:
        if heating_system_installed in [
            lt.HeatingSystems.HEAT_PUMP,
            lt.HeatingSystems.ELECTRIC_HEATING,
        ]:
            my_heater, count = component_connections.configure_heating_electric(
                my_sim=my_sim,
                my_simulation_parameters=my_simulation_parameters,
                my_building=my_building,
                my_electricity_controller=my_electricity_controller,
                my_weather=my_weather,
                heating_system_installed=heating_system_installed,
                controlable=clever,
                count=count,
            )
        else:
            my_heater, count = component_connections.configure_heating(
                my_sim=my_sim,
                my_simulation_parameters=my_simulation_parameters,
                my_building=my_building,
                heating_system_installed=heating_system_installed,
                count=count,
            )
    heater.append(my_heater)

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
        heater.append(my_chp)

        chp_cost = preprocessing.calculate_chp_investment_cost(
            economic_parameters, chp_included, chp_power
        )
        h2_storage_cost = preprocessing.calculate_h2storage_investment_cost(
            economic_parameters, h2_storage_included, h2_storage_size
        )
        electrolyzer_cost = preprocessing.calculate_electrolyzer_investment_cost(
            economic_parameters, electrolyzer_included, electrolyzer_power
        )

        chp_cost = preprocessing.calculate_chp_investment_cost(
            economic_parameters, chp_included, chp_power
        )
        h2_storage_cost = preprocessing.calculate_h2storage_investment_cost(
            economic_parameters, h2_storage_included, h2_storage_size
        )
        electrolyzer_cost = preprocessing.calculate_electrolyzer_investment_cost(
            economic_parameters, electrolyzer_included, electrolyzer_power
        )

    if buffer_included:
        my_buffer.add_component_inputs_and_connect(
            source_component_classes=heater,
            outputstring="ThermalPowerDelivered",
            source_load_type=lt.LoadTypes.HEATING,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.HEAT_TO_BUFFER],
            source_weight=999,
        )
    else:
        my_building.add_component_inputs_and_connect(
            source_component_classes=heater,
            outputstring="ThermalPowerDelivered",
            source_load_type=lt.LoadTypes.HEATING,
            source_unit=lt.Units.WATT,
            source_tags=[lt.InandOutputType.HEAT_TO_BUILDING],
            source_weight=999,
        )

    if needs_ems(
        battery_included,
        chp_included,
        ev_included,
        heating_system_installed,
        smart_devices_included,
        water_heating_system_installed,
    ):
        my_sim.add_component(my_electricity_controller)

    co2_cost = 1000  # CO2 von Herstellung der Komponenten plus CO2 für den Stromverbrauch der Komponenten
    injection = 1000
    autarky_rate = 1000
    self_consumption_rate = 1000
    surplus_controller_cost = 400

    investment_cost = preprocessing.total_investment_cost_threshold_exceedance_check(
        economic_parameters,
        pv_cost,
        smart_devices_cost,
        battery_cost,
        surplus_controller_cost,
        heatpump_cost,
        buffer_cost,
        chp_cost,
        h2_storage_cost,
        electrolyzer_cost,
        ev_cost,
    )
    preprocessing.investment_cost_per_component_exceedance_check(
        economic_parameters,
        pv_cost,
        smart_devices_cost,
        battery_cost,
        surplus_controller_cost,
        heatpump_cost,
        buffer_cost,
        chp_cost,
        h2_storage_cost,
        electrolyzer_cost,
        ev_cost,
    )

    hisim.log.information(
        "total investment_cost"
        + str(investment_cost)
        + "pv_cost"
        + str(pv_cost)
        + "smart_devices_cost"
        + str(smart_devices_cost)
        + "battery_cost"
        + str(battery_cost)
        + "surplus_controller_cost"
        + str(surplus_controller_cost)
        + "heatpump_cost"
        + str(heatpump_cost)
        + "buffer_cost"
        + str(buffer_cost)
        + "chp_cost"
        + str(chp_cost)
        + "h2_storage_cost"
        + str(h2_storage_cost)
        + "electrolyzer_cost"
        + str(electrolyzer_cost)
        + "ev_cost"
        + str(ev_cost)
    )


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
