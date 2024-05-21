"""More advanced heat pump module.

See library on https://github.com/FZJ-IEK3-VSA/hplib/tree/main/hplib

two controller: one dhw controller and one building heating controller

priority on dhw, if there is a demand from both in one timestep

preparation on district heating for water/water heatpumps

"""
import hashlib

# clean
import importlib
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Dict

import pandas as pd
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
)
from hisim.components import weather, simple_hot_water_storage, heat_distribution_system
from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.loadtypes import LoadTypes, Units, InandOutputType, OutputPostprocessingRules
from hisim.units import (
    Quantity,
    Watt,
    Celsius,
    Seconds,
    Kilogram,
    Euro,
    Years,
    KilowattHour,
)
from hisim.components.configuration import PhysicsConfig

from hisim.simulationparameters import SimulationParameters

__authors__ = "Jonas Hoppe"
__copyright__ = ""
__credits__ = ["Jonas Hoppe"]
__license__ = "-"
__version__ = ""
__maintainer__ = ""
__status__ = ""


@dataclass_json
@dataclass
class HeatPumpHplibWithTwoOutputsConfig(ConfigBase):

    """HeatPumpHplibWithTwoOutputsConfig."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatPumpHplibWithTwoOutputs.get_full_classname()

    name: str
    model: str
    heat_source: str
    group_id: int
    heating_reference_temperature_in_celsius: Quantity[float, Celsius]  # before t_in
    flow_temperature_in_celsius: Quantity[float, Celsius]  # before t_out_val
    set_thermal_output_power_in_watt: Quantity[float, Watt]  # before p_th_set
    cycling_mode: bool
    minimum_running_time_in_seconds: Optional[Quantity[int, Seconds]]
    minimum_idle_time_in_seconds: Optional[Quantity[int, Seconds]]
    temperature_difference_primary_side: float
    with_hot_water_storage: bool
    with_domestic_hot_water_preparation: bool
    #: CO2 footprint of investment in kg
    co2_footprint: Quantity[float, Kilogram]
    #: cost for investment in Euro
    cost: Quantity[float, Euro]
    #: lifetime in years
    lifetime: Quantity[float, Years]
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float
    #: consumption of the heatpump in kWh
    consumption: Quantity[float, KilowattHour]

    @classmethod
    def get_default_generic_advanced_hp_lib(
        cls,
        set_thermal_output_power_in_watt: Quantity[float, Watt] = Quantity(8000, Watt),
        heating_reference_temperature_in_celsius: Quantity[float, Celsius] = Quantity(-7.0, Celsius),
    ) -> "HeatPumpHplibWithTwoOutputsConfig":
        """Gets a default HPLib Heat Pump.

        see default values for air/water hp on:
        https://github.com/FZJ-IEK3-VSA/hplib/blob/main/hplib/hplib.py l.135 "fit_p_th_ref.
        """
        return HeatPumpHplibWithTwoOutputsConfig(
            name="MoreAdvancedHeatPumpHPLib",
            model="Generic",
            heat_source="air",
            group_id=1,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            flow_temperature_in_celsius=Quantity(52, Celsius),
            set_thermal_output_power_in_watt=set_thermal_output_power_in_watt,
            cycling_mode=True,
            minimum_running_time_in_seconds=Quantity(3600, Seconds),
            minimum_idle_time_in_seconds=Quantity(3600, Seconds),
            temperature_difference_primary_side=2,
            with_hot_water_storage=True,
            with_domestic_hot_water_preparation=False,
            # value from emission_factors_and_costs_devices.csv
            co2_footprint=Quantity(
                set_thermal_output_power_in_watt.value * 1e-3 * 165.84, Kilogram
            ),
            # value from emission_factors_and_costs_devices.csv
            cost=Quantity(
                set_thermal_output_power_in_watt.value * 1e-3 * 1513.74, Euro
            ),
            lifetime=Quantity(
                10, Years
            ),  # value from emission_factors_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.025,  # source:  VDI2067-1
            consumption=Quantity(0, KilowattHour),
        )

    @classmethod
    def get_scaled_advanced_hp_lib(
        cls,
        heating_load_of_building_in_watt: Quantity[float, Watt],
        heating_reference_temperature_in_celsius: Quantity[float, Celsius] = Quantity(-7.0, Celsius),
    ) -> "HeatPumpHplibWithTwoOutputsConfig":
        """Gets a default heat pump with scaling according to heating load of the building."""

        set_thermal_output_power_in_watt: Quantity[float, Watt] = heating_load_of_building_in_watt

        return HeatPumpHplibWithTwoOutputsConfig(
            name="MoreAdvancedHeatPumpHPLib",
            model="Generic",
            heat_source="air",
            group_id=1,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            flow_temperature_in_celsius=Quantity(52, Celsius),
            set_thermal_output_power_in_watt=set_thermal_output_power_in_watt,
            cycling_mode=True,
            minimum_running_time_in_seconds=Quantity(3600, Seconds),
            minimum_idle_time_in_seconds=Quantity(3600, Seconds),
            temperature_difference_primary_side=2,
            with_hot_water_storage=True,
            with_domestic_hot_water_preparation=False,
            # value from emission_factros_and_costs_devices.csv
            co2_footprint=Quantity(
                set_thermal_output_power_in_watt.value * 1e-3 * 165.84, Kilogram
            ),
            # value from emission_factros_and_costs_devices.csv
            cost=Quantity(
                set_thermal_output_power_in_watt.value * 1e-3 * 1513.74, Euro
            ),
            # value from emission_factros_and_costs_devices.csv
            lifetime=Quantity(10, Years),
            maintenance_cost_as_percentage_of_investment=0.025,  # source:  VDI2067-1
            consumption=Quantity(0, KilowattHour),
        )


class HeatPumpHplibWithTwoOutputs(Component):

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
    MdotWaterPrimary = "MassflowPrimary"  # kg/s --- used for Water/water HP
    WaterTemperaturePrimaryIn = "TemperaturePrimaryIn"  # °C
    WaterTemperaturePrimaryOut = "TemperaturePrimaryOut"  # °C
    CounterSwitchToSH = "CounterSwitchToSH"  # Counter of switching to SH != onOff Switch!
    CounterSwitchToDHW = "CounterSwitchToDHW"  # Counter of switching to DHW != onOff Switch!
    CounterOnOff = "CounterOnOff"  # Counter of starting the hp

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatPumpHplibWithTwoOutputsConfig,
        my_display_config: DisplayConfig = DisplayConfig(display_in_webtool=True),
    ):
        """Loads the parameters of the specified heat pump."""

        super().__init__(
            name=config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        # caching for hplib simulation
        self.calculation_cache: Dict = {}

        self.model = config.model

        self.group_id = config.group_id

        self.t_in = int(config.heating_reference_temperature_in_celsius.value)

        self.t_out_val = int(config.flow_temperature_in_celsius.value)

        self.p_th_set = int(config.set_thermal_output_power_in_watt.value)

        self.cycling_mode = config.cycling_mode

        self.with_hot_water_storage = config.with_hot_water_storage

        self.with_domestic_hot_water_preparation = config.with_domestic_hot_water_preparation

        self.heat_source = config.heat_source

        self.temperature_difference_primary_side = config.temperature_difference_primary_side

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

        postprocessing_flag = [
            InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
            OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
        ]

        # Component has states
        self.state = HeatPumpWithTwoOutputsState(
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
        )
        self.previous_state = self.state.self_copy()

        # Load parameters from heat pump database
        self.parameters = hpl.get_parameters(self.model, self.group_id, self.t_in, self.t_out_val, self.p_th_set)

        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = (
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        )

        # protect erros for Water/Water Heatpumps
        if self.parameters["Group"].iloc[0] == 1.0 or self.parameters["Group"].iloc[0] == 4.0:
            if self.heat_source != "air":
                raise KeyError("HP modell does not fit to heat source in config!")
        if self.parameters["Group"].iloc[0] == 2.0 or self.parameters["Group"].iloc[0] == 5.0:
            if self.heat_source != "brine":
                raise KeyError("HP modell does not fit to heat source in config!")
        if self.parameters["Group"].iloc[0] == 3.0 or self.parameters["Group"].iloc[0] == 6.0:
            if self.heat_source != "water":
                raise KeyError("HP modell does not fit to heat source in config!")

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
            postprocessing_flag=[
                InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
            ],
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
            postprocessing_flag=postprocessing_flag,
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
            self.m_dot_water_primary_dhnet: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.MdotWaterPrimary,
                load_type=LoadTypes.VOLUME,
                unit=Units.KG_PER_SEC,
                output_description="Massflow of Water from District Heating Net",
            )
            self.temp_water_primary_side_in: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.WaterTemperaturePrimaryIn,
                load_type=LoadTypes.TEMPERATURE,
                unit=Units.CELSIUS,
                output_description="Temperature of Water from District Heating Net In HX",
            )
            self.temp_water_primary_side_out: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.WaterTemperaturePrimaryOut,
                load_type=LoadTypes.TEMPERATURE,
                unit=Units.CELSIUS,
                output_description="Temperature of Water to District Heating Net Out HX",
            )

        self.add_default_connections(self.get_default_connections_from_heat_pump_controller_space_heating())
        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())

        if self.with_domestic_hot_water_preparation:
            self.add_default_connections(self.get_default_connections_from_heat_pump_controller_dhw())
            self.add_default_connections(self.get_default_connections_from_dhw_storage())

    def get_default_connections_from_heat_pump_controller_space_heating(
        self,
    ):
        """Get default connections."""
        connections = []
        hpc_classname = HeatPumpHplibControllerSpaceHeating.get_classname()
        connections.append(
            ComponentConnection(
                HeatPumpHplibWithTwoOutputs.OnOffSwitchSH,
                hpc_classname,
                HeatPumpHplibControllerSpaceHeating.State_SH,
            )
        )
        return connections

    def get_default_connections_from_heat_pump_controller_dhw(
        self,
    ):
        """Get default connections."""
        connections = []
        hpc_dhw_classname = HeatPumpHplibControllerDHW.get_classname()
        connections.append(
            ComponentConnection(
                HeatPumpHplibWithTwoOutputs.OnOffSwitchDHW,
                hpc_dhw_classname,
                HeatPumpHplibControllerDHW.State_dhw,
            )
        )
        connections.append(
            ComponentConnection(
                HeatPumpHplibWithTwoOutputs.ThermalPowerIsConstantForDHW,
                hpc_dhw_classname,
                HeatPumpHplibControllerDHW.ThermalPower_dhw_is_constant,
            )
        )
        connections.append(
            ComponentConnection(
                HeatPumpHplibWithTwoOutputs.MaxThermalPowerValueForDHW,
                hpc_dhw_classname,
                HeatPumpHplibControllerDHW.Value_thermalpower_dhw_is_constant,
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
                HeatPumpHplibWithTwoOutputs.TemperatureAmbient,
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
        component_module_name = "hisim.components.simple_hot_water_storage"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "SimpleHotWaterStorage")
        connections = []
        hws_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                HeatPumpHplibWithTwoOutputs.TemperatureInputSecondary_SH,
                hws_classname,
                simple_hot_water_storage.SimpleHotWaterStorage.WaterTemperatureToHeatGenerator,
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
                HeatPumpHplibWithTwoOutputs.TemperatureInputSecondary_DHW,
                dhw_classname,
                component_class.TemperatureMean,
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

        if self.with_domestic_hot_water_preparation:
            on_off_dhw: float = stsv.get_input_value(self.on_off_switch_dhw)
            const_thermal_power_truefalse_dhw: bool = bool(stsv.get_input_value(self.const_thermal_power_truefalse_dhw))
            const_thermal_power_value_dhw = stsv.get_input_value(self.const_thermal_power_value_dhw)
            t_in_secondary_dhw = stsv.get_input_value(self.t_in_secondary_dhw)

        if self.with_domestic_hot_water_preparation and on_off_dhw != 0:
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
                on_off = 1
            elif on_off_previous == 2 and time_on_heating < time_on_min:
                on_off = 2
            elif on_off_previous == -1 and time_on_cooling < time_on_min:
                on_off = -1
            elif on_off_previous == 0 and time_off < time_off_min:
                on_off = 0

        # heat pump is turned on and off only according to heat pump controller
        elif self.cycling_mode is False:
            pass
        else:
            raise ValueError("Cycling mode of the advanced hplib unknown.")

        if on_off == 1:  # Calculation for building heating
            results = self.get_cached_results_or_run_hplib_simulation(
                force_convergence=force_convergence,
                t_in_primary=t_in_primary,
                t_in_secondary=t_in_secondary_sh,
                parameters=self.parameters,
                t_amb=t_amb,
                mode=1,
            )

            p_th_sh = results["P_th"].values[0]
            p_th_dhw = 0.0
            p_el_sh = results["P_el"].values[0]
            p_el_dhw = 0
            p_el_cooling = 0
            cop = results["COP"].values[0]
            eer = results["EER"].values[0]
            t_out_sh = results["T_out"].values[0]
            t_out_dhw = t_in_secondary_dhw if self.with_domestic_hot_water_preparation else 0
            m_dot_sh = results["m_dot"].values[0]
            m_dot_dhw = 0
            time_on_heating = time_on_heating + self.my_simulation_parameters.seconds_per_timestep
            time_on_cooling = 0
            time_off = 0

        elif on_off == 2:  # Calculate outputs for dhw mode
            results = self.get_cached_results_or_run_hplib_simulation(
                force_convergence=force_convergence,
                t_in_primary=t_in_primary,
                t_in_secondary=t_in_secondary_dhw,
                parameters=self.parameters,
                t_amb=t_amb,
                mode=1,
            )

            p_th_sh = 0.0
            p_el_sh = 0
            p_el_cooling = 0
            cop = results["COP"].values[0]
            eer = results["EER"].values[0]
            t_out_sh = t_in_secondary_sh
            t_out_dhw = results["T_out"].values[0]
            m_dot_sh = 0
            m_dot_dhw = results["m_dot"].values[0]
            if const_thermal_power_truefalse_dhw is True:  # True = constant thermal power output for dhw
                p_th_dhw = const_thermal_power_value_dhw
                p_el_dhw = p_th_dhw / cop
            if (
                const_thermal_power_truefalse_dhw is False or const_thermal_power_truefalse_dhw == 0
            ):  # False = modulation
                p_th_dhw = results["P_th"].values[0]
                p_el_dhw = results["P_el"].values[0]
            time_on_heating = time_on_heating + self.my_simulation_parameters.seconds_per_timestep
            time_on_cooling = 0
            time_off = 0

        elif on_off == -1:
            # Calulate outputs for cooling mode
            results = self.get_cached_results_or_run_hplib_simulation(
                force_convergence=force_convergence,
                t_in_primary=t_in_primary,
                t_in_secondary=t_in_secondary_sh,
                parameters=self.parameters,
                t_amb=t_amb,
                mode=2,
            )
            p_th_sh = results["P_th"].values[0]
            p_th_dhw = 0.0
            p_el_sh = results["P_el"].values[0]
            p_el_dhw = 0
            p_el_cooling = p_el_sh
            cop = results["COP"].values[0]
            eer = results["EER"].values[0]
            t_out_sh = results["T_out"].values[0]
            t_out_dhw = t_in_secondary_dhw if self.with_domestic_hot_water_preparation else 0
            m_dot_sh = results["m_dot"].values[0]
            m_dot_dhw = 0
            time_on_cooling = time_on_cooling + self.my_simulation_parameters.seconds_per_timestep
            time_on_heating = 0
            time_off = 0

        elif on_off == 0:
            # Calulate outputs for off mode
            p_th_sh = 0
            p_th_dhw = 0
            p_el_sh = 0
            p_el_dhw = 0
            p_el_cooling = 0
            # None values or nans will cause troubles in post processing, that is why there are not used here
            # cop = None
            # t_out = None
            cop = 0
            eer = 0
            t_out_sh = t_in_secondary_sh
            t_out_dhw = t_in_secondary_dhw if self.with_domestic_hot_water_preparation else 0
            m_dot_sh = 0
            m_dot_dhw = 0
            time_off = time_off + self.my_simulation_parameters.seconds_per_timestep
            time_on_heating = 0
            time_on_cooling = 0

        else:
            raise ValueError("Unknown mode for Advanced HPLib On_Off.")

        p_th_tot_in_watt = p_th_dhw + p_th_sh
        p_el_tot_in_watt = p_el_dhw + p_el_sh + p_el_cooling

        thermal_power_from_environment = (p_th_dhw + p_th_sh) - (p_el_dhw + p_el_sh)

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
            self.state.cumulative_thermal_energy_tot_in_watt_hour + thermal_energy_hp_tot_in_watt_hour
        )
        cumulative_hp_thermal_energy_sh_in_watt_hour = (
            self.state.cumulative_thermal_energy_sh_in_watt_hour + thermal_energy_hp_sh_in_watt_hour
        )
        cumulative_hp_thermal_energy_dhw_in_watt_hour = (
            self.state.cumulative_thermal_energy_dhw_in_watt_hour + thermal_energy_hp_dhw_in_watt_hour
        )

        cumulative_hp_electrical_energy_tot_in_watt_hour = (
            self.state.cumulative_electrical_energy_tot_in_watt_hour + electrical_energy_hp_tot_in_watt_hour
        )
        cumulative_hp_electrical_energy_sh_in_watt_hour = (
            self.state.cumulative_electrical_energy_sh_in_watt_hour + electrical_energy_hp_sh_in_watt_hour
        )
        cumulative_hp_electrical_energy_dhw_in_watt_hour = (
            self.state.cumulative_electrical_energy_dhw_in_watt_hour + electrical_energy_hp_dhw_in_watt_hour
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
            # todo: variability of massflow. now there is a fix temperaturdiffernz between inlet and outlet which calculate the massflow

            m_dot_water_primary = thermal_power_from_environment / (
                self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                * self.temperature_difference_primary_side
            )
            t_out_primary = t_in_primary - self.temperature_difference_primary_side
            stsv.set_output_value(self.m_dot_water_primary_dhnet, m_dot_water_primary)
            stsv.set_output_value(self.temp_water_primary_side_in, t_in_primary)
            stsv.set_output_value(self.temp_water_primary_side_out, t_out_primary)

        # write values for output time series
        stsv.set_output_value(self.p_th_sh, p_th_sh)
        stsv.set_output_value(self.p_th_tot, p_th_tot_in_watt)
        stsv.set_output_value(self.p_el_sh, p_el_sh)
        stsv.set_output_value(self.p_el_cooling, p_el_cooling)
        stsv.set_output_value(self.p_el_tot, p_el_tot_in_watt)
        stsv.set_output_value(self.cop, cop)
        stsv.set_output_value(self.eer, eer)
        stsv.set_output_value(self.heatpump_state, on_off)
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

        if self.with_domestic_hot_water_preparation:
            stsv.set_output_value(self.p_th_dhw, p_th_dhw)
            stsv.set_output_value(self.p_el_dhw, p_el_dhw)
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

    @staticmethod
    def get_cost_capex(config: HeatPumpHplibWithTwoOutputsConfig) -> Tuple[float, float, float]:
        """Returns investment cost, CO2 emissions and lifetime."""
        return config.cost.value, config.co2_footprint.value, config.lifetime.value

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of maintenance costs.

        No electricity costs for components except for Electricity Meter,
        because part of electricity consumption is feed by PV
        """
        for index, output in enumerate(all_outputs):
            if (
                    output.component_name == "HeatPumpHplibWithTwoOutputs"
                    and output.load_type == LoadTypes.ELECTRICITY
            ):  # Todo: check component name from system_setups: find another way of using only heatpump-outputs
                self.config.consumption = round(
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )
        opex_cost_data_class = OpexCostDataClass(
            opex_cost=self.calc_maintenance_cost(),
            co2_footprint=0,
            consumption=self.config.consumption,
        )

        return opex_cost_data_class

    def get_cached_results_or_run_hplib_simulation(
        self,
        force_convergence: bool,
        t_in_primary: float,
        t_in_secondary: float,
        parameters: pd.DataFrame,
        t_amb: float,
        mode: int,
    ) -> Any:
        """Use caching of results of hplib simulation."""

        # rounding of variable values
        if not force_convergence:
            t_in_primary = round(t_in_primary, 1)
            t_in_secondary = round(t_in_secondary, 1)
            t_amb = round(t_amb, 1)

        my_data_class = CalculationRequest(
            t_in_primary=t_in_primary,
            t_in_secondary=t_in_secondary,
            t_amb=t_amb,
            mode=mode,
        )
        my_json_key = my_data_class.get_key()
        my_hash_key = hashlib.sha256(my_json_key.encode("utf-8")).hexdigest()

        if my_hash_key in self.calculation_cache:
            results = self.calculation_cache[my_hash_key]
        else:
            results = hpl.simulate(t_in_primary, t_in_secondary, parameters, t_amb, mode=mode)

            self.calculation_cache[my_hash_key] = results

        return results


@dataclass
class HeatPumpWithTwoOutputsState:

    """HeatPumpWithTwoOutputsState class."""

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

    def self_copy(
        self,
    ):
        """Copy the Heat Pump State."""
        return HeatPumpWithTwoOutputsState(
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
        )


@dataclass
class CalculationRequest(JSONWizard):
    """Class for caching hplib parameters so that hplib.simulate does not need to run so often."""

    t_in_primary: float
    t_in_secondary: float
    t_amb: float
    mode: int

    def get_key(self):
        """Get key of class with important parameters."""

        return str(self.t_in_primary) + " " + str(self.t_in_secondary) + " " + str(self.t_amb) + " " + str(self.mode)


@dataclass_json
@dataclass
class HeatPumpHplibControllerSpaceHeatingConfig(ConfigBase):

    """HeatPump Controller Config Class for building heating."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatPumpHplibControllerSpaceHeating.get_full_classname()

    name: str
    mode: int
    set_heating_threshold_outside_temperature_in_celsius: Optional[float]
    set_cooling_threshold_outside_temperature_in_celsius: Optional[float]
    upper_temperature_offset_for_state_conditions_in_celsius: float
    lower_temperature_offset_for_state_conditions_in_celsius: float
    heat_distribution_system_type: Any

    @classmethod
    def get_default_space_heating_controller_config(
        cls, heat_distribution_system_type: Any
    ) -> "HeatPumpHplibControllerSpaceHeatingConfig":
        """Gets a default Generic Heat Pump Controller."""
        return HeatPumpHplibControllerSpaceHeatingConfig(
            name="HeatPumpHplibControllerSpaceHeating",
            mode=1,
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            set_cooling_threshold_outside_temperature_in_celsius=20.0,
            upper_temperature_offset_for_state_conditions_in_celsius=5.0,
            lower_temperature_offset_for_state_conditions_in_celsius=5.0,
            heat_distribution_system_type=heat_distribution_system_type,
        )


class HeatPumpHplibControllerSpaceHeating(Component):

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
    WaterTemperatureInputFromHeatWaterStorage = "WaterTemperatureInputFromHeatWaterStorage"
    HeatingFlowTemperatureFromHeatDistributionSystem = "HeatingFlowTemperatureFromHeatDistributionSystem"

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    SimpleHotWaterStorageTemperatureModifier = "SimpleHotWaterStorageTemperatureModifier"

    # Outputs
    State_SH = "State_SpaceHeating"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatPumpHplibControllerSpaceHeatingConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.heatpump_controller_config = config
        super().__init__(
            self.heatpump_controller_config.name,
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
            self.WaterTemperatureInputFromHeatWaterStorage,
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
                HeatPumpHplibControllerSpaceHeating.HeatingFlowTemperatureFromHeatDistributionSystem,
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
                HeatPumpHplibControllerSpaceHeating.DailyAverageOutsideTemperature,
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
        hws_classname = simple_hot_water_storage.SimpleHotWaterStorage.get_classname()
        connections.append(
            ComponentConnection(
                HeatPumpHplibControllerSpaceHeating.WaterTemperatureInputFromHeatWaterStorage,
                hws_classname,
                simple_hot_water_storage.SimpleHotWaterStorage.WaterTemperatureToHeatGenerator,
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

            water_temperature_input_from_heat_water_storage_in_celsius = stsv.get_input_value(
                self.water_temperature_input_channel
            )

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
                    water_temperature_input_in_celsius=water_temperature_input_from_heat_water_storage_in_celsius,
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
                    water_temperature_input_in_celsius=water_temperature_input_from_heat_water_storage_in_celsius,
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
                + storage_temperature_modifier  # Todo: Check if storage_temperature_modifier is neccessary here
                or summer_heating_mode == "off"
            ):
                self.controller_heatpumpmode = "off"
                return
        elif self.controller_heatpumpmode == "cooling":
            if water_temperature_input_in_celsius <= cooling_set_temperature or summer_cooling_mode == "off":
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


# implement a hplib controller l1 for dhw storage (tww)
@dataclass_json
@dataclass
class HeatPumpHplibControllerDHWConfig(ConfigBase):

    """HeatPump Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatPumpHplibControllerDHW.get_full_classname()

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
    def get_default_dhw_controller_config(cls):
        """Gets a default Generic Heat Pump Controller."""
        return HeatPumpHplibControllerDHWConfig(
            name="HeatPumpControllerDHW",
            t_min_dhw_storage_in_celsius=40.0,
            t_max_dhw_storage_in_celsius=60.0,
            thermalpower_dhw_is_constant=False,  # false: modulation, true: constant power for dhw
            p_th_max_dhw_in_watt=5000.0,  # only if true
        )


class HeatPumpHplibControllerDHW(Component):

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
        config: HeatPumpHplibControllerDHWConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.heatpump_controller_dhw_config = config
        super().__init__(
            self.heatpump_controller_dhw_config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.config: HeatPumpHplibControllerDHWConfig = config

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
                HeatPumpHplibControllerDHW.WaterTemperatureInputFromDHWStorage,
                dhw_classname,
                component_class.TemperatureMean,
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
        self.water_temperature_input_from_dhw_storage_in_celsius = 50.0
        self.water_temperature_input_from_dhw_storage_in_celsius_previous = 50.0
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
            water_temperature_input_from_dhw_storage_in_celsius = stsv.get_input_value(
                self.water_temperature_input_channel
            )
            if self.water_temperature_input_from_dhw_storage_in_celsius == 0:
                self.water_temperature_input_from_dhw_storage_in_celsius = (
                    self.water_temperature_input_from_dhw_storage_in_celsius_previous
                )

            temperature_modifier = stsv.get_input_value(self.storage_temperature_modifier_channel)

            t_min_dhw_storage_in_celsius = self.config.t_min_dhw_storage_in_celsius
            t_max_dhw_storage_in_celsius = self.config.t_max_dhw_storage_in_celsius

            if water_temperature_input_from_dhw_storage_in_celsius < t_min_dhw_storage_in_celsius:  # on
                self.state_dhw = 2

            if (
                water_temperature_input_from_dhw_storage_in_celsius
                > t_max_dhw_storage_in_celsius + temperature_modifier
            ):  # off
                self.state_dhw = 0

            if (
                temperature_modifier > 0
                and water_temperature_input_from_dhw_storage_in_celsius < t_max_dhw_storage_in_celsius
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
