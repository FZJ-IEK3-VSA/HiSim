"""  Basic household new example. """

# clean

from typing import Optional, Any
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import advanced_heat_pump_hplib
from hisim.components import sumbuilder
from hisim.components import simple_hot_water_storage
from hisim.components import heat_distribution_system
from hisim.postprocessingoptions import PostProcessingOptions

__authors__ = "Katharina Rieck"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


def household_with_hds_and_advanced_hp(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Basic household example.

    This setup function emulates an household including the basic components. Here the residents have their
    electricity and heating needs covered by the photovoltaic system and the heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump
        - Heat Pump Controller
        - Heat Distribution System
        - Heat Distribution Controller
        - Heat Water Storage
    """

    # =================================================================================================================================
    # Set System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # Set Weather
    location = "Aachen"

    # Set Photovoltaic System
    time = 2019
    power = 10e3
    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrate_inverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    name = "PVSystem"
    azimuth = 180
    tilt = 30
    source_weight = -1

    # Set Heat Pump Controller
    hp_controller_mode = (
        1 # mode 1 for on/off and mode 2 for heating/cooling/off (regulated)
    )
    set_heating_threshold_temperature_for_heat_pump_in_celsius = 14.0

    # Set Heat Pump
    model: str = "Generic"
    group_id: int = 4  # outdoor/air heat pump (choose 1 for regulated or 4 for on/off)
    heating_reference_temperature_in_celsius: float = -7  # t_in
    set_thermal_output_power_in_watt: float = 8000
    flow_temperature_in_celsius = 21  # t_out_val
    cycling_mode = False

    # Set Simple Heat Water Storage
    hws_name = "SimpleHeatWaterStorage"
    volume_heating_water_storage_in_liter = 500
    mean_water_temperature_in_storage_in_celsius = 21
    cool_water_temperature_in_storage_in_celsius = 21
    hot_water_temperature_in_storage_in_celsius = 21

    # Set Heat Distribution System
    hds_name = "HeatDistributionSystem"
    water_temperature_in_distribution_system_in_celsius = 21
    heating_system = heat_distribution_system.HeatingSystemType.FLOORHEATING

    # Set Heat Distribution Controller
    hds_controller_name = "HeatDistributionSystemController"
    set_heating_threshold_temperature_for_heat_distribution_system_in_celsius = set_heating_threshold_temperature_for_heat_pump_in_celsius
    set_heating_temperature_for_building_in_celsius = 21
    set_cooling_temperature_for_building_in_celsius = 22

    # =================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_with_only_plots(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.EXPORT_TO_CSV)
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Heat Distribution Controller
    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters,
        config=heat_distribution_system.HeatDistributionControllerConfig(
            name=hds_controller_name,
            set_heating_threshold_outside_temperature_in_celsius=set_heating_threshold_temperature_for_heat_distribution_system_in_celsius,
            set_heating_temperature_for_building_in_celsius=set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=set_cooling_temperature_for_building_in_celsius,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            heating_system=heating_system
        ),
    )
    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home()
    my_building_config.heating_reference_temperature_in_celsius = heating_reference_temperature_in_celsius
    my_building_config.building_code="DE.N.SFH.05.Gen.ReEx.001.003"

    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    # Build Occupancy
    my_occupancy_config = (
        loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01()
    )

    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.Aachen
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig(
        time=time,
        location=location,
        power=power,
        load_module_data=load_module_data,
        module_name=module_name,
        integrate_inverter=integrate_inverter,
        tilt=tilt,
        azimuth=azimuth,
        inverter_name=inverter_name,
        source_weight=source_weight,
        name=name,
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Base Electricity Load Profile
    my_base_electricity_load_profile = sumbuilder.ElectricityGrid(
        config=sumbuilder.ElectricityGridConfig(
            name="ElectrcityGrid_BaseLoad",
            grid=[my_occupancy, "Subtract", my_photovoltaic_system],
            signal=None,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump Controller
    my_heat_pump_controller = advanced_heat_pump_hplib.HeatPumpHplibControllerL1(
        config=advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config(
            name="HeatPumpHplibController",
            mode=hp_controller_mode,
            heating_threshold_temperature_in_celsius=set_heating_threshold_temperature_for_heat_pump_in_celsius),
            my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump = advanced_heat_pump_hplib.HeatPumpHplib(
        config=advanced_heat_pump_hplib.HeatPumpHplibConfig(
            model=model,
            group_id=group_id,
            t_in=heating_reference_temperature_in_celsius,
            t_out_val=flow_temperature_in_celsius,
            p_th_set=set_thermal_output_power_in_watt,
            cycling_mode=cycling_mode,
        ),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Distribution System
    my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig(
        name=hds_name,
        water_temperature_in_distribution_system_in_celsius=water_temperature_in_distribution_system_in_celsius,
        heating_system=heating_system,
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_hot_water_storage.SimpleHotWaterStorageConfig(
        name=hws_name,
        volume_heating_water_storage_in_liter=volume_heating_water_storage_in_liter,
        mean_water_temperature_in_storage_in_celsius=mean_water_temperature_in_storage_in_celsius,
        cool_water_temperature_in_storage_in_celsius=cool_water_temperature_in_storage_in_celsius,
        hot_water_temperature_in_storage_in_celsius=hot_water_temperature_in_storage_in_celsius,
    )
    my_simple_hot_water_storage = simple_hot_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    
    my_simple_hot_water_storage_controller = simple_hot_water_storage.SimpleHotWaterStorageController(my_simulation_parameters=my_simulation_parameters)

    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    my_photovoltaic_system.connect_only_predefined_connections(my_weather)
    # -----------------------------------------------------------------------------------------------------------------
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_distribution_system.component_name,
        my_heat_distribution_system.ThermalPowerDelivered,
    )

    # -----------------------------------------------------------------------------------------------------------------
    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.WaterTemperatureInputFromHeatWaterStorage,
        my_simple_hot_water_storage.component_name,
        my_simple_hot_water_storage.WaterTemperatureToHeatGenerator,
    )
    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.HeatingFlowTemperatureFromHeatDistributionSystem,
        my_heat_distribution_controller.component_name,
        my_heat_distribution_controller.HeatingFlowTemperature,
    )

    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.DailyAverageOutsideTemperature,
        my_weather.component_name,
        my_weather.DailyAverageOutsideTemperatures,
    )

    # -----------------------------------------------------------------------------------------------------------------
    my_heat_pump.connect_input(
        my_heat_pump.OnOffSwitch,
        my_heat_pump_controller.component_name,
        my_heat_pump_controller.State,
    )
    my_heat_pump.connect_input(
        my_heat_pump.TemperatureAmbient,
        my_weather.component_name,
        my_weather.DailyAverageOutsideTemperatures,
    )
    my_heat_pump.connect_input(
        my_heat_pump.TemperatureInputPrimary,
        my_weather.component_name,
        my_weather.DailyAverageOutsideTemperatures,
    )
    my_heat_pump.connect_input(
        my_heat_pump.TemperatureInputSecondary,
        my_simple_hot_water_storage.component_name,
        my_simple_hot_water_storage.WaterTemperatureToHeatGenerator,
    )
    # -----------------------------------------------------------------------------------------------------------------
    my_simple_hot_water_storage.connect_input(
        my_simple_hot_water_storage.WaterTemperatureFromHeatDistributionSystem,
        my_heat_distribution_system.component_name,
        my_heat_distribution_system.WaterTemperatureOutput,
    )
    my_simple_hot_water_storage.connect_input(
        my_simple_hot_water_storage.WaterTemperatureFromHeatGenerator,
        my_heat_pump.component_name,
        my_heat_pump.TemperatureOutput,
    )
    my_simple_hot_water_storage.connect_input(
        my_simple_hot_water_storage.WaterMassFlowRateFromHeatGenerator,
        my_heat_pump.component_name,
        my_heat_pump.MassFlowOutput,
    )
    my_simple_hot_water_storage.connect_input(
        my_simple_hot_water_storage.State,
        my_simple_hot_water_storage_controller.component_name,
        my_simple_hot_water_storage_controller.State,
    )
    my_simple_hot_water_storage_controller.connect_input(
        my_simple_hot_water_storage_controller.WaterMassFlowRateFromHeatGenerator,
        my_heat_pump.component_name,
        my_heat_pump.MassFlowOutput
    )

    # -----------------------------------------------------------------------------------------------------------------
    my_heat_distribution_controller.connect_only_predefined_connections(
        my_weather, my_building, my_simple_hot_water_storage
    )
    # -----------------------------------------------------------------------------------------------------------------
    my_heat_distribution_system.connect_only_predefined_connections(
        my_building, my_heat_distribution_controller, my_simple_hot_water_storage
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters

    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_base_electricity_load_profile)
    my_sim.add_component(my_building)
    my_sim.add_component(my_heat_distribution_controller)
    my_sim.add_component(my_heat_distribution_system)
    my_sim.add_component(my_simple_hot_water_storage_controller)
    my_sim.add_component(my_simple_hot_water_storage)
    my_sim.add_component(my_heat_pump_controller)
    my_sim.add_component(my_heat_pump)
