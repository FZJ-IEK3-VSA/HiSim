"""Dynamic Components Module."""

from typing import Any, Optional

from hisim import loadtypes as lt
from hisim.components import controller_l2_energy_management_system as cl2
from hisim.components import generic_pv_system, loadprofilegenerator_connector, weather
from hisim.components.building import Building, BuildingConfig
from hisim.components.controller_l1_electrolyzer_h2 import (
    ElectrolyzerController,
    ElectrolyzerControllerConfig,
)
from hisim.components.controller_l1_fuel_cell import (
    FuelCellController,
    FuelCellControllerConfig,
)
from hisim.components.controller_l2_ptx_energy_management_system import (
    PTXController,
    PTXControllerConfig,
)
from hisim.components.controller_l2_xtp_fuel_cell_ems import (
    XTPController,
    XTPControllerConfig,
)
from hisim.components.generic_electrolyzer_h2 import Electrolyzer, ElectrolyzerConfig
from hisim.components.generic_fuel_cell import FuelCell, FuelCellConfig
from hisim.components.generic_heat_pump import (
    GenericHeatPump,
    GenericHeatPumpConfig,
    GenericHeatPumpController,
    GenericHeatPumpControllerConfig,
)
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum
from hisim.sim_repository_singleton import SingletonDictKeyEnum, SingletonSimRepository
from hisim.simulator import SimulationParameters


def decentralized_energy_netw_pv_h2sys_hp(my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None) -> None:  # first with bat
    """Dynamic Components Demonstration.

    In this example a generic controller is added. The generic controller
    makes it possible to add component generically.
    Here two fuel_cell/chp_systems and two batteries
    are added.
    """
    year = 2021
    seconds_per_timestep = 60
    # Set systems
    fuel_cell_name = "NedstackFCS10XXL"
    electrolyzer_name = "KYROS50"
    operation_mode = "StandbyandOffLoad"
    """
    - "StandbyLoad"
    - "StandbyandOffLoad"
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

    """
    my_advanced_battery_config_1 = advanced_battery_bslib.BatteryConfig.get_default_config()
    my_advanced_battery_config_1.system_id = "SG1"
    my_advanced_battery_config_1.custom_battery_capacity_generic_in_kilowatt_hour = 200.0
    my_advanced_battery_config_1.custom_pv_inverter_power_generic_in_watt = 110000.0
    my_advanced_battery_config_1.source_weight = 1

    my_advanced_battery_1 = advanced_battery_bslib.Battery(
        my_simulation_parameters=my_simulation_parameters,
        config=my_advanced_battery_config_1,
    )
    """

    my_fuel_cell_controller_l2 = XTPController(
        my_simulation_parameters=my_simulation_parameters,
        config=XTPControllerConfig.control_fuel_cell(fuel_cell_name=fuel_cell_name, operation_mode=operation_mode),
    )
    my_fuel_cell_controller = FuelCellController(
        my_simulation_parameters=my_simulation_parameters,
        config=FuelCellControllerConfig.control_fuel_cell(fuel_cell_name=fuel_cell_name),
    )
    my_fuel_cell = FuelCell(
        my_simulation_parameters=my_simulation_parameters,
        config=FuelCellConfig.config_fuel_cell(fuel_cell_name=fuel_cell_name),
    )
    # buffer bat test start
    my_electrolyzer_controller_l2 = PTXController(
        my_simulation_parameters=my_simulation_parameters,
        config=PTXControllerConfig.control_electrolyzer(
            electrolyzer_name=electrolyzer_name,
            operation_mode=operation_mode,
        ),
    )
    # buffer bat test end

    my_electrolyzer_controller = ElectrolyzerController(
        my_simulation_parameters=my_simulation_parameters,
        config=ElectrolyzerControllerConfig.control_electrolyzer(electrolyzer_name=electrolyzer_name),
    )
    my_electrolyzer = Electrolyzer(
        my_simulation_parameters=my_simulation_parameters,
        config=ElectrolyzerConfig.config_electrolyzer(electrolyzer_name=electrolyzer_name),
    )

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
    # hp test start

    # Build Building
    # my_building = Building(
    #    config=BuildingConfig.get_default_german_single_family_home(),
    #    my_simulation_parameters=my_simulation_parameters,
    # )

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
    my_heat_pump_controller_config = GenericHeatPumpControllerConfig.get_default_generic_heat_pump_controller_config()
    my_heat_pump_controller_config.mode = hp_mode  # hp_mode
    # Build Heat Pump Controller
    my_heat_pump_controller = GenericHeatPumpController(
        config=GenericHeatPumpControllerConfig(
            name="GenericHeatPumpController",
            temperature_air_heating_in_celsius=19.0,
            temperature_air_cooling_in_celsius=24.0,
            offset=0.5,
            mode=hp_mode,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump = GenericHeatPump(
        config=GenericHeatPumpConfig.get_default_generic_heat_pump_config(),
        my_simulation_parameters=my_simulation_parameters,
    )
    # hp test end
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
    # hp test end

    my_cl2.add_component_input_and_connect(
        source_component_class=my_fuel_cell,
        source_component_output=my_fuel_cell.PowerOutput,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_REAL],
        source_weight=2,
    )

    my_cl2.add_component_input_and_connect(
        source_component_class=my_electrolyzer,
        source_component_output=my_electrolyzer.CurrentLoad,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[
            lt.ComponentType.ELECTROLYZER,
            lt.InandOutputType.ELECTRICITY_REAL,
        ],
        source_weight=1,
    )

    electricity_from_fuel_cell_target_1 = my_cl2.add_component_output(
        source_output_name=lt.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=2,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for fuel cell (I). ",
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
        output_description="Target electricity to electrolyzer. ",
    )
    my_fuel_cell_controller_l2.connect_dynamic_input(
        input_fieldname=my_fuel_cell_controller_l2.DemandLoad,
        src_object=electricity_from_fuel_cell_target_1,
    )
    my_fuel_cell_controller.connect_input(
        input_fieldname=my_fuel_cell_controller.DemandProfile,
        src_object_name=my_fuel_cell_controller_l2.component_name,
        src_field_name=my_fuel_cell_controller_l2.DemandToSystem,
    )
    # buffer bat test start
    my_electrolyzer_controller_l2.connect_dynamic_input(
        input_fieldname=my_electrolyzer_controller_l2.RESLoad,
        src_object=electricity_to_electrolyzer_target,
    )
    my_electrolyzer_controller.connect_input(
        input_fieldname=my_electrolyzer_controller.ProvidedLoad,
        src_object_name=my_electrolyzer_controller_l2.component_name,
        src_field_name=my_electrolyzer_controller_l2.PowerToSystem,
    )

    # buffer bat test end

    my_fuel_cell.connect_input(
        my_fuel_cell.DemandProfile,
        my_fuel_cell_controller.component_name,
        my_fuel_cell_controller.PowerTarger,
    )
    my_fuel_cell.connect_input(
        my_fuel_cell.ControlSignal,
        my_fuel_cell_controller.component_name,
        my_fuel_cell_controller.CurrentMode,
    )

    my_electrolyzer.connect_input(
        my_electrolyzer.LoadInput,
        my_electrolyzer_controller.component_name,
        my_electrolyzer_controller.DistributedLoad,
    )

    my_electrolyzer.connect_input(
        my_electrolyzer.InputState,
        my_electrolyzer_controller.component_name,
        my_electrolyzer_controller.CurrentMode,
    )
    """
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
        output_description="Target electricity for battery controller (I). ",
    )

    my_advanced_battery_1.connect_dynamic_input(
        input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput,
        src_object=electricity_to_or_from_battery_target_1,
    )
    """
    # =================================================================================================================================
    # Add Components to Simulation Parameters
    # my_sim.add_component(my_advanced_battery_1)
    my_sim.add_component(my_fuel_cell_controller_l2)
    my_sim.add_component(my_fuel_cell_controller)
    my_sim.add_component(my_fuel_cell)
    my_sim.add_component(my_electrolyzer_controller)
    my_sim.add_component(my_electrolyzer)
    my_sim.add_component(my_cl2)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_photovoltaic_system)
    # hp
    my_sim.add_component(my_building)
    my_sim.add_component(my_heat_pump_controller)
    my_sim.add_component(my_heat_pump)
    # buff bat controll
    my_sim.add_component(my_electrolyzer_controller_l2)

    # Set Results Path
    ResultPathProviderSingleton().set_important_result_path_information(
        module_directory=my_sim.module_directory,
        model_name=my_sim.setup_function,
        variant_name=f"{my_simulation_parameters.duration.days}d_{my_simulation_parameters.seconds_per_timestep}s_PEM_{operation_mode}",
        hash_number=None,
        sorting_option=SortingOptionEnum.MASS_SIMULATION_WITH_INDEX_ENUMERATION,
    )

    SingletonSimRepository().set_entry(
        key=SingletonDictKeyEnum.RESULT_SCENARIO_NAME,
        entry=f"{my_simulation_parameters.duration.days}d_{my_simulation_parameters.seconds_per_timestep}s_PEM",
    )


# python ../hisim/hisim_main.py decentralized_energy_netw_pv_bat_h2system_hp.py decentralized_energy_netw_pv_h2sys_hp
