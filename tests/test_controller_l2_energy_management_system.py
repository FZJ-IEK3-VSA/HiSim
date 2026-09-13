"""Test for energy management system.

Run a normal house with heatpumps, PV and battery and compare EMS outputs with KPI values.
Investigate total consumption, total grid consumption and grid injection.
"""

# clean

import os
import json
from typing import Optional
import pytest
import numpy as np
import pandas as pd
import hisim.component as cp
import hisim.simulator as sim
from hisim import json_generator
from hisim.config.channels import ResolvedDispatch, ResolvedDynamicConnection
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import (
    building,
    electricity_meter,
    generic_pv_system,
    heat_distribution_system,
    advanced_battery_bslib,
    controller_l2_energy_management_system,
    simple_water_storage,
    more_advanced_heat_pump_hplib
)
from hisim import utils
from hisim.config import ComponentID, SizingContext
import hisim.loadtypes as lt

from hisim.postprocessingoptions import PostProcessingOptions

# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../system_setups/household_for_test_ems.py"


@utils.measure_execution_time
@pytest.mark.extendedbase
def test_house(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """Run a one-week household simulation with an EMS and compare outputs to KPIs.

    Builds a residential energy system (building, occupancy loads via UTSP LPG,
    PV, space-heating and DHW heat pumps, hot-water storages, battery, and an
    L2 energy management system), runs the simulation, then reads KPI values
    from the post-processed ``all_kpis.json`` and asserts that the EMS
    time-series outputs for total electricity consumption, grid consumption,
    and grid injection agree with the corresponding KPI values and with the
    sum of per-component consumptions (5 % relative tolerance).

    Args:
        my_simulation_parameters: Optional simulation parameters. When None,
            defaults to one week of 2021 with 60-second timesteps and KPI
            post-processing options enabled.

    Raises:
        AssertionError: If any EMS output disagrees with the corresponding
            KPI value or component sum beyond a 5 % relative tolerance.
    """

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # =========================================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.one_week_only(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
        my_simulation_parameters.logging_level = 3
    # this part is copied from hisim_main
    # Build Simulator
    normalized_path = os.path.normpath(PATH)
    path_in_list = normalized_path.split(os.sep)
    if len(path_in_list) >= 1:
        path_to_be_added = os.path.join(os.getcwd(), *path_in_list[:-1])

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_for_test_ems",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # =================================================================================================================================
    # Build Components
    # Every component in this single-building setup carries no building of its own, so KPI
    # results are grouped under the default building label.
    building_label = ComponentID.DEFAULT_BUILDING_LABEL
    heating_reference_temperature_in_celsius = -7.0

    # Build Building
    # The weather config is created first: the building and PV configs copy its identity
    # (weather_identity) and must have it before those components are built. The weather
    # component itself is still added further down, so the simulator's component order is unchanged.
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)

    my_building_config = building.BuildingConfig.preset_standard("Building")
    my_building_config.heating_reference_temperature_in_celsius = heating_reference_temperature_in_celsius
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building_config.weather_identity = my_weather_config.identity()
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    # Add to simulator
    my_sim.add_component(my_building, connect_automatically=True)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )
    # Add to simulator
    my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)
    # Add to simulator
    my_sim.add_component(my_weather)

    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
        rooftop_area_in_m2=my_building_information.roof_area_in_m2,
        share_of_maximum_pv_potential=1.0,
        module_name="Hanwha HSL60P6-PA-4-250T [2013]",
        module_database=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE,
        inverter_name="ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_",
        inverter_database=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE)
    my_photovoltaic_system_config.weather_identity = my_weather_config.identity()
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,)
    # Add to simulator
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)

    # Build Heat Distribution Controller
    my_heat_distribution_controller_config = (
        heat_distribution_system.HeatDistributionControllerConfig.preset_standard(
            "HeatDistributionController"
        ).resolve(
            SizingContext(
                heating_load_in_watt=my_building_information.max_thermal_building_demand_in_watt,
                conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
                heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
                set_heating_temperature_in_celsius=(
                    my_building_information.set_heating_temperature_for_building_in_celsius
                ),
                set_cooling_temperature_in_celsius=(
                    my_building_information.set_cooling_temperature_for_building_in_celsius
                ),
            )
        )
    )
    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters,
        config=my_heat_distribution_controller_config,
    )
    my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
        config=my_heat_distribution_controller_config
    )
    # Add to simulator
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)

    # Build Heat Pump Controller for space heating
    my_heatpump_controller_sh_config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.get_default_space_heating_controller_config(
        heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type,
        set_heating_threshold_outside_temperature_in_celsius=my_hds_controller_information.set_heating_threshold_temperature_in_celsius,
    )

    my_heatpump_controller_sh = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
        config=my_heatpump_controller_sh_config, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_heatpump_controller_sh, connect_automatically=True)

    my_heatpump_controller_dhw_config = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHWConfig.get_default_dhw_controller_config()
    )

    # Build Heat Pump Controller for dhw
    my_heatpump_controller_dhw = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHW(
        config=my_heatpump_controller_dhw_config, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_heatpump_controller_dhw, connect_automatically=True)

    # Build Heat Pump (for dhw and space heating)
    my_heatpump_config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.get_scaled_advanced_hp_lib(
        heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
    )
    my_heatpump_config.with_domestic_hot_water_preparation = True

    my_heatpump = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib(
        config=my_heatpump_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Verknüpfung mit Luft als Umgebungswärmeqzuelle
    if my_heatpump.parameters["Group"].iloc[0] in (1.0, 4.0):
        my_heatpump.connect_input(
            my_heatpump.TemperatureInputPrimary,
            my_weather.component_name,
            my_weather.DailyAverageOutsideTemperatures,
        )
    else:
        raise KeyError(
            "Wasser oder Sole als primäres Wärmeträgermedium muss über extra Wärmenetz-Modell noch bereitgestellt werden"
        )
    # Add to simulator
    my_sim.add_component(my_heatpump, connect_automatically=True)

    # DHW storage configs
    my_dhw_storage_config = simple_water_storage.SimpleDHWStorageConfig.get_scaled_dhw_storage(
        number_of_apartments=my_building_information.number_of_apartments
    )

    my_dhw_storage = simple_water_storage.SimpleDHWStorage(
        my_simulation_parameters=my_simulation_parameters, config=my_dhw_storage_config
    )

    my_sim.add_component(my_dhw_storage, connect_automatically=True)

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
        max_thermal_power_in_watt_of_heating_system=my_heatpump_config.set_thermal_output_power_in_watt,
        sizing_option=simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP,
    )
    my_simple_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_simple_water_storage, connect_automatically=True)

    # Build Heat Distribution System
    my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig.preset_standard(
        "HeatDistributionSystem"
    ).resolve(
        SizingContext(
            water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kg_per_second,
            conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
            heat_distribution_system_type=my_hds_controller_information.hds_controller_config.heating_system,
    )
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_heat_distribution_system, connect_automatically=True)

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )

    # Build EMS
    my_electricity_controller_config = (
        controller_l2_energy_management_system.EMSConfig.preset_optimize_own_consumption(
            "L2EMSElectricityController"
        )
    )

    my_electricity_controller = controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
        my_simulation_parameters=my_simulation_parameters,
        config=my_electricity_controller_config,
    )

    # Build Battery
    my_advanced_battery_config = advanced_battery_bslib.BatteryConfig.get_scaled_battery(
        total_pv_power_in_watt_peak=my_photovoltaic_system_config.power_in_watt
    )
    my_advanced_battery = advanced_battery_bslib.Battery(
        my_simulation_parameters=my_simulation_parameters,
        config=my_advanced_battery_config,
    )

    # -----------------------------------------------------------------------------------------------------------------
    # Add outputs to EMS
    loading_power_input_for_battery_in_watt = my_electricity_controller.add_component_output(
        source_output_name="LoadingPowerInputForBattery_",
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=6,
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

    # =================================================================================================================================
    # Add Remaining Components to Simulation Parameters

    my_sim.add_component(my_electricity_meter)
    my_sim.add_component(my_advanced_battery)
    my_sim.add_component(my_electricity_controller, connect_automatically=True)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Compare EMS outputs and KPI values

    # Read kpi data
    with open(
        os.path.join(my_sim._simulation_parameters.result_directory, "all_kpis.json"), "r", encoding="utf-8"  # pylint: disable=W0212
    ) as file:
        jsondata = json.load(file)

    jsondata = jsondata[building_label]

    # Get general KPI values
    total_consumption_kpi_in_kilowatt_hour = jsondata["General"]["Total electricity consumption"].get("value")
    electricity_from_grid_kpi_in_kilowatt_hour = jsondata["Electricity Meter"]["Total energy from grid"].get("value")
    electricity_to_grid_kpi_in_kilowatt_hour = jsondata["Electricity Meter"]["Total energy to grid"].get("value")
    other_kpi_grid_injection_in_kilowatt_hour = jsondata["General"]["Grid injection of electricity"].get("value")

    # Get battery KPI values
    battery_charging_energy_in_kilowatt_hour = jsondata["Battery"]["Battery charging energy"].get("value")
    battery_discharging_energy_in_kilowatt_hour = jsondata["Battery"]["Battery discharging energy"].get("value")
    battery_losses_in_kilowatt_hour = jsondata["Battery"]["Battery losses"].get("value")
    print("battery charging energy ", battery_charging_energy_in_kilowatt_hour)
    print("battery discharging energy ", battery_discharging_energy_in_kilowatt_hour)
    print("battery losses ", battery_losses_in_kilowatt_hour)
    print("\n")

    # Get total consumptions of components
    residents_total_consumption_kpi_in_kilowatt_hour = jsondata["Residents"][
        "Residents' total electricity consumption"
    ].get("value")
    space_heating_heatpump_total_consumption_kpi_in_kilowatt_hour = jsondata["Heat Pump For Space Heating"][
        "Total electrical input energy of SH heat pump"
    ].get("value")
    domestic_hot_water_heatpump_total_consumption_kpi_in_kilowatt_hour = jsondata["Heat Pump For Domestic Hot Water"][
        "DHW heat pump total electricity consumption"
    ].get("value")

    sum_component_total_consumptions_in_kilowatt_hour = (
        residents_total_consumption_kpi_in_kilowatt_hour
        + space_heating_heatpump_total_consumption_kpi_in_kilowatt_hour
        + domestic_hot_water_heatpump_total_consumption_kpi_in_kilowatt_hour
        + battery_losses_in_kilowatt_hour
    )
    print("occupancy total consumption ", residents_total_consumption_kpi_in_kilowatt_hour)
    print("sh hp total consumption ", space_heating_heatpump_total_consumption_kpi_in_kilowatt_hour)
    print("dhw hp total consumption ", domestic_hot_water_heatpump_total_consumption_kpi_in_kilowatt_hour)
    print("sum of components' total consumptions ", sum_component_total_consumptions_in_kilowatt_hour)
    print("\n")

    # Get grid consumptions of components
    residents_grid_consumption_kpi_in_kilowatt_hour = jsondata["Energy Management System"][
        "Residents' electricity consumption from grid"
    ].get("value")
    space_heating_heatpump_grid_consumption_kpi_in_kilowatt_hour = jsondata["Energy Management System"][
        "Space heating heat pump electricity from grid"
    ].get("value")
    domestic_hot_water_heatpump_grid_consumption_kpi_in_kilowatt_hour = jsondata["Energy Management System"][
        "Domestic hot water heat pump electricity from grid"
    ].get("value")

    sum_component_grid_consumptions_in_kilowatt_hour = (
        residents_grid_consumption_kpi_in_kilowatt_hour
        + space_heating_heatpump_grid_consumption_kpi_in_kilowatt_hour
        + domestic_hot_water_heatpump_grid_consumption_kpi_in_kilowatt_hour
        - battery_discharging_energy_in_kilowatt_hour
    )

    print("occupancy grid consumption ", residents_grid_consumption_kpi_in_kilowatt_hour)
    print("sh hp grid consumption ", space_heating_heatpump_grid_consumption_kpi_in_kilowatt_hour)
    print("dhw hp grid consumption ", domestic_hot_water_heatpump_grid_consumption_kpi_in_kilowatt_hour)
    print("sum of components' grid consumptions ", sum_component_grid_consumptions_in_kilowatt_hour)
    print("\n")

    # Get EMS output TotalElectricityConsumption
    simulation_results_ems_total_consumption_in_watt = my_sim.results_data_frame[
        "L2EMSElectricityController - TotalElectricityConsumption [Electricity - W]"
    ]

    ems_total_consumption_in_kilowatt_hour = (
        simulation_results_ems_total_consumption_in_watt.sum() * seconds_per_timestep / 3.6e6
    )

    # Get EMS output ElectricityToOrFromGrid -> get grid consumption by filterig only values < 0
    simulation_results_ems_grid_consumption_in_watt = abs(
        my_sim.results_data_frame["L2EMSElectricityController - TotalElectricityToOrFromGrid [Electricity - W]"].loc[
            my_sim.results_data_frame["L2EMSElectricityController - TotalElectricityToOrFromGrid [Electricity - W]"]
            < 0.0
        ]
    )
    ems_grid_consumption_in_kilowatt_hour = (
        simulation_results_ems_grid_consumption_in_watt.sum() * seconds_per_timestep / 3.6e6
    )

    # Get EMS output ElectricityToOrFromGrid -> get grid injection by filterig only values > 0
    simulation_results_ems_grid_injection_in_watt = my_sim.results_data_frame[
        "L2EMSElectricityController - TotalElectricityToOrFromGrid [Electricity - W]"].loc[
        my_sim.results_data_frame["L2EMSElectricityController - TotalElectricityToOrFromGrid [Electricity - W]"] > 0.0
    ]
    ems_grid_injection_in_kilowatt_hour = (
        simulation_results_ems_grid_injection_in_watt.sum() * seconds_per_timestep / 3.6e6
    )

    # =========================================================================================================================================================
    # Test total electricity consumption
    print("ems total consumption ", ems_total_consumption_in_kilowatt_hour)
    print("kpi total consumption ", total_consumption_kpi_in_kilowatt_hour)
    print("sum of components' total consumptions ", sum_component_total_consumptions_in_kilowatt_hour)
    print("\n")
    np.testing.assert_allclose(
        ems_total_consumption_in_kilowatt_hour,
        total_consumption_kpi_in_kilowatt_hour,
        rtol=0.05,
    )
    np.testing.assert_allclose(
        total_consumption_kpi_in_kilowatt_hour,
        sum_component_total_consumptions_in_kilowatt_hour,
        rtol=0.05,
    )

    # Test grid consumption
    print("ems grid consumption ", ems_grid_consumption_in_kilowatt_hour)
    print("em grid consumption ", electricity_from_grid_kpi_in_kilowatt_hour)
    print("sum of components' grid consumptions ", sum_component_grid_consumptions_in_kilowatt_hour)
    print("\n")
    np.testing.assert_allclose(
        ems_grid_consumption_in_kilowatt_hour,
        electricity_from_grid_kpi_in_kilowatt_hour,
        rtol=0.05,
    )
    np.testing.assert_allclose(
        electricity_from_grid_kpi_in_kilowatt_hour,
        sum_component_grid_consumptions_in_kilowatt_hour,
        rtol=0.05,
    )

    # Test grid injection
    print("ems grid injection ", ems_grid_injection_in_kilowatt_hour)
    print("em grid injection ", electricity_to_grid_kpi_in_kilowatt_hour)
    print("other kpi grid injection ", other_kpi_grid_injection_in_kilowatt_hour)

    print("\n")
    np.testing.assert_allclose(
        ems_grid_injection_in_kilowatt_hour,
        electricity_to_grid_kpi_in_kilowatt_hour,
        rtol=0.05,
    )
    np.testing.assert_allclose(
        electricity_to_grid_kpi_in_kilowatt_hour,
        other_kpi_grid_injection_in_kilowatt_hour,
        rtol=0.05,
    )


def _energy_manager() -> controller_l2_energy_management_system.L2GenericEnergyManagementSystem:
    """Builds a bare energy manager, the way every setup with one does.

    Returns:
        A freshly constructed controller, before anything has been wired to it.
    """
    manager: controller_l2_energy_management_system.L2GenericEnergyManagementSystem = (
        controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
            my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15),
            config=controller_l2_energy_management_system.EMSConfig.preset_optimize_own_consumption(
                "L2EMSElectricityController"
            ),
        )
    )
    return manager


@pytest.mark.base
def test_the_manager_grows_no_target_output_until_something_is_wired_to_it() -> None:
    """Catches the manager declaring target ports for devices the house does not have.

    Asking a default connection to *describe* itself used to create the target output as a side
    effect, and the constructor asks all six. A district-heated house therefore carried five
    target ports — two for a heat pump, two for an electric heater, one for a solar collector —
    wired to nothing, and because the ports were named after their position in the list, deleting
    one dead device renamed every port after it in every setup (F-1).
    """
    manager = _energy_manager()

    assert manager.my_component_outputs == []
    assert [output.field_name for output in manager.outputs if "ElectricityToOrFromGridOf" in output.field_name] == []


@pytest.mark.base
def test_a_present_device_gets_its_target_output_where_its_input_is_created() -> None:
    """Catches the target output going missing for a device that is there, or being misnamed.

    This is the other half: what the simulator does for a source component the setup actually
    built. Both of the heat pump's feeds are wired and both of its target ports appear, named
    after what they steer and the weight they are steered on — and nothing belonging to the
    electric heater or the solar collector comes with them.
    """
    manager = _energy_manager()
    connections = manager.dynamic_default_connections["MoreAdvancedHeatPumpHPLib"]
    for connection in connections:
        connection.source_instance_name = "MoreAdvancedHeatPumpHPLib"

    manager.connect_with_dynamic_connections_list(connections)

    grown = [output.field_name for output in manager.outputs if "ElectricityToOrFromGridOf" in output.field_name]
    assert grown == [
        "ElectricityToOrFromGridOfSHMoreAdvancedHeatPumpHPLib_2",
        "ElectricityToOrFromGridOfDHWMoreAdvancedHeatPumpHPLib_3",
    ]
    assert [entry.source_weight for entry in manager.my_component_outputs] == [2, 3]


@pytest.mark.base
def test_two_target_outputs_of_one_name_are_refused() -> None:
    """Catches a port name that is no longer an identity.

    A target port is named after what it steers and the weight it steers on, so two ports of one
    name would be one and the same port to every tag-and-weight lookup the dispatch uses. That is
    a setup wiring one participant twice, or two participants sharing a weight, and it has to say
    so rather than be absorbed by a counter.
    """
    manager = _energy_manager()

    def add_the_battery_target() -> None:
        """Adds the battery target port the ten energy-manager sizers add by hand."""
        manager.add_component_output(
            source_output_name="LoadingPowerInputForBattery_",
            source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
            source_weight=6,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for Battery Control. ",
        )

    add_the_battery_target()
    assert manager.outputs[-1].field_name == "LoadingPowerInputForBattery_6"

    with pytest.raises(ValueError, match="LoadingPowerInputForBattery_6"):
        add_the_battery_target()


@pytest.mark.base
def test_the_kpi_block_finds_a_dispatch_port_that_is_not_named_after_a_class() -> None:
    """Catches a per-participant KPI going missing because the port carries no class name.

    The KPI block used to find the residents' electricity target by looking for the string
    ``UtspLpgConnector`` inside the port's name, which only the legacy wiring puts there: an
    energy-system file names the very same port ``DispatchFor<instance>_<output>``. While the
    manager still declared a target port for every device it might ever meet, the substring found
    that phantom and the KPI came out anyway; once the phantoms went, five declarative houses
    silently lost a KPI their golden expects. The port is found by the tags it carries instead,
    which is what the dispatch itself steers by.
    """
    manager = _energy_manager()
    dispatch_output = manager.add_component_output(
        source_output_name="DispatchForUTSPConnector_",
        source_tags=[lt.ComponentType.RESIDENTS, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=1,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for Occupancy. ",
    )
    assert "UtspLpgConnector" not in dispatch_output.field_name
    results = pd.DataFrame({dispatch_output.field_name: [-1000.0, -1000.0]})

    kpi_entries = manager.get_component_kpi_entries(all_outputs=[dispatch_output], postprocessing_results=results)

    assert [entry.name for entry in kpi_entries] == ["Residents' electricity consumption from grid"]
    assert kpi_entries[0].name_of_source_component == "UtspLpgConnector"
    assert kpi_entries[0].unit == "kWh"
    assert kpi_entries[0].value == pytest.approx(0.5)


def _heat_pump(
    name: str, with_domestic_hot_water_preparation: bool = False
) -> more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib:
    """Builds a heat pump, the cheapest participant the energy manager steers by default.

    Args:
        name: The instance name, which is also what the simulator knows the component by.
        with_domestic_hot_water_preparation: Whether the device prepares domestic hot water. A
            device that does not publishes no DHW electrical power, which is the configuration
            the manager must not grow a DHW target for.

    Returns:
        The heat pump.
    """
    config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.get_default_generic_advanced_hp_lib(
        component_id=ComponentID(name=name)
    )
    config.with_domestic_hot_water_preparation = with_domestic_hot_water_preparation
    heat_pump: more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib(
            my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15),
            config=config,
        )
    )
    return heat_pump


def _simulator(result_directory: str) -> sim.Simulator:
    """Builds a simulator that writes nothing but its own directory.

    Args:
        result_directory: A directory of the test's own, used as both module and result directory.

    Returns:
        The simulator.
    """
    simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15)
    simulation_parameters.result_directory = result_directory
    my_sim: sim.Simulator = sim.Simulator(
        module_directory=result_directory,
        module_filename="household_for_test_grown_ports",
        my_simulation_parameters=simulation_parameters,
    )
    my_sim.set_simulation_parameters(simulation_parameters)
    return my_sim


@pytest.mark.base
def test_a_port_grown_while_wiring_becomes_a_result_column(tmp_path) -> None:
    """Catches a grown dispatch port that never reaches the values vector it is written into.

    The port is created after the component was registered, so nothing but the wiring pass gives
    it a global index; a port left without one would silently write into index 0 or off the end
    of the vector. This drives the whole seam — the simulator's automatic connection, the
    wrapper's registration of what grew, and the sizing that follows — rather than the component
    method alone.
    """
    my_sim = _simulator(str(tmp_path))
    my_sim.add_component(_heat_pump("HeatPump"))
    my_sim.add_component(_energy_manager(), connect_automatically=True)

    my_sim.prepare_calculation()

    grown = [
        output
        for output in my_sim.all_outputs
        if output.field_name == "ElectricityToOrFromGridOfSHMoreAdvancedHeatPumpHPLib_2"
    ]
    assert len(grown) == 1
    assert [output.global_index for output in my_sim.all_outputs] == list(range(len(my_sim.all_outputs)))
    values = cp.SingleTimeStepValues(len(my_sim.all_outputs))
    values.set_output_value(grown[0], 42.0)
    assert values.values[grown[0].global_index] == 42.0


@pytest.mark.base
def test_two_instances_of_one_steered_class_are_refused_by_name(tmp_path) -> None:
    """Catches the two-instance case being absorbed instead of refused, or refused mutely.

    A default connection names its target after the participant's class and the one weight that
    class is dispatched on, so two instances of one class ask for one port. That is refused, and
    the refusal has to say what happened and where to go instead: hand wiring with a weight per
    instance, or the declarative energy-system format, which gives every feed its own weight.
    """
    my_sim = _simulator(str(tmp_path))
    my_sim.add_component(_heat_pump("HeatPumpA"))
    my_sim.add_component(_heat_pump("HeatPumpB"))
    my_sim.add_component(_energy_manager(), connect_automatically=True)

    with pytest.raises(ValueError, match="ElectricityToOrFromGridOfSHMoreAdvancedHeatPumpHPLib_2") as refusal:
        my_sim.prepare_calculation()

    message = str(refusal.value)
    assert "Two components of the class 'MoreAdvancedHeatPumpHPLib'" in message
    assert "its own source weight" in message
    assert "declarative energy-system format" in message


@pytest.mark.base
def test_a_device_that_publishes_no_dhw_power_grows_no_dhw_target() -> None:
    """Catches a dispatch port grown for a flow the device does not have.

    A heat pump that prepares no domestic hot water publishes no DHW electrical power, which is
    why that feed is allowed to stay unconnected. Wiring it anyway gives the run a measurement
    that reads zero forever and a target column nobody steers — the phantom ports F-1 removed,
    back one flow at a time. Both halves have to go together: the manager ranks every feed it
    has and pairs each with a target of the same weight, so a feed without its target would make
    the ranking refuse the run at the first timestep.
    """
    without_dhw = _energy_manager()
    heat_pump = _heat_pump("HeatPump")
    assert "ElectricalInputPowerDHW" not in [output.field_name for output in heat_pump.outputs]

    without_dhw.connect_with_dynamic_connections_list(without_dhw.get_dynamic_default_connections(heat_pump))

    assert [
        output.field_name for output in without_dhw.outputs if "ElectricityToOrFromGridOf" in output.field_name
    ] == ["ElectricityToOrFromGridOfSHMoreAdvancedHeatPumpHPLib_2"]
    assert [entry.source_component_field_name for entry in without_dhw.my_component_inputs] == [
        "ElectricalInputPowerSH"
    ]

    with_dhw = _energy_manager()
    dhw_heat_pump = _heat_pump("HeatPump", with_domestic_hot_water_preparation=True)

    with_dhw.connect_with_dynamic_connections_list(with_dhw.get_dynamic_default_connections(dhw_heat_pump))

    assert [entry.source_component_field_name for entry in with_dhw.my_component_inputs] == [
        "ElectricalInputPowerSH",
        "ElectricalInputPowerDHW",
    ]
    assert [
        output.field_name for output in with_dhw.outputs if "ElectricityToOrFromGridOf" in output.field_name
    ] == [
        "ElectricityToOrFromGridOfSHMoreAdvancedHeatPumpHPLib_2",
        "ElectricityToOrFromGridOfDHWMoreAdvancedHeatPumpHPLib_3",
    ]


def _resolved_feed_of(heat_pump: more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib) -> ResolvedDynamicConnection:
    """Builds the resolved feed an energy-system file produces for a steered participant.

    Args:
        heat_pump: The participant the feed measures.

    Returns:
        A feed whose dispatch block names no target input, so its port is named by the
        ``DispatchFor`` template.
    """
    return ResolvedDynamicConnection(
        source_name=heat_pump.component_name,
        source_component=heat_pump,
        source_output=more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH,
        source_port=heat_pump.outputs[0],
        target_name="L2EMSElectricityController",
        component_type=lt.ComponentType.HEAT_PUMP_BUILDING,
        flow_tags=(lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,),
        weight=2,
        channel=controller_l2_energy_management_system.L2GenericEnergyManagementSystem.get_channel(
            controller_l2_energy_management_system.L2GenericEnergyManagementSystem.CONSUMPTION_CONTROLLED_CHANNEL
        ),
        origin="a test's feed",
        dispatch=ResolvedDispatch(
            target_input=None,
            tags=(lt.ComponentType.HEAT_PUMP_BUILDING, lt.InandOutputType.ELECTRICITY_TARGET),
        ),
    )


@pytest.mark.base
def test_the_scenario_json_writes_the_targets_a_setup_made_and_no_others() -> None:
    """Catches the scenario JSON writing a port twice, losing one, or writing a garbled name.

    The file is the legacy path's own: the JSON executor applies the same default connections
    when it rebuilds the component, so a target grown from one must not be written down, while
    every target the setup added by hand must be — under the prefix it was added with. Both
    answers are read off the port's own bookkeeping now, which is also why a port named by the
    declarative format's templates, having no prefix at all, is refused by name instead of
    written as whatever the arithmetic made of it.
    """
    manager = _energy_manager()
    manager.add_component_output(
        source_output_name="LoadingPowerInputForBattery_",
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=6,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for Battery Control. ",
    )
    heat_pump = _heat_pump("HeatPump")
    manager.connect_with_dynamic_connections_list(manager.get_dynamic_default_connections(heat_pump))

    written, _, _ = json_generator.convert_component_to_json(manager.config, manager)

    assert [(out["source_output_name"], out["source_weight"]) for out in written.outputs] == [
        ("LoadingPowerInputForBattery_", 6)
    ]

    declarative_manager = _energy_manager()
    dispatch_output = declarative_manager.add_resolved_dispatch_output(_resolved_feed_of(heat_pump))
    assert dispatch_output.field_name == "DispatchForHeatPump_ElectricalInputPowerSH"

    with pytest.raises(ValueError, match=dispatch_output.field_name):
        json_generator.convert_component_to_json(declarative_manager.config, declarative_manager)
