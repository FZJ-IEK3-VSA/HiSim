"""Test for energy management system.

Run a normal house with heatpumps, PV and battery and compare EMS outputs with KPI values.
Investigate total consumption, total grid consumption and grid injection.
"""

import os
import json
from dataclasses import dataclass
from typing import List, Optional, Tuple, cast
import pytest
import numpy as np
import pandas as pd
import hisim.component as cp
import hisim.simulator as sim
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
from hisim.config import ComponentID, DisplayConfig, SizingContext, concrete
from hisim.postprocessing.kpi_computation.kpi_address import KpiFinder
from hisim.postprocessing.kpi_computation.kpi_structure import KpiSource
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
    my_weather_config = weather.WeatherConfig.preset_aachen("Weather")

    my_building_config = building.BuildingConfig.preset_german_single_family_home("Building")
    my_building_config.weather_identity = my_weather_config.identity()
    # The design outside temperature is the weather's, not the building's: the building reads
    # it as a sized field (D-21), so it is resolved before the component is built.
    my_building_config = my_building_config.resolve(
        SizingContext(heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius)
    )
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    # Add to simulator
    my_sim.add_component(my_building, connect_automatically=True)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.preset_couple_both_at_work("UTSPConnector")
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
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.preset_rooftop("PVSystem")
    my_photovoltaic_system_config.module_name = "Hanwha HSL60P6-PA-4-250T [2013]"
    my_photovoltaic_system_config.module_database = (
        generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE
    )
    my_photovoltaic_system_config.inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    my_photovoltaic_system_config.inverter_database = (
        generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE
    )
    my_photovoltaic_system_config = my_photovoltaic_system_config.resolve(
        SizingContext(
            roof_area_in_m2=my_building_information.roof_area_in_m2,
            weather_identity=my_weather_config.identity(),
        )
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,)
    # Add to simulator
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)

    # Build Heat Distribution Controller
    my_heat_distribution_controller_config = (
        heat_distribution_system.HeatDistributionControllerConfig.preset_building_derived(
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
    my_heatpump_controller_sh_config = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard(
            "MoreAdvancedHeatPumpHPLibControllerSH"
        ).resolve(
            SizingContext(
                set_heating_temperature_in_celsius=concrete(
                    my_heat_distribution_controller_config.set_heating_temperature_for_building_in_celsius
                ),
                heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type,
                set_heating_threshold_outside_temperature_in_celsius=(
                    my_hds_controller_information.set_heating_threshold_temperature_in_celsius
                ),
            )
        )
    )

    my_heatpump_controller_sh = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
        config=my_heatpump_controller_sh_config, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_heatpump_controller_sh, connect_automatically=True)

    my_heatpump_controller_dhw_config = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard(
            "HeatPumpControllerDHW"
        )
    )

    # Build Heat Pump Controller for dhw
    my_heatpump_controller_dhw = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHW(
        config=my_heatpump_controller_dhw_config, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_heatpump_controller_dhw, connect_automatically=True)

    # Build Heat Pump (for dhw and space heating)
    my_heatpump_config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.preset_air_water(
        "MoreAdvancedHeatPumpHPLib"
    ).resolve(
        SizingContext(
            heating_load_in_watt=my_building_information.max_thermal_building_demand_in_watt,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
        )
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
    my_dhw_storage_config = simple_water_storage.SimpleDHWStorageConfig.preset_standard("DHWStorage").resolve(
        SizingContext(number_of_apartments=my_building_information.number_of_apartments)
    )

    my_dhw_storage = simple_water_storage.SimpleDHWStorage(
        my_simulation_parameters=my_simulation_parameters, config=my_dhw_storage_config
    )

    my_sim.add_component(my_dhw_storage, connect_automatically=True)

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.preset_buffer(
        "SimpleHotWaterStorage"
    )
    # The litres-per-kilowatt figure is per kind of generator, and the volume law reads the field,
    # so the option is set on the preset before the configuration is resolved.
    my_simple_heat_water_storage_config.sizing_option = (
        simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP
    )
    my_simple_heat_water_storage_config = my_simple_heat_water_storage_config.resolve(
        SizingContext(
            maximal_thermal_power_in_watt=concrete(my_heatpump_config.set_thermal_output_power_in_watt)
        )
    )
    my_simple_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_simple_water_storage, connect_automatically=True)

    # Build Heat Distribution System
    my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig.preset_building_derived(
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
        config=electricity_meter.ElectricityMeterConfig.preset_standard("ElectricityMeter"),
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
    my_advanced_battery_config = advanced_battery_bslib.BatteryConfig.preset_sized_to_pv("Battery").resolve(
        SizingContext(pv_peak_power_in_watt=(concrete(my_photovoltaic_system_config.power_in_watt),))
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

    finder = KpiFinder(jsondata)

    # Get general KPI values
    total_consumption_kpi_in_kilowatt_hour = finder.value(
        building=building_label, tag="General", name="Total electricity consumption"
    )
    electricity_from_grid_kpi_in_kilowatt_hour = finder.value(
        building=building_label, tag="Electricity Meter", name="Total energy from grid"
    )
    electricity_to_grid_kpi_in_kilowatt_hour = finder.value(
        building=building_label, tag="Electricity Meter", name="Total energy to grid"
    )
    other_kpi_grid_injection_in_kilowatt_hour = finder.value(
        building=building_label, tag="General", name="Grid injection of electricity"
    )

    # Get battery KPI values
    battery_charging_energy_in_kilowatt_hour = finder.value(
        building=building_label, tag="Battery", name="Battery charging energy"
    )
    battery_discharging_energy_in_kilowatt_hour = finder.value(
        building=building_label, tag="Battery", name="Battery discharging energy"
    )
    battery_losses_in_kilowatt_hour = finder.value(building=building_label, tag="Battery", name="Battery losses")
    print("battery charging energy ", battery_charging_energy_in_kilowatt_hour)
    print("battery discharging energy ", battery_discharging_energy_in_kilowatt_hour)
    print("battery losses ", battery_losses_in_kilowatt_hour)
    print("\n")

    # Get total consumptions of components
    residents_total_consumption_kpi_in_kilowatt_hour = finder.value(
        building=building_label, tag="Residents", name="Residents' total electricity consumption"
    )
    space_heating_heatpump_total_consumption_kpi_in_kilowatt_hour = finder.value(
        building=building_label, tag="Heat Pump For Space Heating", name="Total electrical input energy of SH heat pump"
    )
    domestic_hot_water_heatpump_total_consumption_kpi_in_kilowatt_hour = finder.value(
        building=building_label, tag="Heat Pump For Domestic Hot Water", name="DHW heat pump total electricity consumption"
    )

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
    residents_grid_consumption_kpi_in_kilowatt_hour = finder.value(
        building=building_label, tag="Energy Management System", name="Residents' electricity consumption from grid"
    )
    space_heating_heatpump_grid_consumption_kpi_in_kilowatt_hour = finder.value(
        building=building_label, tag="Energy Management System", name="Space heating heat pump electricity from grid"
    )
    domestic_hot_water_heatpump_grid_consumption_kpi_in_kilowatt_hour = finder.value(
        building=building_label, tag="Energy Management System", name="Domestic hot water heat pump electricity from grid"
    )

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
    residents_output = _wire_measured_participant(manager, "UTSPConnector", lt.ComponentType.RESIDENTS, 1)
    results = pd.DataFrame({dispatch_output.field_name: [-1000.0, -1000.0]})

    kpi_entries = manager.component_kpi_entries(all_outputs=[dispatch_output], postprocessing_results=results)

    assert [entry.name for entry in kpi_entries] == ["Residents' electricity consumption from grid"]
    expected_source = KpiSource.for_component(residents_output.component_id, DisplayConfig.show("Residents"))
    assert kpi_entries[0].source == expected_source
    assert kpi_entries[0].source.display_name == "Residents"
    assert kpi_entries[0].name_of_source_component == "UTSPConnector"
    assert kpi_entries[0].unit == "kWh"
    assert kpi_entries[0].value == pytest.approx(0.5)


def _wire_measured_participant(
    manager: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
    participant_name: str,
    component_type: lt.ComponentType,
    weight: int,
) -> cp.ComponentOutput:
    """Feeds the manager the participant's consumption, as the default connections do.

    The ComponentWrapper resolves the input's source output when the simulator wires the run; the
    test does that step by hand, since it builds no simulator.

    Returns:
        The participant's output the manager's input is connected to.
    """
    participant_output = cp.ComponentOutput(
        participant_name,
        "ElectricalPowerConsumption",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
        output_description="the participant's consumption",
        component_id=ComponentID(name=participant_name),
        display_config=DisplayConfig.show("Residents"),
    )
    manager.add_component_input_and_connect(
        source_object_name=participant_name,
        source_component_output="ElectricalPowerConsumption",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[component_type, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
        source_weight=weight,
    )
    manager.inputs[-1].source_output = participant_output
    return participant_output


@pytest.mark.base
def test_a_grid_share_kpi_names_no_participant_it_cannot_find() -> None:
    """Catches a dispatch KPI reported for a participant nothing identifies.

    The grid share of a dispatch is reported on behalf of the participant, which the manager finds
    through the input sharing the dispatch's weight and component type. A dispatch with no such
    input has no participant to name; it used to be credited to a class name instead, which is no
    component of the run at all.
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
    results = pd.DataFrame({dispatch_output.field_name: [-1000.0, -1000.0]})

    with pytest.raises(ValueError, match="pairs with 0 inputs"):
        manager.component_kpi_entries(all_outputs=[dispatch_output], postprocessing_results=results)


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
    config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.preset_air_water(name)
    # No building in this test, so the two fields the preset leaves to the building's facts are
    # stated here: an 8 kW machine rated at the German design outside temperature.
    config.set_thermal_output_power_in_watt = 8000.0
    config.heating_reference_temperature_in_celsius = -7.0
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
def test_a_meter_connected_automatically_beside_the_manager_reads_its_grid_balance_once(tmp_path) -> None:
    """Catches the meter's declared feed from the manager's balance landing twice, or not at all.

    The real ``ElectricityMeter`` declares a dynamic default connection from the real manager's
    ``TotalElectricityToOrFromGrid``; with both added with ``connect_automatically=True`` the
    simulator resolves it to exactly one input of the meter.
    """
    my_sim = _simulator(str(tmp_path))
    my_sim.add_component(_heat_pump("HeatPump"))
    manager = _energy_manager()
    my_sim.add_component(manager, connect_automatically=True)
    meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15),
        config=electricity_meter.ElectricityMeterConfig.preset_standard("ElectricityMeter"),
    )
    my_sim.add_component(meter, connect_automatically=True)

    my_sim.prepare_calculation()

    balance = controller_l2_energy_management_system.L2GenericEnergyManagementSystem.TotalElectricityToOrFromGrid
    feeds = [item for item in meter.inputs if item.src_field_name == balance]
    assert [(item.src_object_name, item.src_field_name) for item in feeds] == [(manager.component_name, balance)]


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


class TestSurplusHysteresis:

    """The energy manager raises the set temperatures on a PV surplus, with a hysteresis band around zero."""

    BAND_IN_WATT = controller_l2_energy_management_system.L2GenericEnergyManagementSystem.SURPLUS_HYSTERESIS_BAND_IN_WATT

    #: The transition table: surplus after the battery in W, the previous step's decision, the decision.
    TRANSITIONS = [
        (500.0, False, True),
        (500.0, True, True),
        (0.0, False, False),
        (0.0, True, True),
        (5e-12, False, False),
        (-5e-12, True, True),
        (BAND_IN_WATT, False, False),
        (-BAND_IN_WATT, True, True),
        (BAND_IN_WATT * 1.5, False, True),
        (-BAND_IN_WATT * 1.5, True, False),
        (-500.0, False, False),
        (-500.0, True, False),
    ]

    @pytest.mark.base
    @pytest.mark.parametrize(("surplus_in_watt", "raised_in_previous_step", "expected"), TRANSITIONS)
    def test_every_transition_of_the_table(self, surplus_in_watt: float, raised_in_previous_step: bool, expected: bool) -> None:
        """Catches a decision that lags a real surplus, or that follows the sign of float noise inside the band.

        Outside the band the sign decides at once; inside it, edges included, the previous step's decision holds.
        """
        decision = controller_l2_energy_management_system.L2GenericEnergyManagementSystem.raises_set_temperatures(
            surplus_after_battery_in_watt=surplus_in_watt,
            raised_in_previous_step=raised_in_previous_step,
        )
        assert decision is expected

    @pytest.mark.base
    @pytest.mark.parametrize("surplus_in_watt", [float("nan"), float("inf"), float("-inf")])
    def test_a_surplus_that_is_not_finite_is_refused(self, surplus_in_watt: float) -> None:
        """Catches a NaN surplus passing as "within the band" and silently keeping the previous decision."""
        with pytest.raises(ValueError, match="finite"):
            controller_l2_energy_management_system.L2GenericEnergyManagementSystem.raises_set_temperatures(
                surplus_after_battery_in_watt=surplus_in_watt, raised_in_previous_step=False
            )

    #: The battery's share: the surplus offered, the maximum and minimum charging powers in W, and the share in W.
    SHARES = [
        (1000.0, 5000.0, 200.0, 1000.0),
        (8000.0, 5000.0, 200.0, 5000.0),
        (5000.0, 5000.0, 200.0, 5000.0),
        (150.0, 5000.0, 200.0, 0.0),
        (200.0, 5000.0, 200.0, 0.0),
        (200.5, 5000.0, 200.0, 200.5),
        (1000.0, 150.0, 200.0, 0.0),
        (1000.0, 0.0, 200.0, 0.0),
        (0.0, 5000.0, 200.0, 0.0),
        (-300.0, 5000.0, 200.0, 0.0),
        (-8000.0, 5000.0, 0.0, 0.0),
    ]

    @pytest.mark.base
    @pytest.mark.parametrize(("surplus_in_watt", "maximum_in_watt", "minimum_in_watt", "expected_in_watt"), SHARES)
    def test_every_battery_share_of_the_table(
        self, surplus_in_watt: float, maximum_in_watt: float, minimum_in_watt: float, expected_in_watt: float
    ) -> None:
        """Catches a share beyond the battery's limits, a share below its minimum, or a battery that covers a deficit.

        A battery counts only as a sink: whatever it could discharge, it covers no deficit in the decision.
        """
        share_in_watt = controller_l2_energy_management_system.L2GenericEnergyManagementSystem.battery_charging_share_in_watt(
            surplus_in_watt=surplus_in_watt,
            maximum_charging_power_in_watt=maximum_in_watt,
            minimum_charging_power_in_watt=minimum_in_watt,
        )
        assert share_in_watt == expected_in_watt

    @pytest.mark.base
    @pytest.mark.parametrize(
        ("surplus_in_watt", "maximum_in_watt", "minimum_in_watt"),
        [(float("nan"), 5000.0, 200.0), (100.0, float("inf"), 200.0), (100.0, -1.0, 200.0), (100.0, 5000.0, -1.0)],
    )
    def test_an_invalid_battery_limit_is_refused(self, surplus_in_watt: float, maximum_in_watt: float, minimum_in_watt: float) -> None:
        """Catches a NaN or negative limit turning into a share that silently decides the raise."""
        with pytest.raises(ValueError):
            controller_l2_energy_management_system.L2GenericEnergyManagementSystem.battery_charging_share_in_watt(
                surplus_in_watt=surplus_in_watt,
                maximum_charging_power_in_watt=maximum_in_watt,
                minimum_charging_power_in_watt=minimum_in_watt,
            )

    @staticmethod
    def manager_with_pv_and_hot_water_heat_pump() -> Tuple[
        controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
        cp.ComponentOutput,
        cp.SingleTimeStepValues,
    ]:
        """Build an energy manager fed by a PV system and a hot-water heat pump, and step values for it.

        The PV system's production is a measured input; the heat pump is the one participant the manager steers. The
        test wires the ports by hand and gives every output its index, as the simulator would.

        Returns:
            The manager, the PV system's electricity output in W, and the step values.
        """
        manager, outputs = TestSurplusHysteresis.manager_with_pv_and_heat_pump_outputs()
        return manager, outputs.pv, TestSurplusHysteresis.indexed_step_values(manager, outputs)

    @dataclass
    class ParticipantOutputs:
        """The outputs of the participants a test manager reads, each a port of a stand-in component."""

        pv: cp.ComponentOutput
        heat_pump: cp.ComponentOutput
        battery: Optional[cp.ComponentOutput] = None
        battery_maximum_charging_power: Optional[cp.ComponentOutput] = None
        battery_minimum_charging_power: Optional[cp.ComponentOutput] = None

        def all(self) -> List[cp.ComponentOutput]:
            """Return every participant output that exists, in a fixed order."""
            candidates = [
                self.pv,
                self.heat_pump,
                self.battery,
                self.battery_maximum_charging_power,
                self.battery_minimum_charging_power,
            ]
            return [output for output in candidates if output is not None]

    @staticmethod
    def manager_with_pv_and_heat_pump_outputs() -> Tuple[
        controller_l2_energy_management_system.L2GenericEnergyManagementSystem, "TestSurplusHysteresis.ParticipantOutputs"
    ]:
        """Build an energy manager fed by a PV system and a hot-water heat pump steered at weight 3.

        Returns:
            The manager and the participants' outputs.
        """
        manager = _energy_manager()
        pv_output = cp.ComponentOutput(
            "PV",
            "ElectricityOutput",
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="the PV system's production",
            component_id=ComponentID(name="PV"),
        )
        manager.add_component_input_and_connect(
            source_object_name="PV",
            source_component_output="ElectricityOutput",
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.ComponentType.PV, lt.InandOutputType.ELECTRICITY_PRODUCTION],
            source_weight=999,
        )
        manager.inputs[-1].source_output = pv_output
        heat_pump_output = _wire_measured_participant(manager, "HeatPump", lt.ComponentType.HEAT_PUMP_DHW, 3)
        manager.add_component_output(
            source_output_name="ElectricityToOrFromGridOfDHWHeatPump_",
            source_tags=[lt.ComponentType.HEAT_PUMP_DHW, lt.InandOutputType.ELECTRICITY_TARGET],
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_weight=3,
            output_description="Target electricity for the hot-water heat pump.",
        )
        return manager, TestSurplusHysteresis.ParticipantOutputs(pv=pv_output, heat_pump=heat_pump_output)

    @staticmethod
    def wire_battery(
        manager: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
        outputs: "TestSurplusHysteresis.ParticipantOutputs",
        with_limits: bool = True,
    ) -> None:
        """Add a battery steered at weight 6 and, unless told otherwise, its two charging limits.

        Args:
            manager: The manager to wire the battery to.
            outputs: The participants' outputs; the battery's are added to it.
            with_limits: Whether the battery's charging limits are wired.
        """
        battery_name = "Battery"
        outputs.battery = cp.ComponentOutput(
            battery_name,
            "AcBatteryPowerUsed",
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="the battery's realized power",
            component_id=ComponentID(name=battery_name),
        )
        manager.add_component_input_and_connect(
            source_object_name=battery_name,
            source_component_output="AcBatteryPowerUsed",
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
            source_weight=6,
        )
        manager.inputs[-1].source_output = outputs.battery
        manager.add_component_output(
            source_output_name="LoadingPowerInputForBattery_",
            source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_weight=6,
            output_description="Target electricity for the battery.",
        )

        if not with_limits:
            return
        for field_name, limit_tag in (
            ("MaximumChargingPowerInWatt", lt.InandOutputType.MAXIMUM_CHARGING_POWER),
            ("MinimumChargingPowerInWatt", lt.InandOutputType.MINIMUM_CHARGING_POWER),
        ):
            limit_output = cp.ComponentOutput(
                battery_name,
                field_name,
                lt.LoadTypes.ELECTRICITY,
                lt.Units.WATT,
                output_description="a charging limit of the battery",
                component_id=ComponentID(name=battery_name),
            )
            manager.add_component_input_and_connect(
                source_object_name=battery_name,
                source_component_output=field_name,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.BATTERY, limit_tag],
                source_weight=999,
            )
            manager.inputs[-1].source_output = limit_output
            if limit_tag == lt.InandOutputType.MAXIMUM_CHARGING_POWER:
                outputs.battery_maximum_charging_power = limit_output
            else:
                outputs.battery_minimum_charging_power = limit_output

    @staticmethod
    def indexed_step_values(
        manager: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
        outputs: "TestSurplusHysteresis.ParticipantOutputs",
    ) -> cp.SingleTimeStepValues:
        """Give every output its index, as the simulator would, and return step values with the heat pump at 1000 W.

        Returns:
            The step values.
        """
        all_outputs = [*outputs.all(), *manager.outputs]
        for index, output in enumerate(all_outputs):
            output.global_index = index
        stsv = cp.SingleTimeStepValues(len(all_outputs))
        stsv.set_output_value(outputs.heat_pump, 1000.0)
        return stsv

    @staticmethod
    def hot_water_raise_in_kelvin(
        manager: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
        pv_output: cp.ComponentOutput,
        stsv: cp.SingleTimeStepValues,
        surplus_in_watt: float,
    ) -> float:
        """Run one pass with the PV production that leaves this surplus beside the heat pump's 1000 W.

        Returns:
            The hot-water set-temperature raise the manager publishes, in K.
        """
        stsv.set_output_value(pv_output, 1000.0 + surplus_in_watt)
        manager.i_restore_state()
        manager.i_simulate(0, stsv, False)
        return stsv.values[manager.domestic_hot_water_storage_temperature_modifier.global_index]

    @pytest.mark.base
    def test_the_band_keeps_the_decision_of_the_previous_step_not_of_the_previous_pass(self) -> None:
        """Catches a decision that advances inside ``i_simulate``, which would make a pass depend on the pass before.

        A surplus inside the band keeps the decision the last step converged to, whatever an earlier pass of the
        same step decided; the converged decision becomes the next step's start only through ``i_save_state``.
        """
        manager, pv_output, stsv = self.manager_with_pv_and_hot_water_heat_pump()
        offset_in_kelvin = manager.domestic_hot_water_storage_temperature_offset_value
        manager.i_save_state()
        assert self.hot_water_raise_in_kelvin(manager, pv_output, stsv, 200.0) == offset_in_kelvin
        assert self.hot_water_raise_in_kelvin(manager, pv_output, stsv, 5e-12) == 0.0
        assert self.hot_water_raise_in_kelvin(manager, pv_output, stsv, 200.0) == offset_in_kelvin
        manager.i_save_state()
        assert self.hot_water_raise_in_kelvin(manager, pv_output, stsv, -5e-12) == offset_in_kelvin
        assert self.hot_water_raise_in_kelvin(manager, pv_output, stsv, -200.0) == 0.0

    def manager_with_battery(
        self, maximum_charging_power_in_watt: float, minimum_charging_power_in_watt: float
    ) -> Tuple[
        controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
        "TestSurplusHysteresis.ParticipantOutputs",
        cp.SingleTimeStepValues,
    ]:
        """Build a manager with PV, a hot-water heat pump drawing 1000 W and a battery with these charging limits.

        Returns:
            The manager, the participants' outputs and the step values.
        """
        manager, outputs = self.manager_with_pv_and_heat_pump_outputs()
        self.wire_battery(manager, outputs)
        stsv = self.indexed_step_values(manager, outputs)
        stsv.set_output_value(cast(cp.ComponentOutput, outputs.battery_maximum_charging_power), maximum_charging_power_in_watt)
        stsv.set_output_value(cast(cp.ComponentOutput, outputs.battery_minimum_charging_power), minimum_charging_power_in_watt)
        return manager, outputs, stsv

    @staticmethod
    def one_pass(
        manager: controller_l2_energy_management_system.L2GenericEnergyManagementSystem,
        outputs: "TestSurplusHysteresis.ParticipantOutputs",
        stsv: cp.SingleTimeStepValues,
        *,
        pv_production_in_watt: float,
        realized_battery_power_in_watt: float,
    ) -> Tuple[float, float]:
        """Run one pass from the saved state with this PV production and the battery's answer to an earlier offer.

        Returns:
            The hot-water set-temperature raise in K and the published grid balance in W.
        """
        stsv.set_output_value(outputs.pv, pv_production_in_watt)
        stsv.set_output_value(cast(cp.ComponentOutput, outputs.battery), realized_battery_power_in_watt)
        manager.i_restore_state()
        manager.i_simulate(0, stsv, False)
        return (
            stsv.values[manager.domestic_hot_water_storage_temperature_modifier.global_index],
            stsv.values[manager.total_electricity_to_or_from_grid.global_index],
        )

    @pytest.mark.base
    def test_the_battery_share_comes_from_its_limits_and_not_from_its_answer(self) -> None:
        """Catches a decision taken on the battery's answer to an earlier pass's offer, which makes the passes cycle.

        With 3000 W of PV beside 1000 W of heat pump, a battery that can take 1500 W leaves 500 W of surplus, and the
        set temperatures are raised whatever the battery answered before. The published grid balance still books the
        battery's realized power.
        """
        manager, outputs, stsv = self.manager_with_battery(1500.0, 200.0)
        offset_in_kelvin = manager.domestic_hot_water_storage_temperature_offset_value
        manager.i_save_state()
        for realized_battery_power_in_watt in (2000.0, 0.0, -3000.0, 1500.0):
            raise_in_kelvin, grid_balance_in_watt = self.one_pass(
                manager, outputs, stsv, pv_production_in_watt=3000.0, realized_battery_power_in_watt=realized_battery_power_in_watt
            )
            assert raise_in_kelvin == offset_in_kelvin
            assert grid_balance_in_watt == pytest.approx(2000.0 - realized_battery_power_in_watt)

    @pytest.mark.base
    def test_a_battery_that_takes_the_whole_surplus_keeps_the_previous_decision(self) -> None:
        """Catches a battery charging below its maximum read as a surplus or a deficit: what it takes leaves zero."""
        manager, outputs, stsv = self.manager_with_battery(5000.0, 200.0)
        manager.i_save_state()
        raise_in_kelvin, _ = self.one_pass(manager, outputs, stsv, pv_production_in_watt=3000.0, realized_battery_power_in_watt=0.0)
        assert raise_in_kelvin == 0.0

    @pytest.mark.base
    def test_a_surplus_below_the_batterys_minimum_raises(self) -> None:
        """Catches a surplus the battery does not take, because it lies below its minimum, kept from the raise."""
        manager, outputs, stsv = self.manager_with_battery(5000.0, 200.0)
        manager.i_save_state()
        raise_in_kelvin, _ = self.one_pass(manager, outputs, stsv, pv_production_in_watt=1059.0, realized_battery_power_in_watt=0.0)
        assert raise_in_kelvin == manager.domestic_hot_water_storage_temperature_offset_value

    @pytest.mark.base
    def test_a_raise_that_needs_the_battery_to_discharge_ends(self) -> None:
        """Catches a raise kept alive by the battery: only PV electricity keeps it.

        The raised heat pump draws 1000 W beside 800 W of PV, and the battery discharges the missing 200 W. The
        realized balance is zero, inside the band, which kept the raise before; counting the battery only as a sink
        leaves a deficit of 200 W, and the raise ends.
        """
        manager, outputs, stsv = self.manager_with_battery(5000.0, 200.0)
        manager.state = controller_l2_energy_management_system.EMSState(set_temperatures_are_raised=True)
        manager.i_save_state()
        raise_in_kelvin, grid_balance_in_watt = self.one_pass(
            manager, outputs, stsv, pv_production_in_watt=800.0, realized_battery_power_in_watt=-200.0
        )
        assert grid_balance_in_watt == pytest.approx(0.0)
        assert raise_in_kelvin == 0.0

    @pytest.mark.base
    def test_a_battery_without_its_limits_is_refused(self) -> None:
        """Catches a battery whose limits nobody wired, which would leave its share unknown and the decision wrong."""
        manager, outputs = self.manager_with_pv_and_heat_pump_outputs()
        self.wire_battery(manager, outputs, with_limits=False)
        stsv = self.indexed_step_values(manager, outputs)
        manager.i_save_state()
        with pytest.raises(ValueError, match="MaximumChargingPowerInWatt"):
            manager.i_simulate(0, stsv, False)

    @pytest.mark.base
    def test_the_manager_is_safe_to_iterate(self) -> None:
        """Catches state written in ``i_simulate`` or not restored: any passes before must not change the next one.

        The iteration-safety test: save, run passes with other inputs, restore and run with input X; the outputs must
        equal those of a fresh save followed by one pass with X.
        """

        def outputs_after(earlier_passes: List[Tuple[float, float]]) -> List[float]:
            manager, outputs, stsv = self.manager_with_battery(1500.0, 200.0)
            manager.i_simulate(0, stsv, False)
            manager.i_save_state()
            for pv_production_in_watt, realized_battery_power_in_watt in earlier_passes:
                self.one_pass(
                    manager,
                    outputs,
                    stsv,
                    pv_production_in_watt=pv_production_in_watt,
                    realized_battery_power_in_watt=realized_battery_power_in_watt,
                )
            self.one_pass(manager, outputs, stsv, pv_production_in_watt=1100.0, realized_battery_power_in_watt=50.0)
            return list(stsv.values)

        assert outputs_after([(9000.0, 1500.0), (0.0, -800.0), (2600.0, 0.0)]) == outputs_after([])


@pytest.mark.base
def test_the_car_is_offered_the_surplus_before_its_own_charging() -> None:
    """Catches the manager sending the car the surplus left after the car's own charging.

    The car's charge controller charges with the signal it gets. Sent the remainder after its own charging, it
    answered every pass with that remainder and the next pass sent it the surplus minus it, so its charging
    alternated between the passes of a step. The car is offered what is left before it, as the house battery is,
    and what it takes is subtracted for the participants after it. A heat pump keeps the booking signal: what is
    left after its own draw.
    """
    manager = _energy_manager()
    car_draw = cp.ComponentOutput(
        "Car", "Draw", lt.LoadTypes.ELECTRICITY, lt.Units.WATT, component_id=ComponentID(name="Car")
    )
    car_signal = cp.ComponentOutput(
        "Manager", "CarSignal", lt.LoadTypes.ELECTRICITY, lt.Units.WATT, component_id=ComponentID(name="Manager")
    )
    heat_pump_signal = cp.ComponentOutput(
        "Manager", "HeatPumpSignal", lt.LoadTypes.ELECTRICITY, lt.Units.WATT, component_id=ComponentID(name="Manager")
    )
    car_input = cp.ComponentInput("Manager", "CarDraw", lt.LoadTypes.ELECTRICITY, lt.Units.WATT, True)
    car_input.source_output = car_draw
    for position, output in enumerate([car_draw, car_signal, heat_pump_signal]):
        output.global_index = position
    stsv = cp.SingleTimeStepValues(3)
    stsv.values[car_draw.global_index] = 2000.0

    left_after_car_in_watt = manager.control_electricity_component_iterative(
        available_surplus_electricity_in_watt=3000.0,
        stsv=stsv,
        current_component_type=lt.ComponentType.CAR_BATTERY,
        current_input=car_input,
        current_output=car_signal,
    )
    left_after_heat_pump_in_watt = manager.control_electricity_component_iterative(
        available_surplus_electricity_in_watt=3000.0,
        stsv=stsv,
        current_component_type=lt.ComponentType.HEAT_PUMP_BUILDING,
        current_input=car_input,
        current_output=heat_pump_signal,
    )

    assert stsv.values[car_signal.global_index] == 3000.0
    assert left_after_car_in_watt == 1000.0
    assert stsv.values[heat_pump_signal.global_index] == 1000.0
    assert left_after_heat_pump_in_watt == 1000.0


@pytest.mark.base
def test_the_car_takes_from_the_grid_what_its_draw_exceeds_the_offer_by() -> None:
    """Catches the car's grid KPI booked from the offer's sign, as for the consumers that get a remainder.

    Offered 3,000 W and drawing 11,000 W the car takes 8,000 W from the grid; offered 5,000 W and drawing 4,000 W
    it takes nothing; offered a deficit of -2,000 W and drawing 11,000 W it takes all 11,000 W.
    """
    grid_draw_in_watt = (
        controller_l2_energy_management_system.L2GenericEnergyManagementSystem.offered_participant_grid_draw_in_watt(
            surplus_offered_in_watt=pd.Series([3000.0, 5000.0, -2000.0, 0.0]),
            realized_draw_in_watt=pd.Series([11000.0, 4000.0, 11000.0, 0.0]),
        )
    )

    assert grid_draw_in_watt.tolist() == [8000.0, 0.0, 11000.0, 0.0]
    with pytest.raises(ValueError, match="same steps"):
        controller_l2_energy_management_system.L2GenericEnergyManagementSystem.offered_participant_grid_draw_in_watt(
            surplus_offered_in_watt=pd.Series([1.0]), realized_draw_in_watt=pd.Series([1.0, 2.0])
        )


@pytest.mark.base
def test_the_car_grid_kpi_reads_the_draw_beside_the_offer() -> None:
    """Catches the car's grid KPI computed from the offer alone.

    Over two 15-minute steps the car is offered 3,000 W and -1,000 W and draws 11,000 W in both: 8,000 W and
    11,000 W from the grid, 4.75 kWh. The negative part of the offer alone would book 0.25 kWh.
    """
    manager = _energy_manager()
    dispatch_output = manager.add_component_output(
        source_output_name="DispatchToL1EVChargeControl_1_",
        source_tags=[lt.ComponentType.CAR_BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=5,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Surplus offered to the car. ",
    )
    car_output = _wire_measured_participant(manager, "L1EVChargeControl_1", lt.ComponentType.CAR_BATTERY, 5)
    results = pd.DataFrame({dispatch_output.field_name: [3000.0, -1000.0], car_output.field_name: [11000.0, 11000.0]})

    kpi_entries = manager.component_kpi_entries(
        all_outputs=[dispatch_output, car_output], postprocessing_results=results
    )

    car_entries = [entry for entry in kpi_entries if entry.name == "Electric car electricity consumption from grid"]
    assert len(car_entries) == 1
    assert car_entries[0].value == pytest.approx(4.75)
