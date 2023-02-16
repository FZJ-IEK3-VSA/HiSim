"""  Basic household new example. """

# clean

from typing import Optional, Any
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import generic_heat_pump
from hisim.components import sumbuilder
from hisim.components import simple_heat_water_storage
from hisim.components import heat_distribution_system

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


def basic_household_new(
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

    # Set Occupancy
    occupancy_profile = "CH01"

    # Set Building
    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"
    building_heat_capacity_class = "medium"
    initial_temperature_in_celsius = 23
    heating_reference_temperature = -14
    absolute_conditioned_floor_area_in_m2 = 121.2
    total_base_area_in_m2 = None

    # Set Heat Pump Controller
    # set_residence_temperature_heating_in_celsius = 19.0
    # set_residence_temperature_cooling_in_celsius = 24.0
    set_water_storage_temperature_for_heating_in_celsius = 50
    set_water_storage_temperature_for_cooling_in_celsius = 70
    offset = 0.5
    hp_mode = 1

    # Set Heat Pump
    hp_manufacturer = "Viessmann Werke GmbH & Co KG"
    hp_name = "Vitocal 300-A AWO-AC 301.B07"
    hp_min_operation_time = 60
    hp_min_idle_time = 15

    # Set Simple Heat Water Storage
    hws_name = "SimpleHeatWaterStorage"
    volume_heating_water_storage_in_liter = 100
    mean_water_temperature_in_storage_in_celsius = 50
    cool_water_temperature_in_storage_in_celsius = 40
    hot_water_temperature_in_storage_in_celsius = 60

    # Set Heat Distribution System
    hds_name = "HeatDistributionSystem"
    water_temperature_in_distribution_system_in_celsius = 60

    # Set Heat Distribution Controller
    min_heating_temperature_building_in_celsius = 20
    set_heating_threshold_temperature = 16.0
    mode = 1

    # =================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.one_day_only_with_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig(
        profile_name=occupancy_profile, name="Occupancy"
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

    # Build Building
    my_building_config = building.BuildingConfig(
        building_code=building_code,
        building_heat_capacity_class=building_heat_capacity_class,
        initial_internal_temperature_in_celsius=initial_temperature_in_celsius,
        heating_reference_temperature_in_celsius=heating_reference_temperature,
        absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area_in_m2,
        total_base_area_in_m2=total_base_area_in_m2,
        name="Building1",
    )

    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    # Build Building Controller
    my_building_controller_config = building.BuildingControllerConfig(
        minimal_building_temperature_in_celsius=20,
        stop_heating_building_temperature_in_celsius=26
    )

    my_building_controller = building.BuildingController(
        config=my_building_controller_config, my_simulation_parameters=my_simulation_parameters
    )
    # Build Base Electricity Load Profile
    my_base_electricity_load_profile = sumbuilder.ElectricityGrid(
        name="BaseLoad",
        grid=[my_occupancy, "Subtract", my_photovoltaic_system],
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump Controller
    my_heat_pump_controller = generic_heat_pump.HeatPumpController(
        set_water_storage_temperature_for_heating_in_celsius=set_water_storage_temperature_for_heating_in_celsius,
        set_water_storage_temperature_for_cooling_in_celsius=set_water_storage_temperature_for_cooling_in_celsius,
        offset=offset,
        mode=hp_mode,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump
    my_heat_pump = generic_heat_pump.GenericHeatPump(
        manufacturer=hp_manufacturer,
        name=hp_name,
        min_operation_time=hp_min_operation_time,
        min_idle_time=hp_min_idle_time,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_heat_water_storage.HeatingWaterStorageConfig(
        name=hws_name,
        volume_heating_water_storage_in_liter=volume_heating_water_storage_in_liter,
        mean_water_temperature_in_storage_in_celsius=mean_water_temperature_in_storage_in_celsius,
        cool_water_temperature_in_storage_in_celsius=cool_water_temperature_in_storage_in_celsius,
        hot_water_temperature_in_storage_in_celsius=hot_water_temperature_in_storage_in_celsius
    )
    my_simple_heat_water_storage = simple_heat_water_storage.HeatingWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Distribution System

    my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig(
        name=hds_name,
        water_temperature_in_distribution_system_in_celsius=water_temperature_in_distribution_system_in_celsius
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Distribution Controller
    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters,
        min_heating_temperature_building_in_celsius=min_heating_temperature_building_in_celsius,
        set_heating_threshold_temperature_in_celsius=set_heating_threshold_temperature,
        mode=mode,
    )
    # =================================================================================================================================
    # Connect Component Inputs with Outputs

    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.TemperatureOutside,
        my_weather.component_name,
        my_weather.TemperatureOutside,
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.DirectNormalIrradiance,
        my_weather.component_name,
        my_weather.DirectNormalIrradiance,
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.DirectNormalIrradianceExtra,
        my_weather.component_name,
        my_weather.DirectNormalIrradianceExtra,
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.DiffuseHorizontalIrradiance,
        my_weather.component_name,
        my_weather.DiffuseHorizontalIrradiance,
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.GlobalHorizontalIrradiance,
        my_weather.component_name,
        my_weather.GlobalHorizontalIrradiance,
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.Azimuth, my_weather.component_name, my_weather.Azimuth
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.ApparentZenith,
        my_weather.component_name,
        my_weather.ApparentZenith,
    )
    my_photovoltaic_system.connect_input(
        my_photovoltaic_system.WindSpeed,
        my_weather.component_name,
        my_weather.WindSpeed,
    )

    my_building.connect_input(
        my_building.Altitude, my_weather.component_name, my_weather.Altitude
    )
    my_building.connect_input(
        my_building.Azimuth, my_weather.component_name, my_weather.Azimuth
    )
    my_building.connect_input(
        my_building.DirectNormalIrradiance,
        my_weather.component_name,
        my_weather.DirectNormalIrradiance,
    )
    my_building.connect_input(
        my_building.DiffuseHorizontalIrradiance,
        my_weather.component_name,
        my_weather.DiffuseHorizontalIrradiance,
    )
    my_building.connect_input(
        my_building.GlobalHorizontalIrradiance,
        my_weather.component_name,
        my_weather.GlobalHorizontalIrradiance,
    )
    my_building.connect_input(
        my_building.DirectNormalIrradianceExtra,
        my_weather.component_name,
        my_weather.DirectNormalIrradianceExtra,
    )
    my_building.connect_input(
        my_building.ApparentZenith, my_weather.component_name, my_weather.ApparentZenith
    )
    my_building.connect_input(
        my_building.TemperatureOutside,
        my_weather.component_name,
        my_weather.TemperatureOutside,
    )
    my_building.connect_input(
        my_building.HeatingByResidents,
        my_occupancy.component_name,
        my_occupancy.HeatingByResidents,
    )
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_distribution_system.component_name,
        my_heat_distribution_system.ThermalPowerDelivered,
    )
    my_building_controller.connect_input(
        my_building_controller.ResidenceTemperature,
        my_building_controller.component_name,
        my_building.TemperatureIndoorAir
    )

    my_building_controller.connect_input(
        my_building_controller.ReferenceMaxHeatBuildingDemand,
        my_building_controller.component_name,
        my_building.ReferenceMaxHeatBuildingDemand
    )

    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.WaterTemperatureInputFromHeatWaterStorage,
        my_simple_heat_water_storage.component_name,
        my_simple_heat_water_storage.MeanWaterTemperatureInWaterStorage,
    )
    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.ElectricityInput,
        my_base_electricity_load_profile.component_name,
        my_base_electricity_load_profile.ElectricityOutput,
    )

    my_heat_pump.connect_input(
        my_heat_pump.State,
        my_heat_pump_controller.component_name,
        my_heat_pump_controller.State,
    )
    my_heat_pump.connect_input(
        my_heat_pump.TemperatureOutside,
        my_weather.component_name,
        my_weather.TemperatureOutside,
    )

    my_heat_pump.connect_input(
        my_heat_pump.WaterTemperatureInputFromHeatWaterStorage,
        my_simple_heat_water_storage.component_name,
        my_simple_heat_water_storage.MeanWaterTemperatureInWaterStorage,
    )

    my_heat_pump.connect_input(
        my_heat_pump.MaxThermalBuildingDemand,
        my_building.component_name,
        my_building.ReferenceMaxHeatBuildingDemand,
    )

    my_simple_heat_water_storage.connect_input(
        my_simple_heat_water_storage.WaterTemperatureFromHeatDistributionSystem,
        my_heat_distribution_system.component_name,
        my_heat_distribution_system.WaterTemperatureOutput,
    )

    my_simple_heat_water_storage.connect_input(
        my_simple_heat_water_storage.WaterTemperatureFromHeatGenerator,
        my_heat_pump.component_name,
        my_heat_pump.WaterTemperatureOutput,
    )

    my_simple_heat_water_storage.connect_input(
        my_simple_heat_water_storage.WaterMassFlowRateFromHeatGenerator,
        my_heat_pump.component_name,
        my_heat_pump.HeatPumpWaterMassFlowRate,
    )
    my_simple_heat_water_storage.connect_input(
        my_simple_heat_water_storage.WaterMassFlowRateFromHeatDistributionSystem,
        my_heat_distribution_system.component_name,
        my_heat_distribution_system.FloorHeatingWaterMassFlowRate,
    )
    # my_heat_distribution_controller.connect_input(
    #     my_heat_distribution_controller.ResidenceTemperature,
    #     my_building.component_name,
    #     my_building.TemperatureIndoorAir,
    # )
    my_heat_distribution_controller.connect_input(
        my_heat_distribution_controller.RealHeatBuildingDemand,
        my_heat_distribution_controller.component_name,
        my_building_controller.RealHeatBuildingDemand
    )
    my_heat_distribution_controller.connect_input(
        my_heat_distribution_controller.DailyAverageOutsideTemperature,
        my_weather.component_name,
        my_weather.DailyAverageOutsideTemperatures,
    )
    my_heat_distribution_system.connect_input(
        my_heat_distribution_system.State,
        my_heat_distribution_controller.component_name,
        my_heat_distribution_controller.State,
    )
    # my_heat_distribution_system.connect_input(
    #     my_heat_distribution_system.ResidenceTemperature,
    #     my_building.component_name,
    #     my_building.TemperatureIndoorAir,
    # )
    my_heat_distribution_system.connect_input(
        my_heat_distribution_system.RealHeatBuildingDemand,
        my_heat_distribution_system.component_name,
        my_heat_distribution_controller.RealHeatBuildingDemandPassedToHeatDistributionSystem
    )
    my_heat_distribution_system.connect_input(
        my_heat_distribution_system.MaxThermalBuildingDemand,
        my_building.component_name,
        my_building.ReferenceMaxHeatBuildingDemand,
    )

    my_heat_distribution_system.connect_input(
        my_heat_distribution_system.WaterTemperatureInput,
        my_simple_heat_water_storage.component_name,
        my_simple_heat_water_storage.MeanWaterTemperatureInWaterStorage,
    )

    # =================================================================================================================================
    # Add Components to Simulation Parameters

    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_base_electricity_load_profile)
    my_sim.add_component(my_building)
    my_sim.add_component(my_heat_pump_controller)
    my_sim.add_component(my_heat_pump)
    my_sim.add_component(my_simple_heat_water_storage)
    my_sim.add_component(my_heat_distribution_system)
    my_sim.add_component(my_heat_distribution_controller)
