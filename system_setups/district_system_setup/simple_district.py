"""Basic district system setup."""

# clean

import os
from typing import Optional, Any
import datetime
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.simulator import SimulationParameters
from hisim.components.weather import WeatherDataSourceEnum
from hisim import loadtypes
from hisim import utils
from hisim.components import generic_pv_system
from hisim.components import weather
from hisim.components import electricity_meter
from hisim.components import controller_l2_district_energy_management_system
from hisim.postprocessingoptions import PostProcessingOptions
from hisim import log
from system_setups.district_system_setup import simple_generic_household


__authors__ = "Jonas Hoppe"
__copyright__ = ""
__credits__ = ["Jonas Hoppe"]
__license__ = ""
__version__ = ""
__maintainer__ = ""
__status__ = ""


@dataclass_json
@dataclass
class DistrictConfig:
    """Configuration for BuildingPv."""

    number_of_buildings: int
    start_date: datetime.datetime
    end_date: datetime.datetime
    seconds_per_timestep: int
    location_district: str
    latitude_district: float
    longitude_district: float
    weather_data_source_district: WeatherDataSourceEnum
    weather_predictive_control_district: bool
    distance_weatherstations_district: float
    district_name: str
    pv_district_building_name: str
    name_pv_district: str
    nominal_power_pv_district: float
    module_database_pv_district: generic_pv_system.PVLibModuleAndInverterEnum
    inverter_database_pv_district: generic_pv_system.PVLibModuleAndInverterEnum
    load_module_data_pv_district: bool
    module_name_pv_district: str
    integrate_inverter_pv_district: bool
    inverter_name_pv_district: str
    azimuth_pv_district: float
    tilt_pv_district: float
    share_of_maximum_pv_potential_pv_district: float
    source_weight_pv_district: float
    location_pv_district: str
    investment_costs_in_euro_pv_district: float
    co2_footprint_pv_district: float
    maintenance_cost_as_percentage_of_investment_pv_district: float
    lifetime_pv_district: float
    predictive_pv_district: bool
    predictive_control_pv_district: bool
    prediction_horizon_pv_district: Optional[int]

    ems_district_existing: bool

    ems_district_building_name: str
    ems_district_component_name: str
    ems_district_strategy: controller_l2_district_energy_management_system.EMSControlStrategy
    ems_district_limit_to_shave: float
    ems_district_building_temperature_offset_value: float
    ems_district_storage_temperature_offset_value: float
    ems_district_district_heating_storage_offset_value: float
    source_weight_heatpump_district: float

    @classmethod
    def get_default(cls, location_district="AACHEN"):
        """Get default BuildingPVConfig."""

        return DistrictConfig(
            number_of_buildings=2,
            start_date=datetime.datetime(year=2023, month=1, day=1, hour=0, minute=0, second=0),
            end_date=datetime.datetime(year=2023, month=2, day=1, hour=0, minute=0, second=0),
            seconds_per_timestep=60 * 60,
            location_district=location_district,
            latitude_district=50.775,
            longitude_district=6.083,
            weather_data_source_district=WeatherDataSourceEnum.DWD_TRY,
            weather_predictive_control_district=False,
            distance_weatherstations_district=30,
            district_name=loadtypes.DistrictNames.DISTRICT.value,
            pv_district_building_name=loadtypes.DistrictNames.DISTRICT.value,
            name_pv_district="PV_Park",
            nominal_power_pv_district=0,
            module_database_pv_district=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE,
            inverter_database_pv_district=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE,
            load_module_data_pv_district=False,
            module_name_pv_district="Hanwha HSL60P6-PA-4-250T [2013]",
            integrate_inverter_pv_district=True,
            inverter_name_pv_district="ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_",
            azimuth_pv_district=180,
            tilt_pv_district=30,
            share_of_maximum_pv_potential_pv_district=1.0,
            source_weight_pv_district=999,
            location_pv_district=location_district,
            co2_footprint_pv_district=15000 * 1e-3 * 330.51,
            investment_costs_in_euro_pv_district=15000 * 1e-3 * 794.41,
            maintenance_cost_as_percentage_of_investment_pv_district=0.01,
            lifetime_pv_district=25,
            predictive_pv_district=False,
            predictive_control_pv_district=False,
            prediction_horizon_pv_district=None,
            ems_district_existing=False,
            ems_district_building_name=loadtypes.DistrictNames.DISTRICT.value,
            ems_district_component_name="EMS",
            ems_district_strategy=controller_l2_district_energy_management_system.EMSControlStrategy.DISTRICT_OPTIMIZECONSUMPTION_PARALLEL,
            ems_district_limit_to_shave=0,
            ems_district_building_temperature_offset_value=0,
            ems_district_storage_temperature_offset_value=0,
            ems_district_district_heating_storage_offset_value=0,
            source_weight_heatpump_district=999,
        )


def setup_function(
    my_sim: Any, my_simulation_parameters: Optional[SimulationParameters] = None
) -> None:  # noqa: too-many-statements
    """Household system setup."""

    # =================================================================================================================================
    # SETUP
    my_config = DistrictConfig.get_default()

    # Set Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters(
            start_date=my_config.start_date,
            end_date=my_config.end_date,
            seconds_per_timestep=my_config.seconds_per_timestep,
        )
        my_simulation_parameters.multiple_buildings = True
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.GENERATE_PDF_REPORT)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.INCLUDE_CONFIGS_IN_PDF_REPORT)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.EXPORT_TO_CSV)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
        my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON)

        # my_simulation_parameters.logging_level = 4

    my_sim.set_simulation_parameters(my_simulation_parameters)

    # =================================================================================================================================
    # Build Weather district
    location_entry_district = weather.LocationEnum[my_config.location_district]
    my_weather_config_district = weather.WeatherConfig(
        building_name=my_config.district_name,
        name="Weather",
        location=my_config.location_district,
        source_path=os.path.join(
            utils.get_input_directory(),
            "weather",
            location_entry_district.value[1],
            location_entry_district.value[2],
            location_entry_district.value[3],
        ),
        data_source=my_config.weather_data_source_district,
        predictive_control=my_config.weather_predictive_control_district,
    )

    my_weather_district = weather.Weather(
        config=my_weather_config_district, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_weather_district)
    # =================================================================================================================================
    # Build Building
    district_list_bui_names = []
    for i in range(my_config.number_of_buildings):
        district_list_bui_names.append("BUI" + str(i + 1))

    building_config_list = []
    for building_name in district_list_bui_names:
        my_building_config: simple_generic_household.GenericBuildingConfig
        my_building_config = simple_generic_household.GenericBuildingConfig.get_default_generic_building_config(
            building_name=building_name
        )
        parameter_names = list(simple_generic_household.GenericBuildingConfig.__annotations__.keys())
        default_values = {k: getattr(my_building_config, k) for k in parameter_names}
        parameter_values_list = [(param, default_values[param]) for param in parameter_names]
        building_config_list.append(parameter_values_list)
        log.information("No module config path from the simulator was given. Use default config.")

    bui_config = simple_generic_household.GenericBuildingConfig.get_default_generic_building_config()
    district_list = []
    for i, name in enumerate(district_list_bui_names):
        log.information(f"Implement Household {name} in district")

        for parameter, wert in building_config_list[i]:
            if hasattr(bui_config, parameter):
                aktueller_wert = getattr(bui_config, parameter)
                if aktueller_wert == wert:
                    pass
                else:
                    setattr(bui_config, parameter, wert)
            else:
                pass

        district_list.append(
            simple_generic_household.GenericBuilding(
                my_sim=my_sim,
                my_simulation_parameters=my_simulation_parameters,
                config=bui_config,
                location=my_config.location_district,
            )
        )

    # =================================================================================================================================
    # Build districts PV
    my_photovoltaic_system_district_config = generic_pv_system.PVSystemConfig(
        building_name=my_config.pv_district_building_name,
        name=my_config.name_pv_district,
        time=my_config.start_date.year,
        power_in_watt=my_config.nominal_power_pv_district,
        module_database=my_config.module_database_pv_district,
        inverter_database=my_config.inverter_database_pv_district,
        load_module_data=my_config.load_module_data_pv_district,
        module_name=my_config.module_name_pv_district,
        integrate_inverter=my_config.integrate_inverter_pv_district,
        inverter_name=my_config.inverter_name_pv_district,
        azimuth=my_config.azimuth_pv_district,
        tilt=my_config.tilt_pv_district,
        share_of_maximum_pv_potential=my_config.share_of_maximum_pv_potential_pv_district,
        source_weight=my_config.source_weight_pv_district,
        location=my_config.location_pv_district,
        device_co2_footprint_in_kg=my_config.co2_footprint_pv_district,
        investment_costs_in_euro=my_config.investment_costs_in_euro_pv_district,
        maintenance_costs_in_euro_per_year=my_config.maintenance_cost_as_percentage_of_investment_pv_district
        * my_config.investment_costs_in_euro_pv_district,
        subsidy_as_percentage_of_investment_costs=0.0,
        lifetime_in_years=my_config.lifetime_pv_district,
        predictive=my_config.predictive_pv_district,
        predictive_control=my_config.predictive_control_pv_district,
        prediction_horizon=my_config.prediction_horizon_pv_district,
    )

    my_photovoltaic_system_district = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_district_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    my_sim.add_component(my_photovoltaic_system_district, connect_automatically=True)
    # =================================================================================================================================
    # Build electricity grid of district

    my_electricity_meter_district_config = electricity_meter.ElectricityMeterConfig(
        building_name=my_config.district_name,
        name="ElectricityMeter",
        device_co2_footprint_in_kg=None,
        investment_costs_in_euro=None,
        lifetime_in_years=None,
        maintenance_costs_in_euro_per_year=None,
        subsidy_as_percentage_of_investment_costs=None,
    )

    my_electricity_meter_district = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters, config=my_electricity_meter_district_config
    )

    my_sim.add_component(my_electricity_meter_district)
    # =================================================================================================================================
    # Build districts EMS

    if my_config.ems_district_existing is True:
        my_electricity_meter_district.surplus_electricity_unused_to_district_ems_from_building_ems_output.postprocessing_flag = (
            []
        )
        my_electricity_meter_district.electricity_consumption_uncontrolled_in_watt_channel.postprocessing_flag = []

        my_ems_district_config = controller_l2_district_energy_management_system.EMSDistrictConfig(
            building_name=my_config.ems_district_building_name,
            name=my_config.ems_district_component_name,
            strategy=my_config.ems_district_strategy,
            limit_to_shave=my_config.ems_district_limit_to_shave,
            building_indoor_temperature_offset_value=my_config.ems_district_building_temperature_offset_value,
            domestic_hot_water_storage_temperature_offset_value=my_config.ems_district_storage_temperature_offset_value,
            space_heating_water_storage_temperature_offset_value=my_config.ems_district_district_heating_storage_offset_value,
        )

        my_ems_district = controller_l2_district_energy_management_system.L2GenericDistrictEnergyManagementSystem(
            config=my_ems_district_config,
            my_simulation_parameters=my_simulation_parameters,
        )

        my_ems_district.add_component_input_and_connect(
            source_object_name=my_photovoltaic_system_district.component_name,
            source_component_output=my_photovoltaic_system_district.ElectricityOutput,
            source_load_type=loadtypes.LoadTypes.ELECTRICITY,
            source_unit=loadtypes.Units.WATT,
            source_tags=[loadtypes.ComponentType.PV, loadtypes.InandOutputType.ELECTRICITY_PRODUCTION],
            source_weight=999,
        )

        my_electricity_meter_district.add_component_input_and_connect(
            source_object_name=my_ems_district.component_name,
            source_component_output=my_ems_district.TotalElectricityToGrid,
            source_load_type=loadtypes.LoadTypes.ELECTRICITY,
            source_unit=loadtypes.Units.WATT,
            source_tags=[loadtypes.InandOutputType.ELECTRICITY_PRODUCTION],
            source_weight=999,
        )
        my_electricity_meter_district.add_component_input_and_connect(
            source_object_name=my_ems_district.component_name,
            source_component_output=my_ems_district.TotalElectricityFromGrid,
            source_load_type=loadtypes.LoadTypes.ELECTRICITY,
            source_unit=loadtypes.Units.WATT,
            source_tags=[loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            source_weight=999,
        )

        my_sim.add_component(my_ems_district, connect_automatically=True)

    if my_config.ems_district_existing is False:
        for i, district in enumerate(district_list):
            my_electricity_meter_district.add_component_input_and_connect(
                source_object_name=district.electricity_meter_bui.component_name,
                source_component_output=district.electricity_meter_bui.ElectricityFromGridInWatt,
                source_load_type=loadtypes.LoadTypes.ELECTRICITY,
                source_unit=loadtypes.Units.WATT,
                source_tags=[
                    loadtypes.ComponentType.BUILDINGS,
                    loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                ],
                source_weight=999,
            )

            my_electricity_meter_district.add_component_input_and_connect(
                source_object_name=district.electricity_meter_bui.component_name,
                source_component_output=district.electricity_meter_bui.ElectricityToGridInWatt,
                source_load_type=loadtypes.LoadTypes.ELECTRICITY,
                source_unit=loadtypes.Units.WATT,
                source_tags=[loadtypes.ComponentType.BUILDINGS, loadtypes.InandOutputType.ELECTRICITY_PRODUCTION],
                source_weight=999,
            )

            my_electricity_meter_district.add_component_input_and_connect(
                source_object_name=my_photovoltaic_system_district.component_name,
                source_component_output=my_photovoltaic_system_district.ElectricityOutput,
                source_load_type=loadtypes.LoadTypes.ELECTRICITY,
                source_unit=loadtypes.Units.WATT,
                source_tags=[
                    loadtypes.ComponentType.PV,
                    loadtypes.InandOutputType.ELECTRICITY_PRODUCTION,
                ],
                source_weight=999,
            )
