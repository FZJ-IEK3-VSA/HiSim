"""Dynamic Components Module."""

# clean

from typing import Any, Optional

from hisim import loadtypes as lt
from hisim import log
from hisim.components import advanced_battery_bslib
from hisim.components import controller_l2_energy_management_system as cl2
from hisim.components import (
    generic_heat_pump,
    generic_pv_system,
    loadprofilegenerator_connector,
    weather,
)
from hisim.components.building import Building, BuildingConfig
from hisim.components.controller_l1_rsoc import RsocController, RsocControllerConfig
from hisim.components.controller_l2_rsoc_battery_system import (
    RsocBatteryController,
    RsocBatteryControllerConfig,
)
from hisim.components.generic_rsoc import Rsoc, RsocConfig
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum
from hisim.sim_repository_singleton import SingletonDictKeyEnum, SingletonSimRepository
from hisim.simulator import SimulationParameters


def decentralized_energy_netw_pv_h2sys_hp_bat(my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None) -> None:
    """Dynamic Components Demonstration.

    In this example a generic controller is added. The generic controller
    makes it possible to add component generically.
    Here two fuel_cell/chp_systems and two batteries
    are added.
    """
    log.information("Starting basic household_pv_rsoc_hp_grid example")

    year = 2021
    seconds_per_timestep = 60

    # Set rSOC and operation mode
    rsoc_name = "rSOC1040kW"
    operation_mode_rsoc = "StandbyLoad"
    """
    Operation modes:
    - "MinimumLoad"
    - "StandbyLoad"
    """
    number_of_apartments = 20
    # Set hp mode
    hp_mode = 1

    # Set photovoltaic system
    time = 2021
    power = 200000.0
    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrate_inverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    name = "PVSystem"
    azimuth = 180
    tilt = 30
    source_weight = 0
    pv_co2_footprint = power * 1e-3 * 130.7
    pv_cost = power * 1e-3 * 535.81
    pv_maintenance_cost_as_percentage_of_investment = 0.01
    pv_lifetime = 25

    # =================================================================================================================================
    # Build Components

    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)
    # my_simulation_parameters = SimulationParameters.january_only(year=year, seconds_per_timestep=seconds_per_timestep)
    # my_simulation_parameters.enable_all_options( )

    my_sim.set_simulation_parameters(my_simulation_parameters)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_LINE)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_CARPET)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_ALL_OUTPUTS_TO_REPORT)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.INCLUDE_CONFIGS_IN_PDF_REPORT)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.GENERATE_PDF_REPORT)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION_WITH_PYAM)

    my_advanced_battery_config_1 = advanced_battery_bslib.BatteryConfig.get_default_config()
    my_advanced_battery_config_1.system_id = "SG1"
    my_advanced_battery_config_1.custom_battery_capacity_generic_in_kilowatt_hour = 200.0
    my_advanced_battery_config_1.custom_pv_inverter_power_generic_in_watt = 110000.0
    my_advanced_battery_config_1.source_weight = 1

    my_advanced_battery_1 = advanced_battery_bslib.Battery(
        my_simulation_parameters=my_simulation_parameters,
        config=my_advanced_battery_config_1,
    )

    # buffer bat test start
    my_rsoc_controller_l2 = RsocBatteryController(
        my_simulation_parameters=my_simulation_parameters,
        config=RsocBatteryControllerConfig.confic_rsoc_name(
            rsoc_name=rsoc_name,
            operation_mode=operation_mode_rsoc,
        ),
    )
    my_rsoc_controller_l1 = RsocController(
        my_simulation_parameters=my_simulation_parameters,
        config=RsocControllerConfig.config_rsoc(
            rsoc_name=rsoc_name,
        ),
    )
    my_rsoc = Rsoc(
        my_simulation_parameters=my_simulation_parameters,
        config=RsocConfig.config_rsoc(
            rsoc_name=rsoc_name,
        ),
    )
    # buffer bat test end

    my_cl2_config = cl2.EMSConfig.get_default_config_ems()
    my_cl2 = cl2.L2GenericEnergyManagementSystem(my_simulation_parameters=my_simulation_parameters, config=my_cl2_config)

    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01()
    # choose 1 to be the default for the number of apartments
    SingletonSimRepository().set_entry(key=SingletonDictKeyEnum.NUMBEROFAPARTMENTS, entry=number_of_apartments)
    my_occupancy = loadprofilegenerator_connector.Occupancy(config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters)

    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)

    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig(
        time=time,
        location="Aachen",
        power=power,
        load_module_data=load_module_data,
        module_name=module_name,
        integrate_inverter=integrate_inverter,
        tilt=tilt,
        azimuth=azimuth,
        inverter_name=inverter_name,
        source_weight=source_weight,
        name=name,
        co2_footprint=pv_co2_footprint,
        cost=pv_cost,
        maintenance_cost_as_percentage_of_investment=pv_maintenance_cost_as_percentage_of_investment,
        lifetime=pv_lifetime,
        predictive=False,
        predictive_control=False,
        prediction_horizon=None,
    )

    my_photovoltaic_system = generic_pv_system.PVSystem(
        my_simulation_parameters=my_simulation_parameters,
        config=my_photovoltaic_system_config,
    )
    my_photovoltaic_system.connect_only_predefined_connections(my_weather)

    my_building = Building(
        config=BuildingConfig(
            name="Building_1",
            building_code="DE.N.SFH.05.Gen.ReEx.001.002",
            building_heat_capacity_class="medium",
            initial_internal_temperature_in_celsius=23,
            heating_reference_temperature_in_celsius=-14,
            absolute_conditioned_floor_area_in_m2=121.2,
            total_base_area_in_m2=None,
            number_of_apartments=number_of_apartments,
            predictive=False,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )
    # Build Heat Pump Controller Config
    my_heat_pump_controller_config = generic_heat_pump.GenericHeatPumpControllerConfig.get_default_generic_heat_pump_controller_config()
    my_heat_pump_controller_config.mode = hp_mode  # hp_mode
    # Build Heat Pump Controller

    my_heat_pump_controller = generic_heat_pump.GenericHeatPumpController(
        config=generic_heat_pump.GenericHeatPumpControllerConfig(
            name="GenericHeatPumpController",
            temperature_air_heating_in_celsius=19.0,
            temperature_air_cooling_in_celsius=24.0,
            offset=0.5,
            mode=hp_mode,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )
    # Build Heat Pump
    my_heat_pump = generic_heat_pump.GenericHeatPump(
        config=generic_heat_pump.GenericHeatPumpConfig.get_default_generic_heat_pump_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # =================================================================================================================================
    # Connect Component Inputs with Outputs
    my_cl2.add_component_inputs_and_connect(
        source_component_classes=[my_occupancy],
        outputstring="ElectricityOutput",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )
    my_cl2.add_component_inputs_and_connect(
        source_component_classes=[my_photovoltaic_system],
        outputstring="ElectricityOutput",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=999,
    )

    # hp test start
    my_cl2.add_component_input_and_connect(
        source_component_class=my_heat_pump,
        source_component_output=my_heat_pump.ElectricityOutput,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[
            lt.ComponentType.HEAT_PUMP,
            lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
        ],
        source_weight=999,
    )
    my_building.connect_only_predefined_connections(my_weather)
    my_building.connect_only_predefined_connections(my_occupancy)

    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_pump.component_name,
        my_heat_pump.ThermalPowerDelivered,
    )

    my_heat_pump_controller.connect_only_predefined_connections(my_building)

    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.ElectricityInput,
        my_cl2.component_name,
        my_cl2.ElectricityToOrFromGrid,
    )
    my_heat_pump.connect_only_predefined_connections(my_weather, my_heat_pump_controller)
    my_heat_pump.get_default_connections_heatpump_controller()

    my_cl2.add_component_input_and_connect(
        source_component_class=my_rsoc,
        source_component_output=my_rsoc.SOFCCurrentOutput,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[
            lt.ComponentType.FUEL_CELL,
            lt.InandOutputType.ELECTRICITY_PRODUCTION,
        ],
        source_weight=2,
    )

    my_cl2.add_component_input_and_connect(
        source_component_class=my_rsoc,
        source_component_output=my_rsoc.SOECCurrentLoad,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[
            lt.ComponentType.ELECTROLYZER,
            lt.InandOutputType.ELECTRICITY_REAL,
        ],
        source_weight=1,  # maybe change the weigth
    )

    electricity_from_rsofc_target_1 = my_cl2.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=2,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for rSOFC. ",
    )
    electricity_to_electrolyzer_target = my_cl2.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[
            lt.ComponentType.ELECTROLYZER,
            lt.InandOutputType.ELECTRICITY_TARGET,
        ],
        source_weight=1,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity to rSOEC. ",
    )

    my_rsoc_controller_l2.connect_dynamic_input(
        input_fieldname=my_rsoc_controller_l2.Demand,
        src_object=electricity_from_rsofc_target_1,
    )
    my_rsoc_controller_l2.connect_dynamic_input(
        input_fieldname=my_rsoc_controller_l2.RESLoad,
        src_object=electricity_to_electrolyzer_target,
    )

    my_rsoc_controller_l1.connect_input(
        input_fieldname=my_rsoc_controller_l1.ProvidedPower,
        src_object_name=my_rsoc_controller_l2.component_name,
        src_field_name=my_rsoc_controller_l2.PowerToSystem,
    )

    # buffer bat test end
    my_rsoc.connect_input(
        my_rsoc.PowerInput,
        my_rsoc_controller_l1.component_name,
        my_rsoc_controller_l1.PowerVsDemand,
    )
    my_rsoc.connect_input(
        my_rsoc.RSOCInputState,
        my_rsoc_controller_l1.component_name,
        my_rsoc_controller_l1.StateToRSOC,
    )

    my_cl2.add_component_input_and_connect(
        source_component_class=my_advanced_battery_1,
        source_component_output=my_advanced_battery_1.AcBatteryPower,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_REAL],
        source_weight=1,
    )

    electricity_to_or_from_battery_target_1 = my_cl2.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=my_advanced_battery_1.source_weight,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for battery controller. ",
    )

    my_advanced_battery_1.connect_dynamic_input(
        input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput,
        src_object=electricity_to_or_from_battery_target_1,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters
    my_sim.add_component(my_advanced_battery_1)
    my_sim.add_component(my_rsoc_controller_l1)
    my_sim.add_component(my_rsoc)
    my_sim.add_component(my_cl2)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_photovoltaic_system)
    # hp
    my_sim.add_component(my_building)
    my_sim.add_component(my_heat_pump_controller)
    my_sim.add_component(my_heat_pump)
    # buff bat controll
    my_sim.add_component(my_rsoc_controller_l2)

    # Set Results Path
    ResultPathProviderSingleton().set_important_result_path_information(
        module_directory=my_sim.module_directory,
        model_name=my_sim.setup_function,
        variant_name=f"{my_simulation_parameters.duration.days}d_{my_simulation_parameters.seconds_per_timestep}s_rSOC_{operation_mode_rsoc}",
        hash_number=None,
        sorting_option=SortingOptionEnum.MASS_SIMULATION_WITH_INDEX_ENUMERATION,
    )

    SingletonSimRepository().set_entry(
        key=SingletonDictKeyEnum.RESULT_SCENARIO_NAME,
        entry=f"{my_simulation_parameters.duration.days}d_{my_simulation_parameters.seconds_per_timestep}s_rSOC",
    )


# python ../hisim/hisim_main.py decentralized_energy_netw_pv_bat_h2system_hp.py decentralized_energy_netw_pv_h2sys_hp
