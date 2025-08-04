"""More advanced heat pump module.

See library on https://github.com/FZJ-IEK3-VSA/HPLib/tree/main/HPLib

two controller: one dhw controller and one building heating controller

priority on dhw, if there is a demand from both in one timestep

preparation on district heating for water/water heatpumps

"""

import hashlib

# clean
import importlib
from enum import IntEnum
from dataclasses import dataclass
from typing import Any, List, Optional, Dict, Union

import pandas as pd
import numpy as np
from dataclass_wizard import JSONWizard
from dataclasses_json import dataclass_json
from hplib import hplib as hpl

# Import modules from HiSim
from hisim.component import (
    Component,
    ComponentInput,
    ComponentOutput,
    SingleTimeStepValues,
    ConfigBase,
    ComponentConnection,
    OpexCostDataClass,
    DisplayConfig,
    CapexCostDataClass,
)
from hisim.components import weather, simple_water_storage, heat_distribution_system
from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.loadtypes import LoadTypes, Units, InandOutputType, OutputPostprocessingRules, ComponentType
from hisim.units import (
    Quantity,
    Watt,
    Celsius,
    Seconds,
    Kilogram,
    Euro,
    Years,
    KilogramPerSecond,
    Unitless
)
from hisim.components.configuration import (
    PhysicsConfig,
    EmissionFactorsAndCostsForFuelsConfig,
)

from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiTagEnumClass
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions

__authors__ = "Jonas Hoppe"
__copyright__ = ""
__credits__ = ["Jonas Hoppe"]
__license__ = "-"
__version__ = ""
__maintainer__ = ""
__status__ = ""


class PositionHotWaterStorageInSystemSetup(IntEnum):
    """Set Postion of Hot Water Storage in system setup.

    PARALLEL:
    Hot Water Storage is parallel to heatpump and hds, massflow of heatpump and heat distribution system are independent of each other.
    Heatpump massflow is calculated in hp model, hds massflow is calculated in hds model.

    SERIE:
    Hot Water Storage in series to hp/hds, massflow of hds is an input and connected to hp, hot water storage is between output of hds and input of hp

    NO_STORAGE:
    No Hot Water Storage in system setup for space heating
    """

    PARALLEL = 1
    SERIE = 2
    NO_STORAGE = 3


@dataclass_json
@dataclass
class MoreAdvancedHeatPumpHPLibConfig(ConfigBase):
    """MoreAdvancedHeatPumpHPLibConfig."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return MoreAdvancedHeatPumpHPLib.get_full_classname()

    building_name: str
    name: str
    model: str
    fluid_primary_side: str
    group_id: int
    heating_reference_temperature_in_celsius: Quantity[float, Celsius]  # before t_in
    flow_temperature_in_celsius: Quantity[float, Celsius]  # before t_out_val
    set_thermal_output_power_in_watt: Quantity[float, Watt]  # before p_th_set
    cycling_mode: bool
    minimum_running_time_in_seconds: Optional[Quantity[int, Seconds]]
    minimum_idle_time_in_seconds: Optional[Quantity[int, Seconds]]
    minimum_thermal_output_power_in_watt: Quantity[float, Watt]
    position_hot_water_storage_in_system: Union[PositionHotWaterStorageInSystemSetup, int]
    with_domestic_hot_water_preparation: bool
    passive_cooling_with_brine: bool
    electrical_input_power_brine_pump_in_watt: Optional[float]
    massflow_nominal_secondary_side_in_kg_per_s: Quantity[float, KilogramPerSecond]
    massflow_nominal_primary_side_in_kg_per_s: Optional[float]
    specific_heat_capacity_of_primary_fluid: Optional[float]
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg: Optional[Quantity[float, Kilogram]]
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[Quantity[float, Euro]]
    #: lifetime in years
    lifetime_in_years: Optional[Quantity[float, Years]]
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[Quantity[float, Euro]]
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[Quantity[float, Unitless]]

    @classmethod
    def get_default_generic_advanced_hp_lib(
        cls,
        building_name: str = "BUI1",
        name: str = "MoreAdvancedHeatPumpHPLib",
        set_thermal_output_power_in_watt: Quantity[float, Watt] = Quantity(8000, Watt),
        heating_reference_temperature_in_celsius: Quantity[float, Celsius] = Quantity(-7.0, Celsius),
        massflow_nominal_secondary_side_in_kg_per_s: Quantity[float, KilogramPerSecond] = Quantity(
            0.333, KilogramPerSecond
        ),
    ) -> "MoreAdvancedHeatPumpHPLibConfig":
        """Gets a default HPLib Heat Pump.

        see default values for air/water hp on:
        https://github.com/FZJ-IEK3-VSA/HPLib/blob/main/HPLib/HPLib.py l.135 "fit_p_th_ref.
        """
        return MoreAdvancedHeatPumpHPLibConfig(
            building_name=building_name,
            name=name,
            model="Generic",
            fluid_primary_side="air",
            group_id=1,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            flow_temperature_in_celsius=Quantity(52, Celsius),
            set_thermal_output_power_in_watt=set_thermal_output_power_in_watt,
            cycling_mode=True,
            minimum_running_time_in_seconds=Quantity(3600, Seconds),
            minimum_idle_time_in_seconds=Quantity(3600, Seconds),
            minimum_thermal_output_power_in_watt=Quantity(1800, Watt),
            position_hot_water_storage_in_system=PositionHotWaterStorageInSystemSetup.PARALLEL,
            with_domestic_hot_water_preparation=False,
            passive_cooling_with_brine=False,
            electrical_input_power_brine_pump_in_watt=None,
            massflow_nominal_secondary_side_in_kg_per_s=massflow_nominal_secondary_side_in_kg_per_s,
            massflow_nominal_primary_side_in_kg_per_s=0,
            specific_heat_capacity_of_primary_fluid=0,
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
        )

    @classmethod
    def get_scaled_advanced_hp_lib(
        cls,
        heating_load_of_building_in_watt: Quantity[float, Watt],
        name: str = "MoreAdvancedHeatPumpHPLib",
        building_name: str = "BUI1",
        heating_reference_temperature_in_celsius: Quantity[float, Celsius] = Quantity(-7.0, Celsius),
        massflow_nominal_secondary_side_in_kg_per_s: Quantity[float, KilogramPerSecond] = Quantity(
            0.333, KilogramPerSecond
        ),
    ) -> "MoreAdvancedHeatPumpHPLibConfig":
        """Gets a default heat pump with scaling according to heating load of the building."""

        set_thermal_output_power_in_watt: Quantity[float, Watt] = heating_load_of_building_in_watt

        return MoreAdvancedHeatPumpHPLibConfig(
            building_name=building_name,
            name=name,
            model="Generic",
            fluid_primary_side="air",
            group_id=1,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            flow_temperature_in_celsius=Quantity(52, Celsius),
            set_thermal_output_power_in_watt=set_thermal_output_power_in_watt,
            cycling_mode=True,
            minimum_running_time_in_seconds=Quantity(3600, Seconds),
            minimum_idle_time_in_seconds=Quantity(3600, Seconds),
            minimum_thermal_output_power_in_watt=Quantity(1800, Watt),
            position_hot_water_storage_in_system=PositionHotWaterStorageInSystemSetup.PARALLEL,
            with_domestic_hot_water_preparation=False,
            passive_cooling_with_brine=False,
            electrical_input_power_brine_pump_in_watt=None,
            massflow_nominal_secondary_side_in_kg_per_s=massflow_nominal_secondary_side_in_kg_per_s,
            massflow_nominal_primary_side_in_kg_per_s=0,
            specific_heat_capacity_of_primary_fluid=0,
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
        )


class MoreAdvancedHeatPumpHPLib(Component):
    """Simulate the heat pump.

    Outputs are heat pump efficiency (cop) as well as electrical (p_el) and
    thermal power (p_th), massflow (m_dot) and output temperature (t_out) for DHW and space heating.
    Model will switch between both states, but priority is on dhw.
    Relevant simulation parameters are loaded within the init for a
    specific or generic heat pump type.
    """

    # Inputs
    OnOffSwitchSH = "OnOffSwitchSpaceHeating"  # 1 = on space heating,  0 = 0ff , -1 = cooling
    OnOffSwitchDHW = "OnOffSwitchDHW"  # 2 = on DHW , 0 = 0ff
    ThermalPowerIsConstantForDHW = "ThermalPowerIsConstantForDHW"  # true/false
    MaxThermalPowerValueForDHW = "MaxThermalPowerValueForDHW"  # max. Leistungswert
    TemperatureInputPrimary = "TemperatureInputPrimary"  # °C
    TemperatureInputSecondary_SH = "TemperatureInputSecondarySpaceHeating"  # °C
    TemperatureInputSecondary_DHW = "TemperatureInputSecondaryDWH"  # °C
    TemperatureAmbient = "TemperatureAmbient"  # °C
    SetHeatingTemperatureSpaceHeating = "SetHeatingTemperatureSpaceHeating"

    # Outputs
    ThermalOutputPowerSH = "ThermalOutputPowerSpaceHeating"  # W
    ThermalOutputPowerDHW = "ThermalOutputPowerDHW"  # W
    ThermalOutputPowerTotal = "ThermalOutputPowerTotalHeatpump"  # W
    ElectricalInputPowerSH = "ElectricalInputPowerSpaceHeating"  # W
    ElectricalInputPowerForCooling = "ElectricalInputPowerForCooling"  # W
    ElectricalInputPowerDHW = "ElectricalInputPowerDHW"  # W
    ElectricalInputPowerTotal = "ElectricalInputPowerTotalHeatpump"
    COP = "COP"  # -
    EER = "EER"  # -
    HeatPumpOnOffState = "OnOffStateHeatpump"
    TemperatureInputSH = "TemperatureInputSpaceHeating"  # °C
    TemperatureInputDHW = "TemperatureInputDHW"  # °C
    TemperatureOutputSH = "TemperatureOutputSpaceHeating"  # °C
    TemperatureOutputDHW = "TemperatureOutputDHW"  # °C
    MassFlowOutputSH = "MassFlowOutputSpaceHeating"  # kg/s
    MassFlowOutputDHW = "MassFlowOutputDHW"  # kg/s
    TimeOnHeating = "TimeOnHeating"  # s
    TimeOnCooling = "TimeOnCooling"  # s
    TimeOff = "TimeOff"  # s
    ThermalEnergyTotal = "ThermalEnergyTotal"  # Wh
    ThermalEnergySH = "ThermalEnergySH"  # Wh
    ThermalEnergyDHW = "ThermalEnergyDHW"  # Wh
    ElectricalEnergyTotal = "ElectricalEnergyTotal"  # Wh
    ElectricalEnergySH = "ElectricalEnergySH"  # Wh
    ElectricalEnergyDHW = "ElectricalEnergyDHW"  # Wh
    ThermalPowerFromEnvironment = "ThermalPowerInputFromEnvironment"  # W
    CumulativeThermalEnergyTotal = "CumulativeThermalEnergyTotal"  # Wh
    CumulativeThermalEnergySH = "CumulativeThermalEnergySH"  # Wh
    CumulativeThermalEnergyDHW = "CumulativeThermalEnergyDHW"  # Wh
    CumulativeElectricalEnergyTotal = "CumulativeElectricalEnergyTotal"  # Wh
    CumulativeElectricalEnergySH = "CumulativeElectricalEnergySH"  # Wh
    CumulativeElectricalEnergyDHW = "CumulativeElectricalEnergyDHW"  # Wh
    MassflowPrimarySide = "MassflowPrimarySide"  # kg/s --- used for Water/water HP
    BrineTemperaturePrimaryIn = "BrineTemperaturePrimaryIn"  # °C
    BrineTemperaturePrimaryOut = "BrineTemperaturePrimaryOut"  # °C
    CounterSwitchToSH = "CounterSwitchToSH"  # Counter of switching to SH != onOff Switch!
    CounterSwitchToDHW = "CounterSwitchToDHW"  # Counter of switching to DHW != onOff Switch!
    CounterOnOff = "CounterOnOff"  # Counter of starting the hp
    DeltaTHeatpumpSecondarySide = (
        "DeltaTHeatpumpSecondarySide"  # Temperature difference between input and output of HP secondary side
    )
    DeltaTHeatpumpPrimarySide = "DeltaTHeatpumpPrimarySide"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: MoreAdvancedHeatPumpHPLibConfig,
        my_display_config: DisplayConfig = DisplayConfig(display_in_webtool=True),
    ):
        """Loads the parameters of the specified heat pump."""

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        # caching for HPLib simulation
        self.calculation_cache: Dict = {}

        self.model = config.model

        self.group_id = config.group_id

        self.t_in = int(config.heating_reference_temperature_in_celsius.value)

        self.t_out_val = int(config.flow_temperature_in_celsius.value)

        self.p_th_set = int(config.set_thermal_output_power_in_watt.value)

        self.cycling_mode = config.cycling_mode

        self.with_domestic_hot_water_preparation = config.with_domestic_hot_water_preparation

        self.passive_cooling_with_brine = config.passive_cooling_with_brine

        self.electrical_input_power_brine_pump_in_watt = config.electrical_input_power_brine_pump_in_watt
        if self.electrical_input_power_brine_pump_in_watt is None:
            self.electrical_input_power_brine_pump_in_watt = 0.0

        self.position_hot_water_storage_in_system = config.position_hot_water_storage_in_system

        # self.m_dot_ref = float(
        #     config.massflow_nominal_secondary_side_in_kg_per_s.value
        #     if config.massflow_nominal_secondary_side_in_kg_per_s
        #     else config.massflow_nominal_secondary_side_in_kg_per_s
        # )

        self.m_dot_ref = config.massflow_nominal_secondary_side_in_kg_per_s.value

        if self.position_hot_water_storage_in_system in [
            PositionHotWaterStorageInSystemSetup.SERIE,
            PositionHotWaterStorageInSystemSetup.NO_STORAGE,
        ]:
            if self.m_dot_ref is None or self.m_dot_ref == 0:
                raise ValueError(
                    """If system setup is without parallel hot water storage, nominal massflow and minimum
                    thermal power of the heat pump must be given an integer value due to constant massflow
                    of water pump."""
                )

        self.fluid_primary_side = config.fluid_primary_side

        self.massflow_nominal_primary_side_in_kg_per_s = config.massflow_nominal_primary_side_in_kg_per_s
        self.specific_heat_capacity_of_primary_fluid = config.specific_heat_capacity_of_primary_fluid

        self.minimum_running_time_in_seconds = (
            config.minimum_running_time_in_seconds.value
            if config.minimum_running_time_in_seconds
            else config.minimum_running_time_in_seconds
        )

        self.minimum_idle_time_in_seconds = (
            config.minimum_idle_time_in_seconds.value
            if config.minimum_idle_time_in_seconds
            else config.minimum_idle_time_in_seconds
        )

        self.minimum_thermal_output_power = config.minimum_thermal_output_power_in_watt.value

        # Component has states
        self.state = MoreAdvancedHeatPumpHPLibState(
            time_on_heating=0,
            time_off=0,
            time_on_cooling=0,
            on_off_previous=0,
            cumulative_thermal_energy_tot_in_watt_hour=0,
            cumulative_thermal_energy_sh_in_watt_hour=0,
            cumulative_thermal_energy_dhw_in_watt_hour=0,
            cumulative_electrical_energy_tot_in_watt_hour=0,
            cumulative_electrical_energy_sh_in_watt_hour=0,
            cumulative_electrical_energy_dhw_in_watt_hour=0,
            counter_switch_sh=0,
            counter_switch_dhw=0,
            counter_onoff=0,
            delta_t_secondary_side=5,
            delta_t_primary_side=0,
        )
        self.previous_state = self.state.self_copy()

        # Load parameters from heat pump database
        self.parameters = hpl.get_parameters(self.model, self.group_id, self.t_in, self.t_out_val, self.p_th_set)
        self.heatpump = hpl.HeatPump(self.parameters)
        self.heatpump.delta_t = 5

        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = (
            PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
        )

        # protect erros for Water/Water Heatpumps
        if self.parameters["Group"].iloc[0] == 1.0 or self.parameters["Group"].iloc[0] == 4.0:
            if self.fluid_primary_side.lower() != "air":
                raise KeyError("HP modell does not fit to heat source in config!")
            if self.passive_cooling_with_brine:
                raise KeyError("HP modell with air as heat source does not support passive cooling with brine!")
            if self.electrical_input_power_brine_pump_in_watt != 0.0:
                raise KeyError("HP modell with air as heat source does not support electrical input power for brine pump!")

        if self.parameters["Group"].iloc[0] == 2.0 or self.parameters["Group"].iloc[0] == 5.0:
            if self.fluid_primary_side.lower() != "brine":
                raise KeyError("HP modell does not fit to heat source in config!")
            if self.massflow_nominal_primary_side_in_kg_per_s == 0:
                raise KeyError(
                    "HP modell with brine/water as heat source need config parameter massflow_nominal_primary_side_in_kg_per_s!"
                )
            if self.specific_heat_capacity_of_primary_fluid == 0:
                raise KeyError(
                    "HP modell with brine/water as heat source need config parameter specific_heat_capacity_of_primary_fluid! "
                    "--> connection with information class of heat source"
                )
            if self.electrical_input_power_brine_pump_in_watt == 0.0:
                raise KeyError(
                    "HP modell with brine/water as heat source need config parameter electrical_input_power_brine_pump_in_watt!"
                )

        if self.parameters["Group"].iloc[0] == 3.0 or self.parameters["Group"].iloc[0] == 6.0:
            if self.fluid_primary_side.lower() != "water":
                raise KeyError("HP modell does not fit to heat source in config!")
            if self.massflow_nominal_primary_side_in_kg_per_s is None:
                raise KeyError(
                    "HP modell with brine/water as heat source need config parameter massflow_nominal_primary_side_in_kg_per_s!"
                )

            if self.electrical_input_power_brine_pump_in_watt == 0.0 :
                raise KeyError(
                    "HP modell with brine/water as heat source need config parameter electrical_input_power_brine_pump_in_watt!"
                )

        # Define component inputs
        self.on_off_switch_sh: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.OnOffSwitchSH,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            mandatory=False,
        )

        self.t_in_primary: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TemperatureInputPrimary,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=True,
        )

        self.t_in_secondary_sh: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TemperatureInputSecondary_SH,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=False,
        )

        self.t_amb: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TemperatureAmbient,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=True,
        )

        if self.with_domestic_hot_water_preparation:
            self.on_off_switch_dhw: ComponentInput = self.add_input(
                object_name=self.component_name,
                field_name=self.OnOffSwitchDHW,
                load_type=LoadTypes.ANY,
                unit=Units.ANY,
                mandatory=True,
            )

            self.const_thermal_power_truefalse_dhw: ComponentInput = self.add_input(
                object_name=self.component_name,
                field_name=self.ThermalPowerIsConstantForDHW,
                load_type=LoadTypes.ANY,
                unit=Units.ANY,
                mandatory=True,
            )

            self.const_thermal_power_value_dhw: ComponentInput = self.add_input(
                object_name=self.component_name,
                field_name=self.MaxThermalPowerValueForDHW,
                load_type=LoadTypes.ANY,
                unit=Units.ANY,
                mandatory=True,
            )

            self.t_in_secondary_dhw: ComponentInput = self.add_input(
                object_name=self.component_name,
                field_name=self.TemperatureInputSecondary_DHW,
                load_type=LoadTypes.TEMPERATURE,
                unit=Units.CELSIUS,
                mandatory=True,
            )

        if (
            self.position_hot_water_storage_in_system
            in [
                PositionHotWaterStorageInSystemSetup.SERIE,
                PositionHotWaterStorageInSystemSetup.NO_STORAGE,
            ]
            or self.passive_cooling_with_brine
        ):
            self.set_temperature_hp_sh: ComponentInput = self.add_input(
                self.component_name,
                self.SetHeatingTemperatureSpaceHeating,
                LoadTypes.TEMPERATURE,
                Units.CELSIUS,
                True,
            )

        # Define component outputs
        self.p_th_sh: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPowerSH,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description=("Thermal output power hot Water Storage in Watt"),
            postprocessing_flag=[OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )

        self.p_el_sh: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalInputPowerSH,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description="Electricity input power for SpaceHeating in Watt",
        )

        self.p_el_cooling: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalInputPowerForCooling,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
            output_description="Electricity input power for cooling in Watt",
        )

        self.cop: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.COP,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            output_description="COP",
        )
        self.eer: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.EER,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            output_description="EER",
        )

        self.heatpump_state: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.HeatPumpOnOffState,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            output_description="OnOffState",
        )

        self.t_in_sh: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TemperatureInputSH,
            load_type=LoadTypes.HEATING,
            unit=Units.CELSIUS,
            output_description="Temperature Input SpaceHeating in °C",
        )

        self.t_out_sh: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TemperatureOutputSH,
            load_type=LoadTypes.HEATING,
            unit=Units.CELSIUS,
            output_description="Temperature Output SpaceHeating in °C",
        )

        self.m_dot_sh: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.MassFlowOutputSH,
            load_type=LoadTypes.VOLUME,
            unit=Units.KG_PER_SEC,
            output_description="Mass flow output",
        )

        self.time_on_heating: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TimeOnHeating,
            load_type=LoadTypes.TIME,
            unit=Units.SECONDS,
            output_description="Time turned on for heating",
        )

        self.time_on_cooling: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TimeOnCooling,
            load_type=LoadTypes.TIME,
            unit=Units.SECONDS,
            output_description="Time turned on for cooling",
        )

        self.time_off: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TimeOff,
            load_type=LoadTypes.TIME,
            unit=Units.SECONDS,
            output_description="Time turned off",
        )

        self.thermal_power_from_environment: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerFromEnvironment,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description="Thermal Input Power from Environment",
        )

        self.thermal_energy_hp_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalEnergySH,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergySH} will follow.",
        )

        self.electrical_energy_hp_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalEnergySH,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT_HOUR,
            output_description=f"here a description for {self.ElectricalEnergySH} will follow.",
        )

        self.cumulative_hp_thermal_energy_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeThermalEnergySH,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT_HOUR,
            output_description=f"here a description for {self.CumulativeThermalEnergySH} will follow.",
        )

        self.cumulative_hp_electrical_energy_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeElectricalEnergySH,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT_HOUR,
            output_description=f"here a description for {self.CumulativeElectricalEnergySH} will follow.",
        )

        self.counter_on_off_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CounterOnOff,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            output_description=f"{self.CounterOnOff} is a counter of starting procedures hp.",
        )

        self.p_el_tot: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalInputPowerTotal,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[
                InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
            ],
            output_description="Electricity input power for total HP in Watt",
        )

        self.p_th_tot: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPowerTotal,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description="Thermal output power for total HP in Watt",
            postprocessing_flag=[OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )

        self.thermal_energy_hp_tot_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalEnergyTotal,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyTotal} will follow.",
        )

        self.electrical_energy_hp_tot_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalEnergyTotal,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT_HOUR,
            output_description=f"here a description for {self.ElectricalEnergyTotal} will follow.",
        )

        self.cumulative_hp_thermal_energy_tot_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeThermalEnergyTotal,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT_HOUR,
            output_description=f"here a description for {self.CumulativeThermalEnergyTotal} will follow.",
        )

        self.cumulative_hp_electrical_energy_tot_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeElectricalEnergyTotal,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT_HOUR,
            output_description=f"here a description for {self.CumulativeElectricalEnergyTotal} will follow.",
        )

        self.delta_t_hp_secondary_side_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.DeltaTHeatpumpSecondarySide,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.KELVIN,
            output_description=f"{self.DeltaTHeatpumpSecondarySide}.",
        )

        if self.with_domestic_hot_water_preparation:
            self.p_th_dhw: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.ThermalOutputPowerDHW,
                load_type=LoadTypes.HEATING,
                unit=Units.WATT,
                output_description=("Thermal output power dhw Storage in Watt"),
                postprocessing_flag=[OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
            )

            self.p_el_dhw: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.ElectricalInputPowerDHW,
                load_type=LoadTypes.ELECTRICITY,
                unit=Units.WATT,
                output_description="Electricity input power for DHW in Watt",
            )

            self.t_in_dhw: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.TemperatureInputDHW,
                load_type=LoadTypes.HEATING,
                unit=Units.CELSIUS,
                output_description="Temperature Input DHW in °C",
            )

            self.t_out_dhw: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.TemperatureOutputDHW,
                load_type=LoadTypes.HEATING,
                unit=Units.CELSIUS,
                output_description="Temperature Output DHW Water in °C",
            )

            self.m_dot_dhw: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.MassFlowOutputDHW,
                load_type=LoadTypes.VOLUME,
                unit=Units.KG_PER_SEC,
                output_description="Mass flow output",
            )

            self.thermal_energy_hp_dhw_channel: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.ThermalEnergyDHW,
                load_type=LoadTypes.HEATING,
                unit=Units.WATT_HOUR,
                output_description=f"here a description for {self.ThermalEnergyDHW} will follow.",
            )

            self.electrical_energy_hp_dhw_channel: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.ElectricalEnergyDHW,
                load_type=LoadTypes.ELECTRICITY,
                unit=Units.WATT_HOUR,
                output_description=f"here a description for {self.ElectricalEnergyDHW} will follow.",
            )

            self.cumulative_hp_thermal_energy_dhw_channel: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.CumulativeThermalEnergyDHW,
                load_type=LoadTypes.HEATING,
                unit=Units.WATT_HOUR,
                output_description=f"here a description for {self.CumulativeThermalEnergyDHW} will follow.",
            )

            self.cumulative_hp_electrical_energy_dhw_channel: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.CumulativeElectricalEnergyDHW,
                load_type=LoadTypes.ELECTRICITY,
                unit=Units.WATT_HOUR,
                output_description=f"here a description for {self.CumulativeElectricalEnergyDHW} will follow.",
            )

            self.counter_switch_sh_channel: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.CounterSwitchToSH,
                load_type=LoadTypes.ANY,
                unit=Units.ANY,
                output_description=f"{self.CounterSwitchToSH} is a counter of switching the mode to SH, NOT counting starting of on_off.",
            )

            self.counter_switch_dhw_channel: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.CounterSwitchToDHW,
                load_type=LoadTypes.ANY,
                unit=Units.ANY,
                output_description=f"{self.CounterSwitchToDHW} is a counter of switching the mode to DHW, NOT counting starting of on_off.",
            )

        if self.parameters["Group"].iloc[0] in (2, 3, 5, 6):
            self.m_dot_water_primary: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.MassflowPrimarySide,
                load_type=LoadTypes.VOLUME,
                unit=Units.KG_PER_SEC,
                output_description="Massflow of primary Side",
            )
            self.temp_brine_primary_side_in: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.BrineTemperaturePrimaryIn,
                load_type=LoadTypes.TEMPERATURE,
                unit=Units.CELSIUS,
                output_description="Temperature of Water from District Heating Net In HX",
            )
            self.temp_brine_primary_side_out: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.BrineTemperaturePrimaryOut,
                load_type=LoadTypes.TEMPERATURE,
                unit=Units.CELSIUS,
                output_description="Temperature of Water to District Heating Net Out HX",
            )
            self.temperature_difference_primary_side: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.DeltaTHeatpumpPrimarySide,
                load_type=LoadTypes.TEMPERATURE,
                unit=Units.CELSIUS,
                output_description="Temperature difference of brine at primary side",
            )

        self.add_default_connections(self.get_default_connections_from_heat_pump_controller_space_heating())
        self.add_default_connections(self.get_default_connections_from_weather())

        if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL:
            self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())

        if self.with_domestic_hot_water_preparation:
            self.add_default_connections(self.get_default_connections_from_heat_pump_controller_dhw())
            self.add_default_connections(self.get_default_connections_from_dhw_storage())
            self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())
            # self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())

    def get_default_connections_from_heat_pump_controller_space_heating(
        self,
    ):
        """Get default connections."""
        connections = []
        hpc_classname = MoreAdvancedHeatPumpHPLibControllerSpaceHeating.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLib.OnOffSwitchSH,
                hpc_classname,
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.State_SH,
            )
        )
        return connections

    def get_default_connections_from_heat_pump_controller_dhw(
        self,
    ):
        """Get default connections."""
        connections = []
        hpc_dhw_classname = MoreAdvancedHeatPumpHPLibControllerDHW.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLib.OnOffSwitchDHW,
                hpc_dhw_classname,
                MoreAdvancedHeatPumpHPLibControllerDHW.State_dhw,
            )
        )
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLib.ThermalPowerIsConstantForDHW,
                hpc_dhw_classname,
                MoreAdvancedHeatPumpHPLibControllerDHW.ThermalPower_dhw_is_constant,
            )
        )
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLib.MaxThermalPowerValueForDHW,
                hpc_dhw_classname,
                MoreAdvancedHeatPumpHPLibControllerDHW.Value_thermalpower_dhw_is_constant,
            )
        )
        return connections

    def get_default_connections_from_weather(
        self,
    ):
        """Get default connections."""
        connections = []
        weather_classname = weather.Weather.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLib.TemperatureAmbient,
                weather_classname,
                weather.Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get simple hot water storage default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.simple_water_storage"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "SimpleHotWaterStorage")
        connections = []
        hws_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLib.TemperatureInputSecondary_SH,
                hws_classname,
                simple_water_storage.SimpleHotWaterStorage.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def get_default_connections_from_dhw_storage(
        self,
    ):
        """Get simple hot water storage default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.generic_hot_water_storage_modular"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "HotWaterStorage")
        connections = []
        dhw_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLib.TemperatureInputSecondary_DHW,
                dhw_classname,
                component_class.TemperatureMean,
            )
        )
        return connections

    def get_default_connections_from_simple_dhw_storage(
        self,
    ):
        """Get simple dhw water storage default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.simple_water_storage"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "SimpleDHWStorage")
        connections = []
        dhw_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLib.TemperatureInputSecondary_DHW,
                dhw_classname,
                component_class.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def write_to_report(self):
        """Write configuration to the report."""
        return self.config.get_string_dict()

    def i_save_state(self) -> None:
        """Save state."""
        self.previous_state = self.state.self_copy()
        # pass

    def i_restore_state(self) -> None:
        """Restore state."""
        self.state = self.previous_state.self_copy()
        # pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doubelcheck."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepare simulation."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the component."""

        # Load input values
        # on_off: float
        on_off_sh: float = stsv.get_input_value(self.on_off_switch_sh)
        t_in_primary = stsv.get_input_value(self.t_in_primary)
        t_in_secondary_sh = stsv.get_input_value(self.t_in_secondary_sh)
        t_amb = stsv.get_input_value(self.t_amb)
        time_on_heating = self.state.time_on_heating
        time_on_cooling = self.state.time_on_cooling
        time_off = self.state.time_off

        if (
            self.position_hot_water_storage_in_system
            in [
                PositionHotWaterStorageInSystemSetup.SERIE,
                PositionHotWaterStorageInSystemSetup.NO_STORAGE,
            ]
            or self.passive_cooling_with_brine
        ):
            set_temperature_hp_sh = stsv.get_input_value(self.set_temperature_hp_sh)

        if self.with_domestic_hot_water_preparation:
            on_off_dhw: float = stsv.get_input_value(self.on_off_switch_dhw)
            const_thermal_power_truefalse_dhw: bool = bool(stsv.get_input_value(self.const_thermal_power_truefalse_dhw))
            const_thermal_power_value_dhw = stsv.get_input_value(self.const_thermal_power_value_dhw)
            t_in_secondary_dhw = stsv.get_input_value(self.t_in_secondary_dhw)
        else:
            on_off_dhw = 0
            const_thermal_power_truefalse_dhw = False
            const_thermal_power_value_dhw = 0
            t_in_secondary_dhw = 0

        if on_off_dhw != 0:
            on_off = on_off_dhw
        else:
            on_off = on_off_sh

        # cycling means periodic turning on and off of the heat pump
        if self.cycling_mode is True:
            # Parameter
            time_on_min = self.minimum_running_time_in_seconds  # [s]
            time_off_min = self.minimum_idle_time_in_seconds
            on_off_previous = self.state.on_off_previous

            if time_on_min is None or time_off_min is None:
                raise ValueError(
                    """When the cycling mode is true, the minimum running time and minimum idle time of the heat pump
                    must be given an integer value."""
                )

            # Overwrite on_off to realize minimum time of or time off
            if on_off_previous == 1 and time_on_heating < time_on_min:
                if on_off == 0:
                    on_off = 1
            elif on_off_previous == 2 and time_on_heating < time_on_min:
                if on_off == 0:
                    on_off = 2
            elif on_off_previous == -1 and time_on_cooling < time_on_min:
                on_off = -1
            elif on_off_previous == 0 and time_off < time_off_min:
                on_off = 0

        # heat pump is turned on and off only according to heat pump controller
        elif self.cycling_mode is False:
            pass
        else:
            raise ValueError("Cycling mode of the advanced HPLib unknown.")

        if on_off == 1:  # Calculation for building heating
            if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL:
                self.heatpump.delta_t = 5
                results = self.get_cached_results_or_run_hplib_simulation(
                    t_in_primary=t_in_primary,
                    t_in_secondary=t_in_secondary_sh,
                    t_amb=t_amb,
                    mode=1,
                    operation_mode="heating_building",
                    p_th_min=self.minimum_thermal_output_power,
                )

                p_th_sh = results["P_th"]
                p_th_dhw = 0.0
                p_el_sh = results["P_el"]
                p_el_dhw = 0.0
                p_el_cooling = 0.0
                p_el_brine_pump = self.electrical_input_power_brine_pump_in_watt
                cop = results["COP"]
                eer = results["EER"]
                t_out_sh = results["T_out"]
                t_out_dhw = t_in_secondary_dhw if self.with_domestic_hot_water_preparation else 0.0
                m_dot_sh = results["m_dot"]
                m_dot_dhw = 0.0
                time_on_heating = time_on_heating + self.my_simulation_parameters.seconds_per_timestep
                time_on_cooling = 0
                time_off = 0

            else:
                m_dot_sh = self.m_dot_ref

                self.heatpump.delta_t = min(set_temperature_hp_sh - t_in_secondary_sh, 5)

                if self.heatpump.delta_t == 0:
                    self.heatpump.delta_t = 0.00000001

                results = self.get_cached_results_or_run_hplib_simulation(
                    t_in_primary=t_in_primary,
                    t_in_secondary=t_in_secondary_sh,
                    t_amb=t_amb,
                    mode=1,
                    operation_mode="heating_building",
                    p_th_min=self.minimum_thermal_output_power,
                )

                cop = results["COP"]
                eer = results["EER"]

                p_th_sh_theoretical = (
                    m_dot_sh
                    * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                    * self.heatpump.delta_t
                )
                p_el_sh_theoretical = p_th_sh_theoretical / cop

                if p_th_sh_theoretical <= self.minimum_thermal_output_power:
                    p_th_sh = self.minimum_thermal_output_power
                    p_el_sh = p_th_sh / cop
                else:
                    p_el_sh = p_el_sh_theoretical
                    p_th_sh = p_th_sh_theoretical

                p_el_sh = p_el_sh * (1 - np.exp(-time_on_heating / 360))  # time shifting while start of hp
                p_th_sh = p_th_sh * (1 - np.exp(-time_on_heating / 360))

                t_out_sh = t_in_secondary_sh + p_th_sh / (
                    m_dot_sh * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                )

                self.heatpump.delta_t = t_out_sh - t_in_secondary_sh

                t_out_dhw = t_in_secondary_dhw if self.with_domestic_hot_water_preparation else 0.0
                p_th_dhw = 0.0
                p_el_dhw = 0.0
                p_el_cooling = 0.0
                p_el_brine_pump = self.electrical_input_power_brine_pump_in_watt
                m_dot_dhw = 0.0
                time_on_heating = time_on_heating + self.my_simulation_parameters.seconds_per_timestep
                time_on_cooling = 0
                time_off = 0

        elif on_off == 2:  # Calculate outputs for dhw mode
            self.heatpump.delta_t = 5
            self.minimum_thermal_output_power = 0.0
            if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL:
                results = self.get_cached_results_or_run_hplib_simulation(
                    t_in_primary=t_in_primary,
                    t_in_secondary=t_in_secondary_dhw,
                    t_amb=t_amb,
                    mode=1,
                    operation_mode="heating_dhw",
                    p_th_min=self.minimum_thermal_output_power,
                )

                p_th_sh = 0.0
                p_el_sh = 0.0
                p_el_cooling = 0.0
                p_el_brine_pump = self.electrical_input_power_brine_pump_in_watt
                cop = results["COP"]
                eer = results["EER"]
                t_out_sh = t_in_secondary_sh
                t_out_dhw = results["T_out"]
                m_dot_sh = 0.0
                m_dot_dhw = results["m_dot"]
                if const_thermal_power_truefalse_dhw is True:  # True = constant thermal power output for dhw
                    p_th_dhw = const_thermal_power_value_dhw
                    p_el_dhw = p_th_dhw / cop
                if (
                    const_thermal_power_truefalse_dhw is False or const_thermal_power_truefalse_dhw == 0
                ):  # False = modulation
                    p_th_dhw = results["P_th"]
                    p_el_dhw = results["P_el"]
                time_on_heating = time_on_heating + self.my_simulation_parameters.seconds_per_timestep
                time_on_cooling = 0
                time_off = 0

            else:
                m_dot_dhw = self.m_dot_ref

                results = self.get_cached_results_or_run_hplib_simulation(
                    t_in_primary=t_in_primary,
                    t_in_secondary=t_in_secondary_dhw,
                    t_amb=t_amb,
                    mode=1,
                    operation_mode="heating_dhw",
                    p_th_min=self.minimum_thermal_output_power,
                )

                cop = results["COP"]
                eer = results["EER"]

                p_th_dhw_theoretical = (
                    m_dot_dhw
                    * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                    * self.heatpump.delta_t
                )
                p_el_dhw_theoretical = p_th_dhw_theoretical / cop

                if p_th_dhw_theoretical <= self.minimum_thermal_output_power:
                    p_th_dhw = self.minimum_thermal_output_power
                    p_el_dhw = p_th_dhw / cop
                else:
                    p_el_dhw = p_el_dhw_theoretical
                    p_th_dhw = p_th_dhw_theoretical

                p_el_dhw = p_el_dhw * (1 - np.exp(-time_on_heating / 360))
                p_th_dhw = p_th_dhw * (1 - np.exp(-time_on_heating / 360))

                t_out_dhw = t_in_secondary_dhw + p_th_dhw / (
                    m_dot_dhw * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                )

                t_out_sh = t_in_secondary_sh
                p_th_sh = 0.0
                p_el_sh = 0.0
                p_el_cooling = 0.0
                p_el_brine_pump = self.electrical_input_power_brine_pump_in_watt
                m_dot_sh = 0.0
                time_on_heating = time_on_heating + self.my_simulation_parameters.seconds_per_timestep
                time_on_cooling = 0
                time_off = 0

        elif on_off == -1:
            if self.passive_cooling_with_brine:
                # passiv cooling with brine
                cop = 0
                eer = 1

                m_dot_sh = self.m_dot_ref

                self.heatpump.delta_t = min(t_in_secondary_sh - set_temperature_hp_sh, 5)

                if self.heatpump.delta_t == 0:
                    self.heatpump.delta_t = 0.00000001

                p_th_sh = -(
                    m_dot_sh
                    * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                    * self.heatpump.delta_t
                )

                t_out_sh = t_in_secondary_sh + (
                    p_th_sh / (m_dot_sh * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius)
                )

                self.heatpump.delta_t = t_out_sh - t_in_secondary_sh

                p_th_dhw = 0.0
                p_el_dhw = 0.0
                p_el_sh = 0.0
                p_el_cooling = 0.0
                p_el_brine_pump = self.electrical_input_power_brine_pump_in_watt
                t_out_dhw = t_in_secondary_dhw if self.with_domestic_hot_water_preparation else 0.0
                m_dot_dhw = 0.0
                time_on_cooling = time_on_cooling + self.my_simulation_parameters.seconds_per_timestep
                time_on_heating = 0
                time_off = 0

            else:
                # Calulate outputs for cooling mode, aktive cooling with hp
                self.heatpump.delta_t = 5
                results = self.get_cached_results_or_run_hplib_simulation(
                    t_in_primary=t_in_primary,
                    t_in_secondary=t_in_secondary_sh,
                    t_amb=t_amb,
                    mode=2,
                    operation_mode="cooling_building",
                    p_th_min=self.minimum_thermal_output_power,
                )
                p_th_sh = results["P_th"]
                p_th_dhw = 0.0
                p_el_sh = 0.0
                p_el_dhw = 0.0
                p_el_cooling = results["P_el"]
                p_el_brine_pump = self.electrical_input_power_brine_pump_in_watt
                cop = results["COP"]
                eer = results["EER"]
                t_out_sh = results["T_out"]
                t_out_dhw = t_in_secondary_dhw if self.with_domestic_hot_water_preparation else 0.0
                m_dot_sh = results["m_dot"]
                m_dot_dhw = 0.0
                time_on_cooling = time_on_cooling + self.my_simulation_parameters.seconds_per_timestep
                time_on_heating = 0
                time_off = 0

        elif on_off == 0:
            # Calulate outputs for off mode
            p_th_sh = 0.0
            p_th_dhw = 0.0
            p_el_sh = 0.0
            p_el_dhw = 0.0
            p_el_cooling = 0.0
            p_el_brine_pump = 0.0
            # None values or nans will cause troubles in post processing, that is why there are not used here
            # cop = None
            # t_out = None
            cop = 0.0
            eer = 0.0
            t_out_sh = t_in_secondary_sh
            t_out_dhw = t_in_secondary_dhw if self.with_domestic_hot_water_preparation else 0.0
            m_dot_sh = 0.0
            m_dot_dhw = 0.0
            time_off = time_off + self.my_simulation_parameters.seconds_per_timestep
            time_on_heating = 0
            time_on_cooling = 0

        else:
            raise ValueError("Unknown mode for Advanced HPLib On_Off.")

        p_th_tot_in_watt = p_th_dhw + p_th_sh
        p_el_tot_in_watt = p_el_dhw + p_el_sh + p_el_cooling + p_el_brine_pump

        thermal_power_from_environment = p_th_tot_in_watt - p_el_tot_in_watt

        thermal_energy_hp_tot_in_watt_hour = (
            p_th_tot_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3600
        )
        thermal_energy_hp_sh_in_watt_hour = p_th_sh * self.my_simulation_parameters.seconds_per_timestep / 3600
        thermal_energy_hp_dhw_in_watt_hour = p_th_dhw * self.my_simulation_parameters.seconds_per_timestep / 3600

        electrical_energy_hp_tot_in_watt_hour = (
            p_el_tot_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3600
        )
        electrical_energy_hp_sh_in_watt_hour = p_el_sh * self.my_simulation_parameters.seconds_per_timestep / 3600
        electrical_energy_hp_dhw_in_watt_hour = p_el_dhw * self.my_simulation_parameters.seconds_per_timestep / 3600

        cumulative_hp_thermal_energy_tot_in_watt_hour = (
            self.state.cumulative_thermal_energy_tot_in_watt_hour + abs(thermal_energy_hp_tot_in_watt_hour)
        )
        cumulative_hp_thermal_energy_sh_in_watt_hour = (
            self.state.cumulative_thermal_energy_sh_in_watt_hour + abs(thermal_energy_hp_sh_in_watt_hour)
        )
        cumulative_hp_thermal_energy_dhw_in_watt_hour = (
            self.state.cumulative_thermal_energy_dhw_in_watt_hour + abs(thermal_energy_hp_dhw_in_watt_hour)
        )

        cumulative_hp_electrical_energy_tot_in_watt_hour = (
            self.state.cumulative_electrical_energy_tot_in_watt_hour + abs(electrical_energy_hp_tot_in_watt_hour)
        )
        cumulative_hp_electrical_energy_sh_in_watt_hour = (
            self.state.cumulative_electrical_energy_sh_in_watt_hour + abs(electrical_energy_hp_sh_in_watt_hour)
        )
        cumulative_hp_electrical_energy_dhw_in_watt_hour = (
            self.state.cumulative_electrical_energy_dhw_in_watt_hour + abs(electrical_energy_hp_dhw_in_watt_hour)
        )

        # Counter for switching in sh mode
        if self.state.on_off_previous != on_off and on_off == 1:
            counter_switch_sh = self.state.counter_switch_sh + 1
        else:
            counter_switch_sh = self.state.counter_switch_sh

        # Counter for switching in dhw mode
        if self.state.on_off_previous != on_off and on_off == 2:
            counter_switch_dhw = self.state.counter_switch_dhw + 1
        else:
            counter_switch_dhw = self.state.counter_switch_dhw

        # Counter for switching on Off Mode of HP
        if self.state.on_off_previous == 0 and on_off != 0:
            counter_onoff = self.state.counter_onoff + 1
        else:
            counter_onoff = self.state.counter_onoff

        if self.parameters["Group"].iloc[0] in (2, 3, 5, 6):
            # todo: variability of massflow
            if self.massflow_nominal_primary_side_in_kg_per_s is not None:
                m_dot_water_primary = self.massflow_nominal_primary_side_in_kg_per_s
            else:
                raise ValueError("Massflow on primary side has to be a value not none!")
            if self.specific_heat_capacity_of_primary_fluid is not None:
                specific_heat_capacity_of_primary_fluid = self.specific_heat_capacity_of_primary_fluid
            else:
                raise ValueError("specific heat capacity on primary side has to be a value not none!")

            if on_off == 0:
                temperature_difference_primary_side = 0.0
                m_dot_water_primary = 0.0
            else:
                temperature_difference_primary_side = thermal_power_from_environment / (
                    m_dot_water_primary * specific_heat_capacity_of_primary_fluid
                )

            t_out_primary = t_in_primary - temperature_difference_primary_side

            self.state.delta_t_primary_side = temperature_difference_primary_side

            stsv.set_output_value(self.m_dot_water_primary, m_dot_water_primary)
            stsv.set_output_value(self.temp_brine_primary_side_in, t_in_primary)
            stsv.set_output_value(self.temp_brine_primary_side_out, t_out_primary)
            stsv.set_output_value(self.temperature_difference_primary_side, temperature_difference_primary_side)

        # write values for output time series
        stsv.set_output_value(self.p_th_sh, p_th_sh)
        stsv.set_output_value(self.p_th_tot, p_th_tot_in_watt)
        stsv.set_output_value(self.p_el_sh, p_el_sh)
        stsv.set_output_value(self.p_el_cooling, p_el_cooling)
        stsv.set_output_value(self.p_el_tot, p_el_tot_in_watt)
        stsv.set_output_value(self.cop, cop)
        stsv.set_output_value(self.eer, eer)
        stsv.set_output_value(self.heatpump_state, on_off)
        stsv.set_output_value(self.t_in_sh, t_in_secondary_sh)
        stsv.set_output_value(self.t_out_sh, t_out_sh)
        stsv.set_output_value(self.m_dot_sh, m_dot_sh)
        stsv.set_output_value(self.time_on_heating, time_on_heating)
        stsv.set_output_value(self.time_on_cooling, time_on_cooling)
        stsv.set_output_value(self.time_off, time_off)
        stsv.set_output_value(self.thermal_power_from_environment, thermal_power_from_environment)
        stsv.set_output_value(self.thermal_energy_hp_tot_channel, thermal_energy_hp_tot_in_watt_hour)
        stsv.set_output_value(self.thermal_energy_hp_sh_channel, thermal_energy_hp_sh_in_watt_hour)
        stsv.set_output_value(self.electrical_energy_hp_tot_channel, electrical_energy_hp_tot_in_watt_hour)
        stsv.set_output_value(self.electrical_energy_hp_sh_channel, electrical_energy_hp_sh_in_watt_hour)
        stsv.set_output_value(
            self.cumulative_hp_thermal_energy_tot_channel, cumulative_hp_thermal_energy_tot_in_watt_hour
        )
        stsv.set_output_value(
            self.cumulative_hp_thermal_energy_sh_channel, cumulative_hp_thermal_energy_sh_in_watt_hour
        )
        stsv.set_output_value(
            self.cumulative_hp_electrical_energy_tot_channel, cumulative_hp_electrical_energy_tot_in_watt_hour
        )
        stsv.set_output_value(
            self.cumulative_hp_electrical_energy_sh_channel, cumulative_hp_electrical_energy_sh_in_watt_hour
        )
        stsv.set_output_value(self.counter_on_off_channel, counter_onoff)
        stsv.set_output_value(self.delta_t_hp_secondary_side_channel, self.heatpump.delta_t)

        if self.with_domestic_hot_water_preparation:
            stsv.set_output_value(self.p_th_dhw, p_th_dhw)
            stsv.set_output_value(self.p_el_dhw, p_el_dhw)
            stsv.set_output_value(self.t_in_dhw, t_in_secondary_dhw)
            stsv.set_output_value(self.t_out_dhw, t_out_dhw)
            stsv.set_output_value(self.m_dot_dhw, m_dot_dhw)
            stsv.set_output_value(self.thermal_energy_hp_dhw_channel, thermal_energy_hp_dhw_in_watt_hour)
            stsv.set_output_value(self.electrical_energy_hp_dhw_channel, electrical_energy_hp_dhw_in_watt_hour)
            stsv.set_output_value(
                self.cumulative_hp_thermal_energy_dhw_channel, cumulative_hp_thermal_energy_dhw_in_watt_hour
            )
            stsv.set_output_value(
                self.cumulative_hp_electrical_energy_dhw_channel, cumulative_hp_electrical_energy_dhw_in_watt_hour
            )
            stsv.set_output_value(self.counter_switch_dhw_channel, counter_switch_dhw)
            stsv.set_output_value(self.counter_switch_sh_channel, counter_switch_sh)

        # write values to state
        self.state.time_on_heating = time_on_heating
        self.state.time_on_cooling = time_on_cooling
        self.state.time_off = time_off
        self.state.on_off_previous = on_off
        self.state.cumulative_thermal_energy_tot_in_watt_hour = cumulative_hp_thermal_energy_tot_in_watt_hour
        self.state.cumulative_thermal_energy_sh_in_watt_hour = cumulative_hp_thermal_energy_sh_in_watt_hour
        self.state.cumulative_thermal_energy_dhw_in_watt_hour = cumulative_hp_thermal_energy_dhw_in_watt_hour
        self.state.cumulative_electrical_energy_tot_in_watt_hour = cumulative_hp_electrical_energy_tot_in_watt_hour
        self.state.cumulative_electrical_energy_sh_in_watt_hour = cumulative_hp_electrical_energy_sh_in_watt_hour
        self.state.cumulative_electrical_energy_dhw_in_watt_hour = cumulative_hp_electrical_energy_dhw_in_watt_hour
        self.state.counter_switch_sh = counter_switch_sh
        self.state.counter_switch_dhw = counter_switch_dhw
        self.state.counter_onoff = counter_onoff
        self.state.delta_t_secondary_side = self.heatpump.delta_t

    @staticmethod
    def get_cost_capex(
        config: MoreAdvancedHeatPumpHPLibConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        # set variables
        component_type = ComponentType.HEAT_PUMP
        kpi_tag = KpiTagEnumClass.HEATPUMP_SPACE_HEATING_AND_DOMESTIC_HOT_WATER
        unit = Units.KILOWATT
        size_of_energy_system = config.set_thermal_output_power_in_watt.value * 1e-3

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=simulation_parameters,
        component_type=component_type,
        unit=unit,
        size_of_energy_system=size_of_energy_system,
        config=config,
        kpi_tag=kpi_tag
        )
        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(config=config, capex_cost_data_class=capex_cost_data_class)

        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of maintenance costs.

        No electricity costs for components except for Electricity Meter,
        because part of electricity consumption is feed by PV
        """
        total_consumption_in_kwh: float
        sh_consumption_in_kwh: float
        dhw_consumption_in_kwh: float

        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.load_type == LoadTypes.ELECTRICITY
                and output.field_name == self.ElectricalInputPowerTotal
            ):
                total_consumption_in_kwh = round(
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )
            if (
                output.component_name == self.component_name
                and output.load_type == LoadTypes.ELECTRICITY
                and output.field_name == self.ElectricalInputPowerSH
            ):
                sh_consumption_in_kwh = round(
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )
            if (
                output.component_name == self.component_name
                and output.load_type == LoadTypes.ELECTRICITY
                and output.field_name == self.ElectricalInputPowerDHW
            ):
                dhw_consumption_in_kwh = round(
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )
        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year
        )
        co2_per_unit = emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh
        euro_per_unit = emissions_and_cost_factors.electricity_costs_in_euro_per_kwh
        co2_per_simulated_period_in_kg = total_consumption_in_kwh * co2_per_unit
        opex_energy_cost_per_simulated_period_in_euro = total_consumption_in_kwh * euro_per_unit

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_energy_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            total_consumption_in_kwh=total_consumption_in_kwh,
            consumption_for_domestic_hot_water_in_kwh=dhw_consumption_in_kwh,
            consumption_for_space_heating_in_kwh=sh_consumption_in_kwh,
            loadtype=LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING_AND_DOMESTIC_HOT_WATER,
        )

        return opex_cost_data_class

    def get_cached_results_or_run_hplib_simulation(
        self, t_in_primary: float, t_in_secondary: float, t_amb: float, mode: int, operation_mode: str, p_th_min: float
    ) -> Any:
        """Use caching of results of HPLib simulation."""

        # rounding of variable values
        t_in_primary = round(t_in_primary, 1)
        t_in_secondary = round(t_in_secondary, 1)
        t_amb = round(t_amb, 1)

        my_data_class = CalculationRequest(
            t_in_primary=t_in_primary,
            t_in_secondary=t_in_secondary,
            t_amb=t_amb,
            mode=mode,
            operation_mode=operation_mode,
        )
        my_json_key = my_data_class.get_key()
        my_hash_key = hashlib.sha256(my_json_key.encode("utf-8")).hexdigest()

        if my_hash_key in self.calculation_cache:
            results = self.calculation_cache[my_hash_key]
        else:
            results = self.heatpump.simulate(
                t_in_primary=t_in_primary, t_in_secondary=t_in_secondary, t_amb=t_amb, mode=mode, p_th_min=p_th_min
            )

            self.calculation_cache[my_hash_key] = results

        return results

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""

        output_heating_energy_in_kilowatt_hour: float = 0.0
        output_cooling_energy_in_kilowatt_hour: float = 0.0
        electrical_energy_for_heating_in_kilowatt_hour: float = 1.0
        electrical_energy_for_cooling_in_kilowatt_hour: float = 1.0
        number_of_heat_pump_cycles: Optional[float] = None
        seasonal_performance_factor: Optional[float] = None
        seasonal_energy_efficiency_ratio: Optional[float] = None
        total_electrical_energy_input_in_kilowatt_hour: Optional[float] = None
        heating_time_in_hours: Optional[float] = None
        cooling_time_in_hours: Optional[float] = None
        return_temperature_list_in_celsius: pd.Series = pd.Series([])
        flow_temperature_list_in_celsius: pd.Series = pd.Series([])
        dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour: Optional[float] = None
        dhw_heat_pump_heating_energy_output_in_kilowatt_hour: Optional[float] = None

        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                number_of_heat_pump_cycles = self.get_heatpump_cycles(
                    output=output, index=index, postprocessing_results=postprocessing_results
                )
                if output.field_name == self.ThermalOutputPowerSH and output.load_type == LoadTypes.HEATING:
                    # take only output values for heating
                    heating_output_power_values_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    # get energy from power
                    output_heating_energy_in_kilowatt_hour = KpiHelperClass.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=heating_output_power_values_in_watt,
                        timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                    )

                    # take only output values for cooling
                    cooling_output_power_values_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] < 0.0
                    ]
                    # for cooling enery use absolute value, not negative value
                    output_cooling_energy_in_kilowatt_hour = abs(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=cooling_output_power_values_in_watt,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                elif output.field_name == self.ThermalOutputPowerDHW:
                    dhw_heat_pump_heating_power_output_in_watt_series = postprocessing_results.iloc[:, index]
                    dhw_heat_pump_heating_energy_output_in_kilowatt_hour = (
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=dhw_heat_pump_heating_power_output_in_watt_series,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )

                elif output.field_name == self.ElectricalInputPowerSH:
                    # get electrical energie values for heating
                    electrical_energy_for_heating_in_kilowatt_hour = (
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=postprocessing_results.iloc[:, index],
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                elif output.field_name == self.ElectricalInputPowerDHW:
                    dhw_heat_pump_total_electricity_consumption_in_watt_series = postprocessing_results.iloc[:, index]
                    dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour = (
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=dhw_heat_pump_total_electricity_consumption_in_watt_series,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )

                elif output.field_name == self.ElectricalInputPowerForCooling:
                    # get electrical energie values for cooling
                    electrical_energy_for_cooling_in_kilowatt_hour = (
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=postprocessing_results.iloc[:, index],
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )

                elif output.field_name == self.TimeOnHeating:
                    heating_time_in_seconds = sum(postprocessing_results.iloc[:, index])
                    heating_time_in_hours = heating_time_in_seconds / 3600

                elif output.field_name == self.TimeOnCooling:
                    cooling_time_in_seconds = sum(postprocessing_results.iloc[:, index])
                    cooling_time_in_hours = cooling_time_in_seconds / 3600

                elif output.field_name == self.TemperatureOutputSH:
                    flow_temperature_list_in_celsius = postprocessing_results.iloc[:, index]

                elif output.field_name == self.TemperatureInputSH:
                    return_temperature_list_in_celsius = postprocessing_results.iloc[:, index]

        # get flow and return temperatures
        if not flow_temperature_list_in_celsius.empty and not return_temperature_list_in_celsius.empty:
            list_of_kpi_entries = self.get_flow_and_return_temperatures(
                flow_temperature_list_in_celsius=flow_temperature_list_in_celsius,
                return_temperature_list_in_celsius=return_temperature_list_in_celsius,
                list_of_kpi_entries=list_of_kpi_entries,
            )
        # calculate SPF
        if electrical_energy_for_heating_in_kilowatt_hour != 0.0:
            seasonal_performance_factor = (
                output_heating_energy_in_kilowatt_hour / electrical_energy_for_heating_in_kilowatt_hour
            )

        # calculate SEER
        if electrical_energy_for_cooling_in_kilowatt_hour != 0.0:
            seasonal_energy_efficiency_ratio = (
                output_cooling_energy_in_kilowatt_hour / electrical_energy_for_cooling_in_kilowatt_hour
            )

        # calculate total electricty input energy
        total_electrical_energy_input_in_kilowatt_hour = (
            electrical_energy_for_cooling_in_kilowatt_hour + electrical_energy_for_heating_in_kilowatt_hour
        )

        # make kpi entry
        dhw_heatpump_heating_energy_output_entry = KpiEntry(
            name="Heating output energy of DHW heat pump",
            unit="kWh",
            value=dhw_heat_pump_heating_energy_output_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_DOMESTIC_HOT_WATER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(dhw_heatpump_heating_energy_output_entry)

        dhw_heatpump_total_electricity_consumption_entry = KpiEntry(
            name="DHW heat pump total electricity consumption",
            unit="kWh",
            value=dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_DOMESTIC_HOT_WATER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(dhw_heatpump_total_electricity_consumption_entry)

        number_of_heat_pump_cycles_entry = KpiEntry(
            name="Number of SH heat pump cycles",
            unit="-",
            value=number_of_heat_pump_cycles,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(number_of_heat_pump_cycles_entry)

        seasonal_performance_factor_entry = KpiEntry(
            name="Seasonal performance factor of SH heat pump",
            unit="-",
            value=seasonal_performance_factor,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(seasonal_performance_factor_entry)

        seasonal_energy_efficiency_entry = KpiEntry(
            name="Seasonal energy efficiency ratio of SH heat pump",
            unit="-",
            value=seasonal_energy_efficiency_ratio,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(seasonal_energy_efficiency_entry)

        heating_output_energy_heatpump_entry = KpiEntry(
            name="Heating output energy of SH heat pump",
            unit="kWh",
            value=output_heating_energy_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(heating_output_energy_heatpump_entry)

        cooling_output_energy_heatpump_entry = KpiEntry(
            name="Cooling output energy of SH heat pump",
            unit="kWh",
            value=output_cooling_energy_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(cooling_output_energy_heatpump_entry)

        electrical_input_energy_for_heating_entry = KpiEntry(
            name="Electrical input energy for heating of SH heat pump",
            unit="kWh",
            value=electrical_energy_for_heating_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(electrical_input_energy_for_heating_entry)

        electrical_input_energy_for_cooling_entry = KpiEntry(
            name="Electrical input energy for cooling of SH heat pump",
            unit="kWh",
            value=electrical_energy_for_cooling_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(electrical_input_energy_for_cooling_entry)

        electrical_input_energy_total_entry = KpiEntry(
            name="Total electrical input energy of SH heat pump",
            unit="kWh",
            value=total_electrical_energy_input_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(electrical_input_energy_total_entry)

        heating_hours_entry = KpiEntry(
            name="Heating hours of SH heat pump",
            unit="h",
            value=heating_time_in_hours,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(heating_hours_entry)

        cooling_hours_entry = KpiEntry(
            name="Cooling hours of SH heat pump",
            unit="h",
            value=cooling_time_in_hours,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(cooling_hours_entry)

        return list_of_kpi_entries

    # make kpi entries and append to list
    def get_heatpump_cycles(self, output: Any, index: int, postprocessing_results: pd.DataFrame) -> float:
        """Get the number of cycles of the heat pump for the simulated period."""
        number_of_cycles = 0
        if output.field_name == self.TimeOff:
            for time_index, off_time in enumerate(postprocessing_results.iloc[:, index].values):
                try:
                    if off_time != 0 and postprocessing_results.iloc[:, index].values[time_index + 1] == 0:
                        number_of_cycles = number_of_cycles + 1

                except Exception:
                    pass

        return number_of_cycles

    def get_flow_and_return_temperatures(
        self,
        flow_temperature_list_in_celsius: pd.Series,
        return_temperature_list_in_celsius: pd.Series,
        list_of_kpi_entries: List[KpiEntry],
    ) -> List[KpiEntry]:
        """Get flow and return temperatures of heat pump."""
        # get mean, max and min values of flow and return temperatures
        mean_flow_temperature_in_celsius: Optional[float] = None
        mean_return_temperature_in_celsius: Optional[float] = None
        mean_temperature_difference_between_flow_and_return_in_celsius: Optional[float] = None
        min_flow_temperature_in_celsius: Optional[float] = None
        min_return_temperature_in_celsius: Optional[float] = None
        min_temperature_difference_between_flow_and_return_in_celsius: Optional[float] = None
        max_flow_temperature_in_celsius: Optional[float] = None
        max_return_temperature_in_celsius: Optional[float] = None
        max_temperature_difference_between_flow_and_return_in_celsius: Optional[float] = None

        temperature_diff_flow_and_return_in_celsius = (
            flow_temperature_list_in_celsius - return_temperature_list_in_celsius
        )
        (
            mean_temperature_difference_between_flow_and_return_in_celsius,
            max_temperature_difference_between_flow_and_return_in_celsius,
            min_temperature_difference_between_flow_and_return_in_celsius,
        ) = KpiHelperClass.calc_mean_max_min_value(list_or_pandas_series=temperature_diff_flow_and_return_in_celsius)

        (
            mean_flow_temperature_in_celsius,
            max_flow_temperature_in_celsius,
            min_flow_temperature_in_celsius,
        ) = KpiHelperClass.calc_mean_max_min_value(list_or_pandas_series=flow_temperature_list_in_celsius)

        (
            mean_return_temperature_in_celsius,
            max_return_temperature_in_celsius,
            min_return_temperature_in_celsius,
        ) = KpiHelperClass.calc_mean_max_min_value(list_or_pandas_series=return_temperature_list_in_celsius)

        # make kpi entries and append to list
        mean_flow_temperature_sh_entry = KpiEntry(
            name="Mean flow temperature of SH heat pump",
            unit="°C",
            value=mean_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(mean_flow_temperature_sh_entry)

        mean_return_temperature_sh_entry = KpiEntry(
            name="Mean return temperature of SH heat pump",
            unit="°C",
            value=mean_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(mean_return_temperature_sh_entry)

        mean_temperature_difference_sh_entry = KpiEntry(
            name="Mean temperature difference of SH heat pump",
            unit="°C",
            value=mean_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(mean_temperature_difference_sh_entry)

        max_flow_temperature_sh_entry = KpiEntry(
            name="Max flow temperature of SH heat pump",
            unit="°C",
            value=max_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(max_flow_temperature_sh_entry)

        max_return_temperature_sh_entry = KpiEntry(
            name="Max return temperature of SH heat pump",
            unit="°C",
            value=max_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(max_return_temperature_sh_entry)

        max_temperature_difference_sh_entry = KpiEntry(
            name="Max temperature difference of SH heat pump",
            unit="°C",
            value=max_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(max_temperature_difference_sh_entry)

        min_flow_temperature_sh_entry = KpiEntry(
            name="Min flow temperature of SH heat pump",
            unit="°C",
            value=min_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(min_flow_temperature_sh_entry)

        min_return_temperature_sh_entry = KpiEntry(
            name="Min return temperature of SH heat pump",
            unit="°C",
            value=min_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(min_return_temperature_sh_entry)

        min_temperature_difference_sh_entry = KpiEntry(
            name="Min temperature difference of SH heat pump",
            unit="°C",
            value=min_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(min_temperature_difference_sh_entry)
        return list_of_kpi_entries


@dataclass
class MoreAdvancedHeatPumpHPLibState:
    """MoreAdvancedHeatPumpHPLibState class."""

    time_on_heating: int
    time_off: int
    time_on_cooling: int
    on_off_previous: float
    cumulative_thermal_energy_tot_in_watt_hour: float
    cumulative_thermal_energy_sh_in_watt_hour: float
    cumulative_thermal_energy_dhw_in_watt_hour: float
    cumulative_electrical_energy_tot_in_watt_hour: float
    cumulative_electrical_energy_sh_in_watt_hour: float
    cumulative_electrical_energy_dhw_in_watt_hour: float
    counter_switch_sh: int
    counter_switch_dhw: int
    counter_onoff: int
    delta_t_secondary_side: float
    delta_t_primary_side: float

    def self_copy(
        self,
    ):
        """Copy the Heat Pump State."""
        return MoreAdvancedHeatPumpHPLibState(
            self.time_on_heating,
            self.time_off,
            self.time_on_cooling,
            self.on_off_previous,
            self.cumulative_thermal_energy_tot_in_watt_hour,
            self.cumulative_thermal_energy_sh_in_watt_hour,
            self.cumulative_thermal_energy_dhw_in_watt_hour,
            self.cumulative_electrical_energy_tot_in_watt_hour,
            self.cumulative_electrical_energy_sh_in_watt_hour,
            self.cumulative_electrical_energy_dhw_in_watt_hour,
            self.counter_switch_sh,
            self.counter_switch_dhw,
            self.counter_onoff,
            self.delta_t_secondary_side,
            self.delta_t_primary_side,
        )


@dataclass
class CalculationRequest(JSONWizard):
    """Class for caching HPLib parameters so that HPLib.simulate does not need to run so often."""

    t_in_primary: float
    t_in_secondary: float
    t_amb: float
    mode: int
    operation_mode: str

    def get_key(self):
        """Get key of class with important parameters."""

        return (
            str(self.t_in_primary)
            + " "
            + str(self.t_in_secondary)
            + " "
            + str(self.t_amb)
            + " "
            + str(self.mode)
            + " "
            + str(self.operation_mode)
        )


@dataclass_json
@dataclass
class MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig(ConfigBase):
    """HeatPump Controller Config Class for building heating."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return MoreAdvancedHeatPumpHPLibControllerSpaceHeating.get_full_classname()

    building_name: str
    name: str
    mode: int
    set_heating_threshold_outside_temperature_in_celsius: Optional[float]
    set_cooling_threshold_outside_temperature_in_celsius: Optional[float]
    upper_temperature_offset_for_state_conditions_in_celsius: float
    lower_temperature_offset_for_state_conditions_in_celsius: float
    heat_distribution_system_type: Any

    @classmethod
    def get_default_space_heating_controller_config(
        cls,
        heat_distribution_system_type: Any,
        name: str = "MoreAdvancedHeatPumpHPLibControllerSpaceHeating",
        building_name: str = "BUI1",
        upper_temperature_offset_for_state_conditions_in_celsius: float = 5.0,
        lower_temperature_offset_for_state_conditions_in_celsius: float = 5.0,
    ) -> "MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig":
        """Gets a default Generic Heat Pump Controller."""
        return MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig(
            building_name=building_name,
            name=name,
            mode=1,
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            set_cooling_threshold_outside_temperature_in_celsius=20.0,
            upper_temperature_offset_for_state_conditions_in_celsius=upper_temperature_offset_for_state_conditions_in_celsius,
            lower_temperature_offset_for_state_conditions_in_celsius=lower_temperature_offset_for_state_conditions_in_celsius,
            heat_distribution_system_type=heat_distribution_system_type,
        )


class MoreAdvancedHeatPumpHPLibControllerSpaceHeating(Component):
    """Heat Pump Controller for Space Heating.

    It takes data from other
    components and sends signal to the heat pump for
    activation or deactivation.
    On/off Switch with respect to water temperature from storage.

    Parameters
    ----------
    t_air_heating: float
        Minimum comfortable temperature for residents
    t_air_cooling: float
        Maximum comfortable temperature for residents
    offset: float
        Temperature offset to compensate the hysteresis
        correction for the building temperature change
    mode : int
        Mode index for operation type for this heat pump

    """

    # Inputs
    WaterTemperatureInput = "WaterTemperatureInput"
    HeatingFlowTemperatureFromHeatDistributionSystem = "HeatingFlowTemperatureFromHeatDistributionSystem"

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    SimpleHotWaterStorageTemperatureModifier = "SimpleHotWaterStorageTemperatureModifier"

    # Outputs
    State_SH = "State_SpaceHeating"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.heatpump_controller_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.heat_distribution_system_type = self.heatpump_controller_config.heat_distribution_system_type
        self.build(
            mode=self.heatpump_controller_config.mode,
            upper_temperature_offset_for_state_conditions_in_celsius=self.heatpump_controller_config.upper_temperature_offset_for_state_conditions_in_celsius,
            lower_temperature_offset_for_state_conditions_in_celsius=self.heatpump_controller_config.lower_temperature_offset_for_state_conditions_in_celsius,
        )

        self.water_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInput,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.heating_flow_temperature_from_heat_distribution_system_channel: ComponentInput = self.add_input(
            self.component_name,
            self.HeatingFlowTemperatureFromHeatDistributionSystem,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.daily_avg_outside_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.DailyAverageOutsideTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.simple_hot_water_storage_temperature_modifier_channel: ComponentInput = self.add_input(
            self.component_name,
            self.SimpleHotWaterStorageTemperatureModifier,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=False,
        )

        self.state_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.State_SH,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.State_SH} will follow.",
        )

        self.controller_heatpumpmode: Any
        self.previous_heatpump_mode: Any

        self.add_default_connections(self.get_default_connections_from_heat_distribution_controller())
        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())

    def get_default_connections_from_heat_distribution_controller(
        self,
    ):
        """Get default connections."""
        connections = []
        hdsc_classname = heat_distribution_system.HeatDistributionController.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.HeatingFlowTemperatureFromHeatDistributionSystem,
                hdsc_classname,
                heat_distribution_system.HeatDistributionController.HeatingFlowTemperature,
            )
        )
        return connections

    def get_default_connections_from_weather(
        self,
    ):
        """Get default connections."""
        connections = []
        weather_classname = weather.Weather.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.DailyAverageOutsideTemperature,
                weather_classname,
                weather.Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get simple hot water storage default connections."""
        connections = []
        hws_classname = simple_water_storage.SimpleHotWaterStorage.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.WaterTemperatureInput,
                hws_classname,
                simple_water_storage.SimpleHotWaterStorage.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def build(
        self,
        mode: float,
        upper_temperature_offset_for_state_conditions_in_celsius: float,
        lower_temperature_offset_for_state_conditions_in_celsius: float,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_heatpumpmode = "off"
        self.previous_heatpump_mode = self.controller_heatpumpmode

        # Configuration
        self.mode = mode
        self.upper_temperature_offset_for_state_conditions_in_celsius = (
            upper_temperature_offset_for_state_conditions_in_celsius
        )
        self.lower_temperature_offset_for_state_conditions_in_celsius = (
            lower_temperature_offset_for_state_conditions_in_celsius
        )

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_heatpump_mode = self.controller_heatpumpmode

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_heatpumpmode = self.previous_heatpump_mode

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        return self.heatpump_controller_config.get_string_dict()

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the heat pump comtroller."""

        if force_convergence:
            pass
        else:
            # Retrieves inputs

            water_temperature_input_in_celsius = stsv.get_input_value(self.water_temperature_input_channel)

            heating_flow_temperature_from_heat_distribution_system = stsv.get_input_value(
                self.heating_flow_temperature_from_heat_distribution_system_channel
            )

            daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
                self.daily_avg_outside_temperature_input_channel
            )

            storage_temperature_modifier = stsv.get_input_value(
                self.simple_hot_water_storage_temperature_modifier_channel
            )

            # turning heat pump off when the average daily outside temperature is above a certain threshold (if threshold is set in the config)
            summer_heating_mode = self.summer_heating_condition(
                daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                set_heating_threshold_temperature_in_celsius=self.heatpump_controller_config.set_heating_threshold_outside_temperature_in_celsius,
            )

            # mode 1 is on/off controller
            if self.mode == 1:
                self.conditions_on_off(
                    water_temperature_input_in_celsius=water_temperature_input_in_celsius,
                    set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                    summer_heating_mode=summer_heating_mode,
                    storage_temperature_modifier=storage_temperature_modifier,
                    upper_temperature_offset_for_state_conditions_in_celsius=self.upper_temperature_offset_for_state_conditions_in_celsius,
                    lower_temperature_offset_for_state_conditions_in_celsius=self.lower_temperature_offset_for_state_conditions_in_celsius,
                )

            # mode 2 is regulated controller (meaning heating, cooling, off). this is only possible if heating system is floor heating
            elif self.mode == 2 and self.heat_distribution_system_type == HeatDistributionSystemType.FLOORHEATING:
                # turning heat pump cooling mode off when the average daily outside temperature is below a certain threshold
                summer_cooling_mode = self.summer_cooling_condition(
                    daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                    set_cooling_threshold_temperature_in_celsius=self.heatpump_controller_config.set_cooling_threshold_outside_temperature_in_celsius,
                )
                self.conditions_heating_cooling_off(
                    water_temperature_input_in_celsius=water_temperature_input_in_celsius,
                    set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                    summer_heating_mode=summer_heating_mode,
                    summer_cooling_mode=summer_cooling_mode,
                    storage_temperature_modifier=storage_temperature_modifier,
                    upper_temperature_offset_for_state_conditions_in_celsius=self.upper_temperature_offset_for_state_conditions_in_celsius,
                    lower_temperature_offset_for_state_conditions_in_celsius=self.lower_temperature_offset_for_state_conditions_in_celsius,
                )

            else:
                raise ValueError(
                    "Either the Advanced HP Lib Controller Mode is neither 1 nor 2,"
                    "or the heating system is not floor heating which is the condition for cooling (mode 2)."
                )

            if self.controller_heatpumpmode == "heating":
                state = 1
            elif self.controller_heatpumpmode == "cooling":
                state = -1
            elif self.controller_heatpumpmode == "off":
                state = 0
            else:
                raise ValueError("Advanced HP Lib Controller State unknown.")

            stsv.set_output_value(self.state_channel, state)

    def conditions_on_off(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
        summer_heating_mode: str,
        storage_temperature_modifier: float,
        upper_temperature_offset_for_state_conditions_in_celsius: float,
        lower_temperature_offset_for_state_conditions_in_celsius: float,
    ) -> None:
        """Set conditions for the heat pump controller mode."""

        if self.controller_heatpumpmode == "heating":
            if (
                water_temperature_input_in_celsius
                > (
                    set_heating_flow_temperature_in_celsius
                    # + 0.5
                    + upper_temperature_offset_for_state_conditions_in_celsius
                    + storage_temperature_modifier
                )
                or summer_heating_mode == "off"
            ):  # + 1:
                self.controller_heatpumpmode = "off"
                return

        elif self.controller_heatpumpmode == "off":
            # heat pump is only turned on if the water temperature is below the flow temperature
            # and if the avg daily outside temperature is cold enough (summer mode on)
            if (
                water_temperature_input_in_celsius
                < (
                    set_heating_flow_temperature_in_celsius
                    # - 1.0
                    - lower_temperature_offset_for_state_conditions_in_celsius
                    + storage_temperature_modifier
                )
                and summer_heating_mode == "on"
            ):  # - 1:
                self.controller_heatpumpmode = "heating"
                return

        else:
            raise ValueError("unknown mode")

    def conditions_heating_cooling_off(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
        summer_heating_mode: str,
        summer_cooling_mode: str,
        storage_temperature_modifier: float,
        upper_temperature_offset_for_state_conditions_in_celsius: float,
        lower_temperature_offset_for_state_conditions_in_celsius: float,
    ) -> None:
        """Set conditions for the heat pump controller mode according to the flow temperature."""
        # Todo: storage temperature modifier is only working for heating so far. Implement for cooling similar
        heating_set_temperature = set_heating_flow_temperature_in_celsius
        cooling_set_temperature = set_heating_flow_temperature_in_celsius

        if self.controller_heatpumpmode == "heating":
            if (
                water_temperature_input_in_celsius
                >= heating_set_temperature
                + upper_temperature_offset_for_state_conditions_in_celsius
                + storage_temperature_modifier  # Todo: Check if storage_temperature_modifier is neccessary here
                or summer_heating_mode == "off"
            ):
                self.controller_heatpumpmode = "off"
                return
        elif self.controller_heatpumpmode == "cooling":
            if (
                water_temperature_input_in_celsius
                <= cooling_set_temperature - lower_temperature_offset_for_state_conditions_in_celsius
                or summer_cooling_mode == "off"
            ):
                self.controller_heatpumpmode = "off"
                return

        elif self.controller_heatpumpmode == "off":
            # heat pump is only turned on if the water temperature is below the flow temperature
            # and if the avg daily outside temperature is cold enough (summer heating mode on)
            if (
                water_temperature_input_in_celsius
                < (
                    heating_set_temperature
                    - lower_temperature_offset_for_state_conditions_in_celsius
                    + storage_temperature_modifier
                )
                and summer_heating_mode == "on"
            ):
                self.controller_heatpumpmode = "heating"
                return

            # heat pump is only turned on for cooling if the water temperature is above a certain flow temperature
            # and if the avg daily outside temperature is warm enough (summer cooling mode on)
            if (
                water_temperature_input_in_celsius
                > (cooling_set_temperature + upper_temperature_offset_for_state_conditions_in_celsius)
                and summer_cooling_mode == "on"
            ):
                self.controller_heatpumpmode = "cooling"
                return

        else:
            raise ValueError("unknown mode")

    def summer_heating_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_heating_threshold_temperature_in_celsius: Optional[float],
    ) -> str:
        """Set conditions for the heat pump."""

        # if no heating threshold is set, the heat pump is always on
        if set_heating_threshold_temperature_in_celsius is None:
            heating_mode = "on"

        # it is too hot for heating
        elif daily_average_outside_temperature_in_celsius > set_heating_threshold_temperature_in_celsius:
            heating_mode = "off"

        # it is cold enough for heating
        elif daily_average_outside_temperature_in_celsius < set_heating_threshold_temperature_in_celsius:
            heating_mode = "on"

        else:
            raise ValueError(
                f"daily average temperature {daily_average_outside_temperature_in_celsius}°C"
                f"or heating threshold temperature {set_heating_threshold_temperature_in_celsius}°C is not acceptable."
            )
        return heating_mode

    def summer_cooling_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_cooling_threshold_temperature_in_celsius: Optional[float],
    ) -> str:
        """Set conditions for the heat pump."""

        # if no cooling threshold is set, cooling is always possible no matter what daily outside temperature
        if set_cooling_threshold_temperature_in_celsius is None:
            cooling_mode = "on"

        # it is hot enough for cooling
        elif daily_average_outside_temperature_in_celsius > set_cooling_threshold_temperature_in_celsius:
            cooling_mode = "on"

        # it is too cold for cooling
        elif daily_average_outside_temperature_in_celsius < set_cooling_threshold_temperature_in_celsius:
            cooling_mode = "off"

        else:
            raise ValueError(
                f"daily average temperature {daily_average_outside_temperature_in_celsius}°C"
                f"or cooling threshold temperature {set_cooling_threshold_temperature_in_celsius}°C is not acceptable."
            )

        return cooling_mode

    @staticmethod
    def get_cost_capex(
        config: MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_cost_opex(
        self, all_outputs: List, postprocessing_results: pd.DataFrame
    ) -> OpexCostDataClass:  # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for Heat Distribution System."""
        opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()

        return opex_cost_data_class

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []


# implement a HPLib controller l1 for dhw storage (tww)
@dataclass_json
@dataclass
class MoreAdvancedHeatPumpHPLibControllerDHWConfig(ConfigBase):
    """HeatPump Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return MoreAdvancedHeatPumpHPLibControllerDHW.get_full_classname()

    building_name: str
    name: str
    #: lower set temperature of DHW Storage, given in °C
    t_min_dhw_storage_in_celsius: float
    #: upper set temperature of DHW Storage, given in °C
    t_max_dhw_storage_in_celsius: float
    #: set thermal power delivered for dhw on constant value --> max. Value of heatpump
    thermalpower_dhw_is_constant: bool
    #: max. Power of Heatpump for not modulation dhw production
    p_th_max_dhw_in_watt: float

    @classmethod
    def get_default_dhw_controller_config(
        cls,
        name: str = "HeatPumpControllerDHW",
        building_name: str = "BUI1",
    ) -> "MoreAdvancedHeatPumpHPLibControllerDHWConfig":
        """Gets a default Generic Heat Pump Controller."""
        return MoreAdvancedHeatPumpHPLibControllerDHWConfig(
            building_name=building_name,
            name=name,
            t_min_dhw_storage_in_celsius=40.0,
            t_max_dhw_storage_in_celsius=60.0,
            thermalpower_dhw_is_constant=False,  # false: modulation, true: constant power for dhw
            p_th_max_dhw_in_watt=5000.0,  # only if true
        )


class MoreAdvancedHeatPumpHPLibControllerDHW(Component):
    """Heat Pump Controller for DHW.

    It takes data from DHW Storage --> generic hot water storage modular
    sends signal to the heat pump for activation or deactivation.

    """

    # Inputs
    WaterTemperatureInputFromDHWStorage = "WaterTemperatureInputFromDHWStorage"
    DWHStorageTemperatureModifier = "StorageTemperatureModifier"

    # Outputs
    State_dhw = "StateDHW"
    ThermalPower_dhw_is_constant = "ThermalPowerDHWConst"  # if heatpump has fix power for dhw
    Value_thermalpower_dhw_is_constant = "ThermalPowerHPForDHWConst"  # if heatpump has fix power for dhw

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: MoreAdvancedHeatPumpHPLibControllerDHWConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.heatpump_controller_dhw_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.config: MoreAdvancedHeatPumpHPLibControllerDHWConfig = config

        self.state_dhw: int
        self.previous_state_dhw: int
        self.water_temperature_input_from_dhw_storage_in_celsius_previous: float
        self.water_temperature_input_from_dhw_storage_in_celsius: float
        self.thermalpower_dhw_is_constant: bool
        self.p_th_max_dhw: float

        self.build()

        self.water_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromDHWStorage,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.storage_temperature_modifier_channel: ComponentInput = self.add_input(
            self.component_name,
            self.DWHStorageTemperatureModifier,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=False,
        )

        self.state_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.State_dhw,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.State_dhw} will follow.",
        )

        self.thermalpower_dhw_is_constant_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPower_dhw_is_constant,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.ThermalPower_dhw_is_constant} will follow.",
        )

        self.thermalpower_dhw_is_constant_value_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.Value_thermalpower_dhw_is_constant,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.Value_thermalpower_dhw_is_constant} will follow.",
        )

        self.add_default_connections(self.get_default_connections_from_dhw_storage())
        self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())

    def get_default_connections_from_dhw_storage(
        self,
    ):
        """Get simple hot water storage default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.generic_hot_water_storage_modular"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "HotWaterStorage")
        connections = []
        dhw_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerDHW.WaterTemperatureInputFromDHWStorage,
                dhw_classname,
                component_class.TemperatureMean,
            )
        )
        return connections

    def get_default_connections_from_simple_dhw_storage(
        self,
    ):
        """Get simple dhw water storage default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.simple_water_storage"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "SimpleDHWStorage")
        connections = []
        dhw_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerDHW.WaterTemperatureInputFromDHWStorage,
                dhw_classname,
                component_class.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def build(
        self,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """

        self.state_dhw = 0
        self.water_temperature_input_from_dhw_storage_in_celsius = 40.0
        self.thermalpower_dhw_is_constant = self.config.thermalpower_dhw_is_constant
        self.p_th_max_dhw = self.config.p_th_max_dhw_in_watt

        if self.thermalpower_dhw_is_constant is True:
            print("INFO: DHW Power is constant with " + str(self.p_th_max_dhw) + " Watt.")
        elif self.thermalpower_dhw_is_constant is False:
            print("INFO: DHW Power is modulating")
            self.p_th_max_dhw = 0.0

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_state_dhw = self.state_dhw
        self.water_temperature_input_from_dhw_storage_in_celsius_previous = (
            self.water_temperature_input_from_dhw_storage_in_celsius
        )

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.state_dhw = self.previous_state_dhw
        self.water_temperature_input_from_dhw_storage_in_celsius = (
            self.water_temperature_input_from_dhw_storage_in_celsius_previous
        )

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        return self.heatpump_controller_dhw_config.get_string_dict()

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the heat pump controller for dhw."""

        if force_convergence:
            # self.state_dhw = self.previous_state_dhw
            pass
        else:
            self.water_temperature_input_from_dhw_storage_in_celsius = stsv.get_input_value(
                self.water_temperature_input_channel
            )
            if self.water_temperature_input_from_dhw_storage_in_celsius == 0:
                # for avoiding errors: sometimes timestep output of dhw storage sends zero as input, so hp will switch to dhw, even this is not necessary
                self.water_temperature_input_from_dhw_storage_in_celsius = (
                    self.water_temperature_input_from_dhw_storage_in_celsius_previous
                )

            temperature_modifier = stsv.get_input_value(self.storage_temperature_modifier_channel)

            t_min_dhw_storage_in_celsius = self.config.t_min_dhw_storage_in_celsius
            t_max_dhw_storage_in_celsius = self.config.t_max_dhw_storage_in_celsius

            if self.water_temperature_input_from_dhw_storage_in_celsius < t_min_dhw_storage_in_celsius:  # on
                self.state_dhw = 2

            if (
                self.water_temperature_input_from_dhw_storage_in_celsius
                > t_max_dhw_storage_in_celsius + temperature_modifier
            ):  # off
                self.state_dhw = 0

            if (
                temperature_modifier > 0
                and self.water_temperature_input_from_dhw_storage_in_celsius < t_max_dhw_storage_in_celsius
            ):  # aktiviren wenn strom überschuss
                self.state_dhw = 2

        self.previous_state_dhw = self.state_dhw
        self.water_temperature_input_from_dhw_storage_in_celsius_previous = (
            self.water_temperature_input_from_dhw_storage_in_celsius
        )

        stsv.set_output_value(self.state_dhw_channel, self.state_dhw)
        stsv.set_output_value(
            self.thermalpower_dhw_is_constant_channel,
            self.thermalpower_dhw_is_constant,
        )

        if self.thermalpower_dhw_is_constant is True:
            stsv.set_output_value(self.thermalpower_dhw_is_constant_value_channel, self.p_th_max_dhw)

    @staticmethod
    def get_cost_capex(
        config: MoreAdvancedHeatPumpHPLibControllerDHWConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_cost_opex(
        self, all_outputs: List, postprocessing_results: pd.DataFrame
    ) -> OpexCostDataClass:  # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for Heat Distribution System."""
        opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()

        return opex_cost_data_class

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
