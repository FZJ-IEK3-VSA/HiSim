"""Basic household for districts."""

# clean

from typing import Optional, Any, Union, List
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from utspclient.helpers.lpgdata import Households
from utspclient.helpers.lpgpythonbindings import JsonReference
from hisim import component as cp
from hisim.components import (
    loadprofilegenerator_utsp_connector,
    generic_pv_system,
    building,
    advanced_heat_pump_hplib,
    advanced_battery_bslib,
    controller_l2_energy_management_system,
    simple_water_storage,
    heat_distribution_system,
    generic_heat_pump_modular,
    generic_hot_water_storage_modular,
    controller_l1_heatpump,
    electricity_meter,
)
from hisim.component import (
    ConfigBase,
    DisplayConfig,
)
from hisim import loadtypes as lt
from hisim.units import Quantity, Celsius, Watt

__authors__ = "Jonas Hoppe"
__copyright__ = ""
__credits__ = ["Jonas Hoppe"]
__license__ = "-"
__version__ = ""
__maintainer__ = ""
__status__ = ""


@dataclass_json
@dataclass
class GenericBuildingConfig(ConfigBase):
    """Configuration for BuildingPv."""

    building_name: str
    name: str
    building_id: str
    pv_azimuth: float
    pv_tilt: float
    pv_rooftop_capacity_in_kilowatt: Optional[float]
    share_of_maximum_pv_potential: float
    building_code: str
    conditioned_floor_area_in_m2: float
    number_of_dwellings_per_building: int
    norm_heating_load_in_kilowatt: Optional[float]
    lpg_households: List[str]

    @classmethod
    def get_default_generic_building_config(cls, building_name="BUI1"):
        """Get default BuildingPVConfig."""

        return GenericBuildingConfig(
            building_name=building_name,
            name="Generic_Building",
            building_id="default_building",
            pv_azimuth=180,
            pv_tilt=30,
            pv_rooftop_capacity_in_kilowatt=None,
            share_of_maximum_pv_potential=1,
            building_code="DE.N.SFH.05.Gen.ReEx.001.002",
            conditioned_floor_area_in_m2=121.2,
            number_of_dwellings_per_building=1,
            norm_heating_load_in_kilowatt=None,
            lpg_households=["CHR01_Couple_both_at_Work"],
        )


class GenericBuilding(cp.Component):
    """Simple Generic Building."""

    electricity_meter_bui: Any

    def __init__(
        self, my_sim: Any, my_simulation_parameters: Any, config: GenericBuildingConfig, location: Any
    ) -> None:
        """Simple Generic Building."""

        my_config = config

        super().__init__(
            name=my_config.building_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(display_in_webtool=False),
        )

        # =================================================================================================================================
        # Set System Parameters
        building_name = my_config.building_name

        # Set Heat Pump Controller
        hp_controller_mode = 2  # mode 1 for heating/off and mode 2 for heating/cooling/off
        heating_reference_temperature_in_celsius = -7.0
        # Set gas meter (default is False, is set true when gas heaters are used)

        # Set Photovoltaic System
        azimuth = my_config.pv_azimuth
        tilt = my_config.pv_tilt
        if my_config.pv_rooftop_capacity_in_kilowatt is not None:
            pv_power_in_watt = my_config.pv_rooftop_capacity_in_kilowatt * 1000
        else:
            pv_power_in_watt = None
        share_of_maximum_pv_potential = 1  # my_config.share_of_maximum_pv_potential

        # Set Building (scale building according to total base area and not absolute floor area)
        building_code = my_config.building_code
        total_base_area_in_m2 = None
        absolute_conditioned_floor_area_in_m2 = my_config.conditioned_floor_area_in_m2
        number_of_apartments = my_config.number_of_dwellings_per_building
        if my_config.norm_heating_load_in_kilowatt is not None:
            max_thermal_building_demand_in_watt = my_config.norm_heating_load_in_kilowatt * 1000
        else:
            max_thermal_building_demand_in_watt = None

        # Set Occupancy

        # get household attribute jsonreferences from list of strings
        lpg_households: Union[JsonReference, List[JsonReference]]
        if isinstance(my_config.lpg_households, List):
            if len(my_config.lpg_households) == 1:
                lpg_households = getattr(Households, my_config.lpg_households[0])
            elif len(my_config.lpg_households) > 1:
                lpg_households = []
                for household_string in my_config.lpg_households:
                    if hasattr(Households, household_string):
                        lpg_household = getattr(Households, household_string)
                        lpg_households.append(lpg_household)
                        print(lpg_household)
            else:
                raise ValueError("Config list with lpg household is empty.")
        else:
            raise TypeError(f"Type {type(my_config.lpg_households)} is incompatible. Should be List[str].")

        # =================================================================================================================================
        # Build Basic Components
        # Build Building
        my_building_config = building.BuildingConfig.get_default_german_single_family_home(
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            max_thermal_building_demand_in_watt=max_thermal_building_demand_in_watt,
            building_name=building_name,
        )
        my_building_config.building_code = building_code
        my_building_config.total_base_area_in_m2 = total_base_area_in_m2
        my_building_config.absolute_conditioned_floor_area_in_m2 = absolute_conditioned_floor_area_in_m2
        my_building_config.number_of_apartments = number_of_apartments
        my_building_config.enable_opening_windows = True
        my_building_information = building.BuildingInformation(config=my_building_config)
        my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
        # Add to simulator
        my_sim.add_component(my_building, connect_automatically=True)

        # Build Occupancy
        my_occupancy_config = (
            loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config(
                building_name=building_name,
            )
        )
        my_occupancy_config.data_acquisition_mode = loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_UTSP
        my_occupancy_config.household = lpg_households

        my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
            config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
        )
        # Add to simulator
        my_sim.add_component(my_occupancy)

        # Build PV
        if pv_power_in_watt is None:
            my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
                rooftop_area_in_m2=my_building_information.roof_area_in_m2,
                share_of_maximum_pv_potential=share_of_maximum_pv_potential,
                location=location,
                building_name=building_name,
            )
        else:
            my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.get_default_pv_system(
                power_in_watt=pv_power_in_watt,
                share_of_maximum_pv_potential=share_of_maximum_pv_potential,
                location=location,
                building_name=building_name,
            )

        my_photovoltaic_system_config.azimuth = azimuth
        my_photovoltaic_system_config.tilt = tilt

        my_photovoltaic_system = generic_pv_system.PVSystem(
            config=my_photovoltaic_system_config,
            my_simulation_parameters=my_simulation_parameters,
        )
        # Add to simulator
        my_sim.add_component(my_photovoltaic_system, connect_automatically=True)

        # Build Heat Distribution Controller
        my_heat_distribution_controller_config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
            set_heating_temperature_for_building_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=my_building_information.set_cooling_temperature_for_building_in_celsius,
            heating_load_of_building_in_watt=my_building_information.max_thermal_building_demand_in_watt,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            building_name=building_name,
        )
        # my_heat_distribution_controller_config.heating_system = heat_distribution_system.HeatDistributionSystemType.RADIATOR

        my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
            my_simulation_parameters=my_simulation_parameters,
            config=my_heat_distribution_controller_config,
        )
        my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
            config=my_heat_distribution_controller_config
        )
        # Add to simulator
        my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)

        # Set sizing option for Hot water Storage
        sizing_option = simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP

        # Build Heat Pump Controller
        my_heat_pump_controller_config = (
            advanced_heat_pump_hplib.HeatPumpHplibControllerL1Config.get_default_generic_heat_pump_controller_config(
                heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type,
                building_name=building_name,
            )
        )
        my_heat_pump_controller_config.mode = hp_controller_mode

        my_heat_pump_controller = advanced_heat_pump_hplib.HeatPumpHplibController(
            config=my_heat_pump_controller_config,
            my_simulation_parameters=my_simulation_parameters,
        )
        # Add to simulator
        my_sim.add_component(my_heat_pump_controller, connect_automatically=True)

        # Build Heat Pump
        my_heat_pump_config = advanced_heat_pump_hplib.HeatPumpHplibConfig.get_scaled_advanced_hp_lib(
            heating_load_of_building_in_watt=Quantity(
                my_building_information.max_thermal_building_demand_in_watt, Watt
            ),
            heating_reference_temperature_in_celsius=Quantity(heating_reference_temperature_in_celsius, Celsius),
            building_name=building_name,
        )

        my_heat_pump = advanced_heat_pump_hplib.HeatPumpHplib(
            config=my_heat_pump_config,
            my_simulation_parameters=my_simulation_parameters,
        )
        # Add to simulator
        my_sim.add_component(my_heat_pump, connect_automatically=True)

        # Build DHW (this is taken from household_3_advanced_hp_diesel-car_pv_battery.py)
        my_dhw_heatpump_config = (
            generic_heat_pump_modular.HeatPumpConfig.get_scaled_waterheating_to_number_of_apartments(
                number_of_apartments=my_building_information.number_of_apartments,
                default_power_in_watt=6000,
                building_name=building_name,
            )
        )
        my_dhw_heatpump_controller_config = (
            controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
                name="DHWHeatpumpController",
                building_name=building_name,
            )
        )
        my_dhw_storage_config = (
            generic_hot_water_storage_modular.StorageConfig.get_scaled_config_for_boiler_to_number_of_apartments(
                number_of_apartments=my_building_information.number_of_apartments,
                default_volume_in_liter=450,
                building_name=building_name,
            )
        )
        my_dhw_storage_config.compute_default_cycle(
            temperature_difference_in_kelvin=my_dhw_heatpump_controller_config.t_max_heating_in_celsius
            - my_dhw_heatpump_controller_config.t_min_heating_in_celsius
        )
        my_domnestic_hot_water_storage = generic_hot_water_storage_modular.HotWaterStorage(
            my_simulation_parameters=my_simulation_parameters, config=my_dhw_storage_config
        )
        my_domnestic_hot_water_heatpump_controller = controller_l1_heatpump.L1HeatPumpController(
            my_simulation_parameters=my_simulation_parameters,
            config=my_dhw_heatpump_controller_config,
        )
        my_domnestic_hot_water_heatpump = generic_heat_pump_modular.ModularHeatPump(
            config=my_dhw_heatpump_config, my_simulation_parameters=my_simulation_parameters
        )
        # Add to simulator
        my_sim.add_component(my_domnestic_hot_water_storage, connect_automatically=True)
        my_sim.add_component(my_domnestic_hot_water_heatpump_controller, connect_automatically=True)
        my_sim.add_component(my_domnestic_hot_water_heatpump, connect_automatically=True)

        # Build Heat Water Storage
        my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.get_scaled_hot_water_storage(
            max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt,
            temperature_difference_between_flow_and_return_in_celsius=my_hds_controller_information.temperature_difference_between_flow_and_return_in_celsius,
            sizing_option=sizing_option,
            building_name=building_name,
        )
        my_simple_hot_water_storage = simple_water_storage.SimpleHotWaterStorage(
            config=my_simple_heat_water_storage_config,
            my_simulation_parameters=my_simulation_parameters,
        )
        # Add to simulator
        my_sim.add_component(my_simple_hot_water_storage, connect_automatically=True)

        # Build Heat Distribution System
        my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
            water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kp_per_second,
            absolute_conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
            building_name=building_name,
            heating_system=my_hds_controller_information.hds_controller_config.heating_system,
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
            config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(
                building_name=building_name,
            ),
        )

        # use ems and battery only when PV is used
        if share_of_maximum_pv_potential != 0:

            # Build EMS
            my_electricity_controller_config = controller_l2_energy_management_system.EMSConfig.get_default_config_ems(
                building_name=building_name
            )

            my_electricity_controller = controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
                my_simulation_parameters=my_simulation_parameters,
                config=my_electricity_controller_config,
            )

            # Build Battery
            my_advanced_battery_config = advanced_battery_bslib.BatteryConfig.get_scaled_battery(
                total_pv_power_in_watt_peak=my_photovoltaic_system_config.power_in_watt,
                building_name=building_name,
            )
            my_advanced_battery = advanced_battery_bslib.Battery(
                my_simulation_parameters=my_simulation_parameters,
                config=my_advanced_battery_config,
            )

            # -----------------------------------------------------------------------------------------------------------------
            # Add outputs to EMS
            my_electricity_controller.add_component_input_and_connect(
                source_object_name=my_photovoltaic_system.component_name,
                source_component_output=my_photovoltaic_system.ElectricityOutput,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.PV, lt.InandOutputType.ELECTRICITY_PRODUCTION],
                source_weight=999,
            )

            my_heat_distribution_controller.connect_input(
                my_heat_distribution_controller.BuildingTemperatureModifier,
                my_electricity_controller.component_name,
                my_electricity_controller.BuildingIndoorTemperatureModifier,
            )

            my_building.connect_input(
                my_building.BuildingTemperatureModifier,
                my_electricity_controller.component_name,
                my_electricity_controller.BuildingIndoorTemperatureModifier,
            )

            my_electricity_controller.add_component_input_and_connect(
                source_object_name=my_occupancy.component_name,
                source_component_output=my_occupancy.ElectricalPowerConsumption,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.RESIDENTS, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
                source_weight=1,
            )
            my_electricity_controller.add_component_output(
                source_output_name=f"ElectricityToOrFromGridOf{my_occupancy.get_classname()}_",
                source_tags=[
                    lt.ComponentType.RESIDENTS,
                    lt.InandOutputType.ELECTRICITY_TARGET,
                ],
                source_weight=1,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                output_description="Target electricity for Occupancy. ",
            )

            my_domnestic_hot_water_heatpump_controller.connect_input(
                my_domnestic_hot_water_heatpump_controller.StorageTemperatureModifier,
                my_electricity_controller.component_name,
                my_electricity_controller.DomesticHotWaterStorageTemperatureModifier,
            )
            my_electricity_controller.add_component_input_and_connect(  # Anteil Heatpump für DHW erzeugung
                source_object_name=my_domnestic_hot_water_heatpump.component_name,
                source_component_output=my_domnestic_hot_water_heatpump.ElectricityOutput,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.HEAT_PUMP_DHW, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
                source_weight=2,
            )

            my_electricity_controller.add_component_output(
                source_output_name=f"ElectricityToOrFromGridOfDHW{my_domnestic_hot_water_heatpump.get_classname()}_",
                source_tags=[
                    lt.ComponentType.HEAT_PUMP_DHW,
                    lt.InandOutputType.ELECTRICITY_TARGET,
                ],
                source_weight=2,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                output_description="ElectricityToOrFromGrid for dhw Heat Pump. ",
            )

            my_heat_pump_controller.connect_input(
                my_heat_pump_controller.SimpleHotWaterStorageTemperatureModifier,
                my_electricity_controller.component_name,
                my_electricity_controller.SpaceHeatingWaterStorageTemperatureModifier,
            )

            my_electricity_controller.add_component_input_and_connect(  # Anteil Heatpump für Heizung erzeugung
                source_object_name=my_heat_pump.component_name,
                source_component_output=my_heat_pump.ElectricalInputPower,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.HEAT_PUMP_BUILDING,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                ],
                source_weight=3,
            )

            my_electricity_controller.add_component_output(
                source_output_name=f"ElectricityToOrFromGridOfSH{my_heat_pump.get_classname()}_",
                source_tags=[
                    lt.ComponentType.HEAT_PUMP_BUILDING,
                    lt.InandOutputType.ELECTRICITY_TARGET,
                ],
                source_weight=3,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                output_description="ElectricityToOrFromGrid for Space Heating Heat Pump. ",
            )

            my_electricity_controller.add_component_input_and_connect(
                source_object_name=my_advanced_battery.component_name,
                source_component_output=my_advanced_battery.AcBatteryPowerUsed,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
                source_weight=5,
            )

            loading_power_input_for_battery_in_watt = my_electricity_controller.add_component_output(
                source_output_name=f"ElectricityToOrFrom{my_advanced_battery.get_classname()}_",
                source_tags=[
                    lt.ComponentType.BATTERY,
                    lt.InandOutputType.ELECTRICITY_TARGET,
                ],
                source_weight=5,
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
            my_sim.add_component(my_electricity_controller)

        # when no PV is used, connect electricty meter automatically
        else:
            my_sim.add_component(my_electricity_meter, connect_automatically=True)

        # connection from building to district

        self.electricity_meter_bui = my_electricity_meter
