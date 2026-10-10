"""More advanced heat pump module.

See library on https://github.com/FZJ-IEK3-VSA/HPLib/tree/main/HPLib

two controller: one dhw controller and one building heating controller

priority on dhw, if there is a demand from both in one timestep

preparation on district heating for water/water heatpumps

"""

import hashlib

import importlib
import math
from enum import Enum, unique
from dataclasses import dataclass, field
from typing import Any, ClassVar, List, Optional, Dict, Tuple

import pandas as pd
import numpy as np
from dataclasses_json import dataclass_json
from hplib import hplib as hpl

# Import modules from HiSim
from hisim.component import (
    Component,
    ComponentInput,
    ComponentOutput,
    SingleTimeStepValues,
    ComponentConnection,
    OpexCostDataClass,
    CapexCostDataClass,
)
from hisim.config import (
    ComponentID,
    ConfigBase,
    ConfigurationRefusedError,
    DisplayConfig,
    FactContribution,
    Sizable,
    Size,
    SizingContext,
    concrete,
    preset,
    sized_field,
)
from hisim.components import weather, simple_water_storage, heat_distribution_system
from hisim import hydronics
from hisim.energy_port import EnergyPort
from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.loadtypes import (
    ComponentType,
    EnergyBalanceCarrier,
    EnergyRole,
    InandOutputType,
    LoadTypes,
    OutputPostprocessingRules,
    Units,
)
from hisim.components.configuration import (
    EmissionFactorsAndCostsForFuelsConfig,
)

from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiTagEnumClass
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.economics.facts import CostRelevance
from hisim.components.more_advanced_heat_pump_hplib_model import (
    CalculationRequest,
    HeatingCircuitPowers,
    HeatPumpEnergyTotals,
    HeatPumpOperation,
    HeatPumpRunTimers,
    HeatPumpStepConditions,
    HeatPumpSwitchCounters,
    HplibResult,
    ScopCalibration,
    StandardizedSeasonalCop,
)


@unique
class PositionHotWaterStorageInSystemSetup(str, Enum):
    """Set Postion of Hot Water Storage in system setup.

    Every member carries its own name as its value so that a serialized
    configuration spells the storage position out instead of encoding it as an
    integer. Only member identity is meaningful; no ordinal is used anywhere.

    PARALLEL:
    Hot Water Storage is parallel to heatpump and hds, massflow of heatpump and heat distribution system are independent of each other.
    Heatpump massflow is calculated in hp model, hds massflow is calculated in hds model.

    SERIES:
    Hot Water Storage in series to hp/hds, massflow of hds is an input and connected to hp, hot water storage is between output of hds and input of hp

    NO_STORAGE:
    No Hot Water Storage in system setup for space heating
    """

    PARALLEL = "PARALLEL"
    SERIES = "SERIES"
    NO_STORAGE = "NO_STORAGE"


@dataclass_json
@dataclass
class MoreAdvancedHeatPumpHPLibConfig(ConfigBase):
    """Configuration of the MoreAdvancedHeatPumpHPLib class.

    An hplib heat pump serving space heating and, optionally, domestic hot water. The
    named default is :meth:`preset_air_water`, the generic air/water curve fit hplib
    ships; the two fields that depend on the building — the thermal output power and the
    heating reference temperature the curve fit is evaluated at — are sizable, so the
    preset leaves them ``AUTO`` and ``.resolve(ctx)`` copies them from the building's
    facts. An author who knows the machine pins the fields instead::

        MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump").resolve(
            SizingContext(heating_load_in_watt=7780.75, heating_reference_temperature_in_celsius=-7.0)
        )

    ``massflow_nominal_secondary_side_in_kg_per_s`` is deliberately *not* sizable: nothing
    in the system contributes a nominal massflow, and the 0.333 kg/s the fleet uses is a
    property of the secondary circuit, not of the building.
    """

    MAIN_CLASS = "hisim.components.more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib"

    component_id: ComponentID
    #: hplib parameter set to evaluate. ``"Generic"`` is the curve fit hplib derives from the
    #: whole device database for the given group; a manufacturer's model name picks that one
    #: machine instead.
    model: str = "Generic"
    #: Medium on the primary -- that is, the source -- side: air, brine or water. Together with
    #: ``group_id`` it says which kind of heat pump the parameters describe.
    fluid_primary_side: str = "air"
    #: hplib device group: 1 air/water, 2 brine/water, 3 water/water, 4 air/air.
    group_id: int = 1
    #: Flow temperature the curve fit is evaluated at, on the secondary (sink) side.
    flow_temperature_in_celsius: float = 52.0
    #: The highest supply temperature the hot-water circuit delivers, °C; hplib itself has no outlet limit. A
    #: charge whose outlet would exceed it is throttled to it. 75 °C is the highest
    #: flow temperature air/water heat pumps on the market reach (propane (R290) units' datasheets state 70-75 °C),
    #: and it lies above the hot-water controller's switch-off point, its 60 °C upper set temperature plus the
    #: energy manager's 10 K surplus raise, so every charge can end.
    maximal_dhw_supply_temperature_in_celsius: float = field(default=75.0, metadata={"unit": Units.CELSIUS})
    #: The unit's standardised SCOP as its datasheet states it (EN 14825, average climate) for the
    #: low- (W35) and medium-temperature (W55) application. Either, both or neither: a stated one
    #: calibrates hplib's fit to it (:class:`ScopCalibration`); unset keeps the fit as hplib ships it.
    #: Air/water and brine/water only; above 1, at most 10, and W55 not above W35.
    standardized_scop_en14825_w35: Optional[float] = field(default=None, metadata={"unit": Units.ANY})
    standardized_scop_en14825_w55: Optional[float] = field(default=None, metadata={"unit": Units.ANY})
    #: Whether the machine is allowed to cycle, which is what makes the minimum running and
    #: idle times below take effect.
    cycling_mode: bool = True
    minimum_running_time_in_seconds: Optional[int] = 3600
    minimum_idle_time_in_seconds: Optional[int] = 3600
    #: Lowest thermal power the machine modulates down to before it has to cycle.
    minimum_thermal_output_power_in_watt: float = 1800.0
    #: Where the space-heating buffer vessel sits relative to the machine, which decides
    #: whether the secondary massflow is the machine's own or the distribution system's.
    position_hot_water_storage_in_system: PositionHotWaterStorageInSystemSetup = (
        PositionHotWaterStorageInSystemSetup.PARALLEL
    )
    #: Whether the machine also heats domestic hot water, in which case it prioritises that
    #: demand over space heating.
    with_domestic_hot_water_preparation: bool = False
    #: Whether the brine circuit may cool the building passively, without running the
    #: compressor. Only meaningful for a brine/water machine.
    passive_cooling_with_brine: bool = False
    #: Electrical power the brine pump draws, for a machine with a primary circuit of its own.
    electrical_input_power_brine_pump_in_watt: Optional[float] = None
    #: Nominal massflow on the secondary side. A plain field, not a sizable one: no component
    #: contributes it, and it describes the circuit the machine is plumbed into.
    massflow_nominal_secondary_side_in_kg_per_s: float = 0.333
    #: Specific heat capacity of the primary fluid, needed only where that fluid is not air.
    specific_heat_capacity_of_primary_fluid: Optional[float] = 0.0
    #: CO2 footprint of investment in kg. Unset throughout the repository, which is what makes
    #: postprocessing look the machine up in the cost database instead.
    device_co2_footprint_in_kg: Optional[float] = None
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float] = None
    #: lifetime in years
    lifetime_in_years: Optional[float] = None
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float] = None
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float] = None
    #: Outside temperature the machine is rated at, ``t_in`` of the hplib curve fit. Sizable:
    #: left ``AUTO`` it is the building's own heating reference temperature, so the machine is
    #: rated at the same design condition the heating load was computed for.
    heating_reference_temperature_in_celsius: Sizable[float] = sized_field(
        rule=Size.HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS
    )
    #: Thermal output power the machine is sized to, ``p_th_set`` of the hplib curve fit.
    #: Sizable: left ``AUTO`` it is the building's heating load exactly, the machine covering
    #: the design load with no reserve.
    set_thermal_output_power_in_watt: Sizable[float] = sized_field(rule=Size.HEATING_LOAD_IN_WATT, unit=Units.WATT)

    @staticmethod
    def sizing_facts(config: "MoreAdvancedHeatPumpHPLibConfig", ctx: SizingContext) -> dict:
        """Contributes the machine's resolved thermal power for the components around it.

        Runs after the heat pump itself resolved, so the value is the final concrete number
        whether it came from the law, from a preset constant or from an override. The buffer
        vessel beside the machine reads it to pick its volume, the same way it reads a
        boiler's power band.

        Args:
            config: this heat pump configuration, fully resolved.
            ctx: the sizing context; unused, the value is this config's own.

        Returns:
            dict: the one fact named in :attr:`SIZING_CONTRIBUTIONS`.
        """
        del ctx
        return {"maximal_thermal_power_in_watt": concrete(config.set_thermal_output_power_in_watt)}

    #: Sizing facts this config contributes: its resolved thermal output power, under the name
    #: the heating-generator family shares, so a buffer vessel sizes from a heat pump exactly
    #: as it sizes from a boiler. With two generators in one scenario each is addressable as
    #: "<its name>.maximal_thermal_power_in_watt" and a consumer must say which one it means.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(facts=("maximal_thermal_power_in_watt",), compute=sizing_facts),
    )

    @preset
    @classmethod
    def preset_air_water(cls, name: str) -> "MoreAdvancedHeatPumpHPLibConfig":
        """The fleet's air/water heat pump, scaled to the building it heats.

        The field defaults are that machine: hplib's generic air/water parameter set
        (``model="Generic"``, ``group_id=1``), cycling with an hour of minimum running and
        idle time, modulating down to 1800 W, flowing at 52 °C into a buffer vessel parallel
        to it, and heating space only. What the preset does not fix is how large the machine
        is and what design condition it is rated at: ``set_thermal_output_power_in_watt`` and
        ``heating_reference_temperature_in_celsius`` stay ``AUTO`` so that both are copied
        from the building's facts.

        The default parameters of hplib's air/water fit are documented at
        https://github.com/FZJ-IEK3-VSA/HPLib/blob/main/HPLib/HPLib.py under ``fit_p_th_ref``.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            MoreAdvancedHeatPumpHPLibConfig: The preset configuration, power and reference
            temperature unsized.
        """
        return cls(component_id=ComponentID(name=name))


class MoreAdvancedHeatPumpHPLib(Component):
    """Simulate the heat pump.

    Outputs are heat pump efficiency (cop) as well as electrical (p_el) and
    thermal power (p_th), massflow (m_dot) and output temperature (t_out) for DHW and space heating.
    Model will switch between both states, but priority is on dhw.
    Relevant simulation parameters are loaded within the init for a
    specific or generic heat pump type.
    """

    cost_relevance = CostRelevance.PRICED

    # Inputs
    OnOffSwitchSH = "OnOffSwitchSH"  # 1 = on space heating,  0 = 0ff , -1 = cooling
    OnOffSwitchDHW = "OnOffSwitchDHW"  # 2 = on DHW , 0 = 0ff
    TemperatureInputPrimary = "TemperatureInputPrimary"  # °C
    TemperatureInputSecondarySH = "TemperatureInputSecondarySH"  # °C
    TemperatureInputSecondaryDHW = "TemperatureInputSecondaryDHW"  # °C
    #: The hot-water supply temperature the hot-water controller aims at: the hot-water supply never exceeds it.
    SupplyTemperatureSetForDHWInCelsius = "SupplyTemperatureSetForDHWInCelsius"
    TemperatureAmbient = "TemperatureAmbient"  # °C
    SetHeatingTemperatureSH = "SetHeatingTemperatureSH"

    # Outputs
    ThermalOutputPowerSH = "ThermalOutputPowerSH"  # W
    ThermalOutputPowerDHW = "ThermalOutputPowerDHW"  # W
    ThermalOutputPowerTotal = "ThermalOutputPowerTotalHeatpump"  # W
    ElectricalInputPowerSH = "ElectricalInputPowerSH"  # W
    ElectricalInputPowerForCooling = "ElectricalInputPowerForCooling"  # W
    ElectricalInputPowerDHW = "ElectricalInputPowerDHW"  # W
    ElectricalInputPowerTotal = "ElectricalInputPowerTotalHeatpump"  # W
    #: The brine or well pump's electricity, the part of the total that is neither SH, DHW nor cooling (W).
    ElectricalInputPowerBrinePump = "ElectricalInputPowerBrinePump"
    COP = "COP"  # -
    EER = "EER"  # -
    HeatPumpOnOffState = "OnOffStateHeatpump"
    TemperatureInputSH = "TemperatureInputSH"  # °C
    TemperatureInputDHW = "TemperatureInputDHW"  # °C
    TemperatureOutputSH = "TemperatureOutputSH"  # °C
    TemperatureOutputDHW = "TemperatureOutputDHW"  # °C
    MassFlowOutputSH = "MassFlowOutputSH"  # kg/s
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
    #: The space-heating part of ThermalOutputPowerSH, its positive part: heat delivered to the circuit (W).
    ThermalPowerDeliveredForSpaceHeating = "ThermalPowerDeliveredForSpaceHeating"
    #: The cooling part of ThermalOutputPowerSH, its negative part turned positive: heat drawn from the circuit (W).
    ThermalPowerDrawnForCooling = "ThermalPowerDrawnForCooling"
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

        self.t_in = int(concrete(config.heating_reference_temperature_in_celsius))

        self.t_out_val = int(config.flow_temperature_in_celsius)

        self.p_th_set = int(concrete(config.set_thermal_output_power_in_watt))

        self.cycling_mode = config.cycling_mode

        self.with_domestic_hot_water_preparation = config.with_domestic_hot_water_preparation

        self.passive_cooling_with_brine = config.passive_cooling_with_brine

        # An unset brine pump power means the machine has no brine pump of its own, so it draws nothing.
        self.electrical_input_power_brine_pump_in_watt: float = (
            0.0
            if config.electrical_input_power_brine_pump_in_watt is None
            else config.electrical_input_power_brine_pump_in_watt
        )

        self.position_hot_water_storage_in_system = config.position_hot_water_storage_in_system

        # self.m_dot_ref = float(
        #     config.massflow_nominal_secondary_side_in_kg_per_s.value
        #     if config.massflow_nominal_secondary_side_in_kg_per_s
        #     else config.massflow_nominal_secondary_side_in_kg_per_s
        # )

        self.m_dot_ref = config.massflow_nominal_secondary_side_in_kg_per_s

        if self.position_hot_water_storage_in_system in [
            PositionHotWaterStorageInSystemSetup.SERIES,
            PositionHotWaterStorageInSystemSetup.NO_STORAGE,
        ]:
            if self.m_dot_ref is None or self.m_dot_ref == 0:
                raise ValueError(
                    """If system setup is without parallel hot water storage, nominal massflow and minimum
                    thermal power of the heat pump must be given an integer value due to constant massflow
                    of water pump."""
                )

        self.fluid_primary_side = config.fluid_primary_side

        self.specific_heat_capacity_of_primary_fluid = config.specific_heat_capacity_of_primary_fluid

        self.minimum_running_time_in_seconds = (
            config.minimum_running_time_in_seconds
            if config.minimum_running_time_in_seconds
            else config.minimum_running_time_in_seconds
        )

        self.minimum_idle_time_in_seconds = (
            config.minimum_idle_time_in_seconds
            if config.minimum_idle_time_in_seconds
            else config.minimum_idle_time_in_seconds
        )

        self.minimum_thermal_output_power = config.minimum_thermal_output_power_in_watt

        # Component has states
        self.state = MoreAdvancedHeatPumpHPLibState(
            time_on_heating=0,
            time_off=0,
            time_on_cooling=0,
            on_off_previous=0,
            cumulative_thermal_energy_tot_in_watt_hour=0,
            cumulative_thermal_energy_space_heating_in_watt_hour=0,
            cumulative_thermal_energy_dhw_in_watt_hour=0,
            cumulative_electrical_energy_tot_in_watt_hour=0,
            cumulative_electrical_energy_space_heating_in_watt_hour=0,
            cumulative_electrical_energy_dhw_in_watt_hour=0,
            counter_switch_space_heating=0,
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
        self.scop_calibration = self.calibration_of(config, self.heatpump)

        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K

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

            if self.electrical_input_power_brine_pump_in_watt == 0.0 :
                raise KeyError(
                    "HP modell with brine/water as heat source need config parameter electrical_input_power_brine_pump_in_watt!"
                )

        # Define component inputs
        self.on_off_switch_space_heating: ComponentInput = self.add_input(
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

        self.t_in_secondary_space_heating: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TemperatureInputSecondarySH,
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

            self.t_in_secondary_dhw: ComponentInput = self.add_input(
                object_name=self.component_name,
                field_name=self.TemperatureInputSecondaryDHW,
                load_type=LoadTypes.TEMPERATURE,
                unit=Units.CELSIUS,
                mandatory=True,
            )

            self.supply_temperature_set_for_dhw_in_celsius_channel: ComponentInput = self.add_input(
                object_name=self.component_name,
                field_name=self.SupplyTemperatureSetForDHWInCelsius,
                load_type=LoadTypes.TEMPERATURE,
                unit=Units.CELSIUS,
                mandatory=True,
            )

        if (
            self.position_hot_water_storage_in_system
            in [
                PositionHotWaterStorageInSystemSetup.SERIES,
                PositionHotWaterStorageInSystemSetup.NO_STORAGE,
            ]
            or self.passive_cooling_with_brine
        ):
            self.set_temperature_hp_space_heating: ComponentInput = self.add_input(
                self.component_name,
                self.SetHeatingTemperatureSH,
                LoadTypes.TEMPERATURE,
                Units.CELSIUS,
                True,
            )

        # Define component outputs
        self.p_th_space_heating: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPowerSH,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description=("Thermal output power hot Water Storage in Watt"),
            postprocessing_flag=[OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )

        self.p_el_space_heating: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalInputPowerSH,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description="Electricity input power for SH in Watt",
            energy_port=EnergyPort(
                EnergyRole.IN, EnergyBalanceCarrier.ELECTRICITY, peer_output=self.ElectricalInputPowerSH
            ),
        )

        self.p_el_cooling: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalInputPowerForCooling,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
            output_description="Electricity input power for cooling in Watt",
            energy_port=EnergyPort(
                EnergyRole.IN, EnergyBalanceCarrier.ELECTRICITY, peer_output=self.ElectricalInputPowerForCooling
            ),
        )
        self.p_el_brine_pump: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalInputPowerBrinePump,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description=(
                "Electricity of the brine or well pump of a ground- or water-source heat pump in W, part of "
                "ElectricalInputPowerTotalHeatpump; zero for an air-source heat pump."
            ),
            energy_port=EnergyPort(
                EnergyRole.IN, EnergyBalanceCarrier.ELECTRICITY, peer_output=self.ElectricalInputPowerBrinePump
            ),
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

        self.t_in_space_heating: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TemperatureInputSH,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            output_description="Temperature Input SH in °C",
        )

        self.t_out_space_heating: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TemperatureOutputSH,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            output_description="Temperature Output SH in °C",
        )

        self.m_dot_space_heating: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.MassFlowOutputSH,
            load_type=LoadTypes.WARM_WATER,
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
            energy_port=EnergyPort(EnergyRole.IN, EnergyBalanceCarrier.AMBIENT_HEAT),
        )
        self.thermal_power_delivered_for_space_heating: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerDeliveredForSpaceHeating,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description="Heat delivered to the space-heating circuit in W: ThermalOutputPowerSH when positive.",
            energy_port=EnergyPort(
                EnergyRole.OUT, EnergyBalanceCarrier.SPACE_HEATING_HEAT, peer_output=self.MassFlowOutputSH
            ),
        )
        self.thermal_power_drawn_for_cooling: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerDrawnForCooling,
            load_type=LoadTypes.COOLING,
            unit=Units.WATT,
            output_description=(
                "Heat drawn from the space-heating circuit in cooling mode in W: minus ThermalOutputPowerSH when "
                "negative. It leaves, with the electricity, to the environment."
            ),
            energy_port=EnergyPort(EnergyRole.IN, EnergyBalanceCarrier.COOLING, peer_output=self.MassFlowOutputSH),
        )

        self.thermal_energy_hp_space_heating_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalEnergySH,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergySH} will follow.",
        )

        self.electrical_energy_hp_space_heating_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalEnergySH,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT_HOUR,
            output_description=f"here a description for {self.ElectricalEnergySH} will follow.",
        )

        self.cumulative_hp_thermal_energy_space_heating_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeThermalEnergySH,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT_HOUR,
            output_description=f"here a description for {self.CumulativeThermalEnergySH} will follow.",
        )

        self.cumulative_hp_electrical_energy_space_heating_channel: ComponentOutput = self.add_output(
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
                energy_port=EnergyPort(
                    EnergyRole.OUT, EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT, peer_output=self.MassFlowOutputDHW
                ),
            )

            self.p_el_dhw: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.ElectricalInputPowerDHW,
                load_type=LoadTypes.ELECTRICITY,
                unit=Units.WATT,
                output_description="Electricity input power for DHW in Watt",
                energy_port=EnergyPort(
                    EnergyRole.IN, EnergyBalanceCarrier.ELECTRICITY, peer_output=self.ElectricalInputPowerDHW
                ),
            )

            self.t_in_dhw: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.TemperatureInputDHW,
                load_type=LoadTypes.TEMPERATURE,
                unit=Units.CELSIUS,
                output_description="Temperature Input DHW in °C",
            )

            self.t_out_dhw: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.TemperatureOutputDHW,
                load_type=LoadTypes.TEMPERATURE,
                unit=Units.CELSIUS,
                output_description="Temperature Output DHW Water in °C",
            )

            self.m_dot_dhw: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.MassFlowOutputDHW,
                load_type=LoadTypes.WARM_WATER,
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

            self.counter_switch_space_heating_channel: ComponentOutput = self.add_output(
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
                load_type=LoadTypes.WATER,
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
            self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())

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
                MoreAdvancedHeatPumpHPLib.SupplyTemperatureSetForDHWInCelsius,
                hpc_dhw_classname,
                MoreAdvancedHeatPumpHPLibControllerDHW.SupplyTemperatureSetForDHWInCelsius,
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
                MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH,
                hws_classname,
                simple_water_storage.SimpleHotWaterStorage.WaterTemperatureToHeatGenerator,
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
                MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW,
                dhw_classname,
                component_class.StepMeanWaterTemperatureToHeatGeneratorInCelsius,
            )
        )
        return connections

    #: The largest standardised SCOP a configuration may state.
    MAXIMUM_STANDARDIZED_SCOP: ClassVar[float] = 10.0

    @classmethod
    def calibration_of(cls, config: MoreAdvancedHeatPumpHPLibConfig, heatpump: Any) -> ScopCalibration:
        """Return the SCOP calibration a configuration asks for, refusing an impossible one.

        Raises:
            ConfigurationRefusedError: When the W55 rating exceeds the W35 one: each value may be
                valid, their combination is not.
            ValueError: When a stated SCOP is not above 1 or above :attr:`MAXIMUM_STANDARDIZED_SCOP`,
                when the machine is neither air/water nor brine/water, the two groups EN 14825's
                rating here covers, or when hplib's fit cannot be calibrated to the stated pair
                (:meth:`ScopCalibration.of`).
        """
        w35, w55 = config.standardized_scop_en14825_w35, config.standardized_scop_en14825_w55
        given = {"standardized_scop_en14825_w35": w35, "standardized_scop_en14825_w55": w55}
        stated: Dict[str, float] = {name: value for name, value in given.items() if value is not None}
        if not stated:
            return ScopCalibration({})
        for name, value in stated.items():
            if not 1.0 < value <= cls.MAXIMUM_STANDARDIZED_SCOP:
                raise ValueError(
                    f"{config.component_id.name}: {name} is {value}; a standardised SCOP is above 1 and at "
                    f"most {cls.MAXIMUM_STANDARDIZED_SCOP}."
                )
        if w35 is not None and w55 is not None and w55 > w35:
            raise ConfigurationRefusedError(
                f"{config.component_id.name}: the W55 SCOP {w55} is above the W35 SCOP {w35}; a unit rated for "
                "55 °C water cannot outperform its 35 °C rating."
            )
        if heatpump.group_id not in StandardizedSeasonalCop.RATED_GROUPS:
            raise ValueError(
                f"{config.component_id.name}: a standardised SCOP calibrates air/water (group 1) and brine/water "
                f"(group 2) machines only, and this one is hplib group {heatpump.group_id}."
            )
        machine = f"hplib's {config.model} {StandardizedSeasonalCop.RATED_GROUPS[int(heatpump.group_id)]} fit"
        try:
            return ScopCalibration.of(heatpump, w35, w55, machine=machine)
        except ValueError as error:
            raise ValueError(f"{config.component_id.name}: {error}") from error

    def write_to_report(self):
        """Write configuration to the report, with the SCOP calibration factors when there are any."""
        lines = self.config.get_string_dict()
        for application, factor in self.scop_calibration.factors.items():
            lines.append(f"SCOP calibration factor {application.value}: {factor:.4f}")
        return lines

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

    @staticmethod
    def booked_heating_powers_in_watt(
        *,
        mass_flow_in_kg_per_second: float,
        outlet_temperature_in_celsius: float,
        return_temperature_in_celsius: float,
        cop: float,
    ) -> "HeatingCircuitPowers":
        """Return the thermal and electrical power a running heating circuit books, from the water it carries.

        The thermal power is the heat the circuit's water carries, ``P_th = m c (T_out - T_in)``
        (:func:`hisim.hydronics.circuit_power_w`), with ``T_in`` the return temperature the heat pump reads.
        The electrical power follows at the step's coefficient of performance, ``P_el = P_th / COP``.
        For example, at a return of 47.04 °C hplib's results at 47.0 and 47.1 °C are interpolated to 0.4 kg/s and
        an outlet of 52.04 °C, the 5 K lift hplib holds; at a COP of 3.5 the heat pump books
        ``0.4 * 4180 * 5.0 = 8360`` W of heat and 2388.57 W of electricity, where hplib's own thermal power is
        ``0.4 * 4200 * 5.0 = 8400`` W.

        The flow is authoritative because the storage at the other end integrates exactly this flow, so the heat
        pump books what the storage receives. While the outlet is hplib's own (not capped,
        :meth:`capped_hot_water_outlet_in_celsius`), hplib's own thermal power differs from it only
        through its specific heat of water, 4200 J/(kg K) instead of HiSim's 4180 J/(kg K): the booked heat is
        hplib's times 4180/4200, about 0.48 % less.

        Args:
            mass_flow_in_kg_per_second: The circuit's mass flow, in kg/s.
            outlet_temperature_in_celsius: The temperature the water leaves the heat pump at, in °C.
            return_temperature_in_celsius: The return temperature the heat pump reads, in °C.
            cop: The coefficient of performance of the step, dimensionless, above 0.

        Returns:
            The thermal and the electrical power, in W.

        Raises:
            ValueError: If ``cop`` is 0 or less, which would book infinite or negative electricity.
            NonFiniteValueError: If an argument is NaN or infinite, or the power overflows the float range, as
                :func:`hisim.hydronics.circuit_power_w` raises it.
            NegativeMassFlowError: If ``mass_flow_in_kg_per_second`` is negative, as
                :func:`hisim.hydronics.circuit_power_w` raises it.
        """
        if not cop > 0.0:
            raise ValueError(
                f"A running heating circuit needs a coefficient of performance above 0 to book its electricity, "
                f"got {cop}."
            )
        thermal_power_in_watt = hydronics.circuit_power_w(
            mass_flow_kg_per_s=mass_flow_in_kg_per_second,
            t_supply_c=outlet_temperature_in_celsius,
            t_return_c=return_temperature_in_celsius,
        )
        return HeatingCircuitPowers(
            thermal_power_in_watt=thermal_power_in_watt, electrical_power_in_watt=thermal_power_in_watt / cop
        )

    @staticmethod
    def active_cooling_electrical_power_in_watt(*, thermal_power_in_watt: float, eer: float) -> float:
        """Return the electricity an actively cooling heat pump draws for the heat its circuit removes, in W.

        The energy efficiency ratio (EER) is the heat removed per unit of electricity, so the electricity is the
        heat removed over the EER. A cooling circuit's thermal power is negative (its supply is colder than its
        return), so the heat removed is ``-thermal_power_in_watt``. For example, a circuit that removes 3000 W
        (thermal power -3000 W) at an EER of 4 draws 750 W.

        Args:
            thermal_power_in_watt: The cooling circuit's thermal power, in W, 0 or negative.
            eer: The energy efficiency ratio of the step, dimensionless, above 0.

        Returns:
            The electrical power, in W, 0 or more.

        Raises:
            ValueError: If ``eer`` is 0 or less, which would book no or negative electricity for the cooling.
        """
        if not eer > 0.0:
            raise ValueError(
                f"An actively cooling heat pump needs an energy efficiency ratio above 0 to book its electricity, "
                f"got {eer} for a thermal power of {thermal_power_in_watt} W."
            )
        return -thermal_power_in_watt / eer

    #: The time constant of the heat pump's start-up in the fixed-flow mode, in s: a machine that has run for ``t``
    #: seconds delivers ``1 - e^(-t / 360 s)`` of its target power, so it reaches 63 % after six minutes.
    START_UP_TIME_CONSTANT_IN_SECONDS: ClassVar[float] = 360.0

    #: The lift hplib holds between return and outlet in the parallel mode and on the hot-water side, in K.
    NOMINAL_LIFT_IN_KELVIN: ClassVar[float] = 5.0

    #: The lift that stands for zero in the fixed-flow modes, in K: hplib divides by the lift, so a set temperature
    #: equal to the return is answered with this vanishing lift instead of zero.
    VANISHING_LIFT_IN_KELVIN: ClassVar[float] = 0.00000001

    @staticmethod
    def fixed_flow_outlet_temperature_in_celsius(
        *,
        return_temperature_in_celsius: float,
        lift_in_kelvin: float,
        mass_flow_in_kg_per_second: float,
        minimal_thermal_power_in_watt: float,
        time_on_heating_in_seconds: float,
        specific_heat_capacity_in_joule_per_kg_per_kelvin: float,
    ) -> float:
        """Return the outlet temperature of a circuit the heat pump runs at a fixed flow, in °C.

        In the fixed-flow mode (a storage in series, or none) the pump runs the nominal flow and the heat pump aims
        at the power that flow carries over the lift, at least its minimal thermal power. While the machine starts,
        it delivers ``1 - e^(-t / START_UP_TIME_CONSTANT_IN_SECONDS)`` of that power, ``t`` being how long it has
        been heating. The outlet is the return plus that power over ``m c``. For example, 0.333 kg/s over a 5 K lift
        aims at 6959.7 W; after ten minutes of running it delivers 81 % of it and supplies 4.05 K above the return.

        Args:
            return_temperature_in_celsius: The circuit's return temperature, in °C.
            lift_in_kelvin: The lift the heat pump aims at, in K.
            mass_flow_in_kg_per_second: The fixed flow, in kg/s, above 0.
            minimal_thermal_power_in_watt: The lowest thermal power the machine modulates down to, in W.
            time_on_heating_in_seconds: How long the machine has been heating before this step, in s.
            specific_heat_capacity_in_joule_per_kg_per_kelvin: The specific heat of the circuit's water, in J/(kg K).

        Returns:
            The outlet temperature, in °C.
        """
        thermal_power_at_lift_in_watt = (
            mass_flow_in_kg_per_second * specific_heat_capacity_in_joule_per_kg_per_kelvin * lift_in_kelvin
        )
        if thermal_power_at_lift_in_watt <= minimal_thermal_power_in_watt:
            target_power_in_watt = minimal_thermal_power_in_watt
        else:
            target_power_in_watt = thermal_power_at_lift_in_watt
        target_power_in_watt = target_power_in_watt * (
            1 - np.exp(-time_on_heating_in_seconds / MoreAdvancedHeatPumpHPLib.START_UP_TIME_CONSTANT_IN_SECONDS)
        )
        return float(
            return_temperature_in_celsius
            + target_power_in_watt / (mass_flow_in_kg_per_second * specific_heat_capacity_in_joule_per_kg_per_kelvin)
        )

    @staticmethod
    def on_off_after_minimum_times(
        *,
        on_off: float,
        on_off_previous: float,
        timers: "HeatPumpRunTimers",
        minimum_running_time_in_seconds: float,
        minimum_idle_time_in_seconds: float,
    ) -> float:
        """Return the operating signal after the heat pump's minimum running and idle times, in cycling mode.

        A machine that heated (1 space heating, 2 hot water) for less than its minimum running time keeps heating
        in the same mode when it is asked to stop; one that cooled (-1) for less than it keeps cooling; one that
        was off (0) for less than its minimum idle time stays off. For example, a machine asked to stop after five
        minutes of space heating with a minimum running time of ten minutes keeps heating.

        Args:
            on_off: The signal the controllers ask for: 1 space heating, 2 hot water, -1 cooling, 0 off.
            on_off_previous: The signal of the step before.
            timers: How long the machine has been heating, cooling and off.
            minimum_running_time_in_seconds: The minimum running time, in s.
            minimum_idle_time_in_seconds: The minimum idle time, in s.

        Returns:
            The signal the machine runs on.
        """
        if on_off_previous == 1 and timers.time_on_heating_in_seconds < minimum_running_time_in_seconds:
            if on_off == 0:
                return 1
        elif on_off_previous == 2 and timers.time_on_heating_in_seconds < minimum_running_time_in_seconds:
            if on_off == 0:
                return 2
        elif on_off_previous == -1 and timers.time_on_cooling_in_seconds < minimum_running_time_in_seconds:
            return -1
        elif on_off_previous == 0 and timers.time_off_in_seconds < minimum_idle_time_in_seconds:
            return 0
        return on_off

    @staticmethod
    def advanced_run_timers(
        *, on_off: float, timers: "HeatPumpRunTimers", seconds_per_timestep: int
    ) -> "HeatPumpRunTimers":
        """Return the heating, cooling and off timers after a step in a mode.

        The timer of the mode the machine runs in grows by the step, the two others restart at 0; space heating and
        hot water share the heating timer. For example, a machine that heated for 600 s and heats on through a 60 s
        step has heated for 660 s and has been off and cooling for 0 s.

        Args:
            on_off: The signal the machine runs on: 1 or 2 heating, -1 cooling, 0 off.
            timers: The timers before the step.
            seconds_per_timestep: The step length, in s.

        Returns:
            The timers after the step.

        Raises:
            ValueError: If ``on_off`` is no known signal.
        """
        if on_off in (1, 2):
            return HeatPumpRunTimers(
                time_on_heating_in_seconds=timers.time_on_heating_in_seconds + seconds_per_timestep,
                time_on_cooling_in_seconds=0,
                time_off_in_seconds=0,
            )
        if on_off == -1:
            return HeatPumpRunTimers(
                time_on_heating_in_seconds=0,
                time_on_cooling_in_seconds=timers.time_on_cooling_in_seconds + seconds_per_timestep,
                time_off_in_seconds=0,
            )
        if on_off == 0:
            return HeatPumpRunTimers(
                time_on_heating_in_seconds=0,
                time_on_cooling_in_seconds=0,
                time_off_in_seconds=timers.time_off_in_seconds + seconds_per_timestep,
            )
        raise ValueError("Unknown mode for Advanced HPLib On_Off.")

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Run the heat pump in the mode its controllers ask for and publish its circuits, powers and counters.

        Reads the controllers' signals and the circuits' return temperatures, applies the minimum running and idle
        times in cycling mode, computes the step in the resulting mode (:meth:`operation_in_mode`) and publishes it
        (:meth:`publish_operation`).

        Raises:
            ValueError: If the cycling mode lacks its minimum times or the signal is no known mode.
        """
        conditions = self.step_conditions(stsv)
        timers = HeatPumpRunTimers(
            time_on_heating_in_seconds=self.state.time_on_heating,
            time_on_cooling_in_seconds=self.state.time_on_cooling,
            time_off_in_seconds=self.state.time_off,
        )
        on_off_space_heating: float = stsv.get_input_value(self.on_off_switch_space_heating)
        on_off_dhw: float = (
            stsv.get_input_value(self.on_off_switch_dhw) if self.with_domestic_hot_water_preparation else 0
        )
        on_off = on_off_dhw if on_off_dhw != 0 else on_off_space_heating

        # cycling means periodic turning on and off of the heat pump
        if self.cycling_mode is True:
            if self.minimum_running_time_in_seconds is None or self.minimum_idle_time_in_seconds is None:
                raise ValueError(
                    """When the cycling mode is true, the minimum running time and minimum idle time of the heat pump
                    must be given an integer value."""
                )
            on_off = self.on_off_after_minimum_times(
                on_off=on_off,
                on_off_previous=self.state.on_off_previous,
                timers=timers,
                minimum_running_time_in_seconds=self.minimum_running_time_in_seconds,
                minimum_idle_time_in_seconds=self.minimum_idle_time_in_seconds,
            )
        elif self.cycling_mode is not False:
            raise ValueError("Cycling mode of the advanced HPLib unknown.")

        operation = self.operation_in_mode(on_off, conditions, timers)
        timers_after = self.advanced_run_timers(
            on_off=on_off, timers=timers, seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep
        )
        self.publish_operation(stsv, on_off, conditions, operation, timers_after)

    def step_conditions(self, stsv: SingleTimeStepValues) -> "HeatPumpStepConditions":
        """Return the temperatures the heat pump reads in this step: source, ambient, returns and set points.

        A set point that this configuration does not read, such as the space-heating set temperature in the
        parallel mode, is None; without hot-water preparation the hot-water return is 0 °C, the value its idle
        outputs have always carried.
        """
        reads_space_heating_set_temperature = (
            self.position_hot_water_storage_in_system
            in [
                PositionHotWaterStorageInSystemSetup.SERIES,
                PositionHotWaterStorageInSystemSetup.NO_STORAGE,
            ]
            or self.passive_cooling_with_brine
        )
        return HeatPumpStepConditions(
            source_temperature_in_celsius=stsv.get_input_value(self.t_in_primary),
            ambient_temperature_in_celsius=stsv.get_input_value(self.t_amb),
            space_heating_return_temperature_in_celsius=stsv.get_input_value(self.t_in_secondary_space_heating),
            hot_water_return_temperature_in_celsius=(
                stsv.get_input_value(self.t_in_secondary_dhw) if self.with_domestic_hot_water_preparation else 0
            ),
            space_heating_set_temperature_in_celsius=(
                stsv.get_input_value(self.set_temperature_hp_space_heating) if reads_space_heating_set_temperature else None
            ),
            hot_water_supply_set_temperature_in_celsius=(
                stsv.get_input_value(self.supply_temperature_set_for_dhw_in_celsius_channel)
                if self.with_domestic_hot_water_preparation
                else None
            ),
        )

    def operation_in_mode(
        self, on_off: float, conditions: "HeatPumpStepConditions", timers: "HeatPumpRunTimers"
    ) -> "HeatPumpOperation":
        """Return the heat pump's powers, outlets and flows for one step in the mode ``on_off`` selects.

        1 heats the building, 2 the hot water, -1 cools the building (passively through the brine, or actively),
        0 is off.

        Raises:
            ValueError: If ``on_off`` is no known mode.
        """
        if on_off == 1:
            return self.space_heating(conditions, timers)
        if on_off == 2:
            return self.hot_water(conditions, timers)
        if on_off == -1:
            return self.passive_cooling(conditions) if self.passive_cooling_with_brine else self.active_cooling(conditions)
        if on_off == 0:
            return HeatPumpOperation.idle(conditions)
        raise ValueError("Unknown mode for Advanced HPLib On_Off.")

    def space_heating(self, conditions: "HeatPumpStepConditions", timers: "HeatPumpRunTimers") -> "HeatPumpOperation":
        """Return a space-heating step: at hplib's flow with a parallel storage, at the nominal flow otherwise."""
        if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL:
            return self.space_heating_at_hplib_flow(conditions)
        return self.space_heating_at_fixed_flow(conditions, timers)

    def hot_water(self, conditions: "HeatPumpStepConditions", timers: "HeatPumpRunTimers") -> "HeatPumpOperation":
        """Return a hot-water step: at hplib's flow with a parallel storage, at the nominal flow otherwise.

        A hot-water step holds the nominal 5 K lift and modulates down to no minimal power.
        """
        self.heatpump.delta_t = self.NOMINAL_LIFT_IN_KELVIN
        self.minimum_thermal_output_power = 0.0
        if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL:
            return self.hot_water_at_hplib_flow(conditions)
        return self.hot_water_at_fixed_flow(conditions, timers)

    def hplib_result(
        self, conditions: "HeatPumpStepConditions", *, return_temperature_in_celsius: float, mode: int, operation_mode: str
    ) -> "HplibResult":
        """Return hplib's answer at the step's source and ambient temperatures and one circuit's return.

        Args:
            conditions: The step's temperatures.
            return_temperature_in_celsius: The return temperature of the circuit hplib is asked for, in °C.
            mode: hplib's mode, 1 heating, 2 cooling.
            operation_mode: The circuit hplib is evaluated for, part of the cache key.
        """
        return self.get_cached_results_or_run_hplib_simulation(
            source_temperature_in_celsius=conditions.source_temperature_in_celsius,
            return_temperature_in_celsius=return_temperature_in_celsius,
            ambient_temperature_in_celsius=conditions.ambient_temperature_in_celsius,
            mode=mode,
            operation_mode=operation_mode,
            minimal_thermal_power_in_watt=self.minimum_thermal_output_power,
        )

    def space_heating_at_hplib_flow(self, conditions: "HeatPumpStepConditions") -> "HeatPumpOperation":
        """Return a space-heating step with a parallel storage: hplib's flow and outlet, the heat that flow carries.

        hplib holds a 5 K lift and answers the buffer's return with an outlet, a flow and a COP; the heat pump books
        the heat that flow carries and that heat over the COP (:meth:`booked_heating_powers_in_watt`).
        """
        self.heatpump.delta_t = self.NOMINAL_LIFT_IN_KELVIN
        return_in_celsius = conditions.space_heating_return_temperature_in_celsius
        results = self.hplib_result(
            conditions, return_temperature_in_celsius=return_in_celsius, mode=1, operation_mode="heating_building"
        )
        powers = self.booked_heating_powers_in_watt(
            mass_flow_in_kg_per_second=results.mass_flow_in_kg_per_second,
            outlet_temperature_in_celsius=results.outlet_temperature_in_celsius,
            return_temperature_in_celsius=return_in_celsius,
            cop=results.cop,
        )
        return HeatPumpOperation.space_heating(
            conditions,
            powers=powers,
            electrical_power_brine_pump_in_watt=self.electrical_input_power_brine_pump_in_watt,
            cop=results.cop,
            eer=results.eer,
            outlet_temperature_in_celsius=results.outlet_temperature_in_celsius,
            mass_flow_in_kg_per_second=results.mass_flow_in_kg_per_second,
        )

    def space_heating_at_fixed_flow(
        self, conditions: "HeatPumpStepConditions", timers: "HeatPumpRunTimers"
    ) -> "HeatPumpOperation":
        """Return a space-heating step in the fixed-flow mode: the nominal flow, the outlet from the target power.

        The heat pump aims at the lift to its space-heating set temperature, at most 5 K, and its outlet follows from
        that power at the nominal flow (:meth:`fixed_flow_outlet_temperature_in_celsius`); it books the heat that
        flow carries.
        """
        assert conditions.space_heating_set_temperature_in_celsius is not None
        return_in_celsius = conditions.space_heating_return_temperature_in_celsius
        mass_flow_in_kg_per_second = self.m_dot_ref
        self.heatpump.delta_t = min(
            conditions.space_heating_set_temperature_in_celsius - return_in_celsius, self.NOMINAL_LIFT_IN_KELVIN
        )
        if self.heatpump.delta_t == 0:
            self.heatpump.delta_t = self.VANISHING_LIFT_IN_KELVIN
        results = self.hplib_result(
            conditions, return_temperature_in_celsius=return_in_celsius, mode=1, operation_mode="heating_building"
        )
        outlet_in_celsius = self.fixed_flow_outlet_temperature_in_celsius(
            return_temperature_in_celsius=return_in_celsius,
            lift_in_kelvin=self.heatpump.delta_t,
            mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
            minimal_thermal_power_in_watt=self.minimum_thermal_output_power,
            time_on_heating_in_seconds=timers.time_on_heating_in_seconds,
            specific_heat_capacity_in_joule_per_kg_per_kelvin=(
                self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            ),
        )
        powers = self.booked_heating_powers_in_watt(
            mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
            outlet_temperature_in_celsius=outlet_in_celsius,
            return_temperature_in_celsius=return_in_celsius,
            cop=results.cop,
        )
        self.heatpump.delta_t = outlet_in_celsius - return_in_celsius
        return HeatPumpOperation.space_heating(
            conditions,
            powers=powers,
            electrical_power_brine_pump_in_watt=self.electrical_input_power_brine_pump_in_watt,
            cop=results.cop,
            eer=results.eer,
            outlet_temperature_in_celsius=outlet_in_celsius,
            mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
        )

    def capped_hot_water_outlet_in_celsius(
        self, conditions: "HeatPumpStepConditions", outlet_temperature_in_celsius: float
    ) -> float:
        """Return the hot-water outlet after the cap at the maximal supply and the controller's set temperature, in °C.

        hplib has no outlet limit, so the heat pump caps its hot-water supply at the lower of
        ``maximal_dhw_supply_temperature_in_celsius`` and its hot-water controller's set temperature
        (:func:`hisim.hydronics.capped_hot_water_supply_temperature_c`); the flow stays, and the circuit carries
        the heat of the capped supply.
        """
        assert conditions.hot_water_supply_set_temperature_in_celsius is not None
        return hydronics.capped_hot_water_supply_temperature_c(
            unthrottled_supply_c=outlet_temperature_in_celsius,
            return_c=conditions.hot_water_return_temperature_in_celsius,
            maximal_supply_c=self.config.maximal_dhw_supply_temperature_in_celsius,
            set_supply_c=conditions.hot_water_supply_set_temperature_in_celsius,
        )

    def hot_water_at_hplib_flow(self, conditions: "HeatPumpStepConditions") -> "HeatPumpOperation":
        """Return a hot-water step with a parallel storage: hplib's flow, its outlet capped, the heat the flow carries.

        hplib answers the tank's return; the outlet is capped (:meth:`capped_hot_water_outlet_in_celsius`), and the
        heat pump books the heat the flow carries and that heat over hplib's COP.
        """
        return_in_celsius = conditions.hot_water_return_temperature_in_celsius
        results = self.hplib_result(
            conditions, return_temperature_in_celsius=return_in_celsius, mode=1, operation_mode="heating_dhw"
        )
        outlet_in_celsius = self.capped_hot_water_outlet_in_celsius(conditions, results.outlet_temperature_in_celsius)
        powers = self.booked_heating_powers_in_watt(
            mass_flow_in_kg_per_second=results.mass_flow_in_kg_per_second,
            outlet_temperature_in_celsius=outlet_in_celsius,
            return_temperature_in_celsius=return_in_celsius,
            cop=results.cop,
        )
        return HeatPumpOperation.hot_water(
            conditions,
            powers=powers,
            electrical_power_brine_pump_in_watt=self.electrical_input_power_brine_pump_in_watt,
            cop=results.cop,
            eer=results.eer,
            outlet_temperature_in_celsius=outlet_in_celsius,
            mass_flow_in_kg_per_second=results.mass_flow_in_kg_per_second,
        )

    def hot_water_at_fixed_flow(
        self, conditions: "HeatPumpStepConditions", timers: "HeatPumpRunTimers"
    ) -> "HeatPumpOperation":
        """Return a hot-water step in the fixed-flow mode: the nominal flow, a 5 K target lift, the outlet capped.

        The outlet follows from the target power at the nominal flow (:meth:`fixed_flow_outlet_temperature_in_celsius`)
        and is capped (:meth:`capped_hot_water_outlet_in_celsius`); the heat pump books the heat the flow carries.
        """
        return_in_celsius = conditions.hot_water_return_temperature_in_celsius
        mass_flow_in_kg_per_second = self.m_dot_ref
        results = self.hplib_result(
            conditions, return_temperature_in_celsius=return_in_celsius, mode=1, operation_mode="heating_dhw"
        )
        outlet_in_celsius = self.fixed_flow_outlet_temperature_in_celsius(
            return_temperature_in_celsius=return_in_celsius,
            lift_in_kelvin=self.heatpump.delta_t,
            mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
            minimal_thermal_power_in_watt=self.minimum_thermal_output_power,
            time_on_heating_in_seconds=timers.time_on_heating_in_seconds,
            specific_heat_capacity_in_joule_per_kg_per_kelvin=(
                self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            ),
        )
        outlet_in_celsius = self.capped_hot_water_outlet_in_celsius(conditions, outlet_in_celsius)
        powers = self.booked_heating_powers_in_watt(
            mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
            outlet_temperature_in_celsius=outlet_in_celsius,
            return_temperature_in_celsius=return_in_celsius,
            cop=results.cop,
        )
        return HeatPumpOperation.hot_water(
            conditions,
            powers=powers,
            electrical_power_brine_pump_in_watt=self.electrical_input_power_brine_pump_in_watt,
            cop=results.cop,
            eer=results.eer,
            outlet_temperature_in_celsius=outlet_in_celsius,
            mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
        )

    def passive_cooling(self, conditions: "HeatPumpStepConditions") -> "HeatPumpOperation":
        """Return a passive cooling step through the brine: the nominal flow cooled by up to 5 K, no compressor.

        The circuit is cooled towards the space-heating set temperature, by at most 5 K; only the brine pump draws
        electricity, so the COP is 0 and the EER is reported as 1.
        """
        assert conditions.space_heating_set_temperature_in_celsius is not None
        return_in_celsius = conditions.space_heating_return_temperature_in_celsius
        mass_flow_in_kg_per_second = self.m_dot_ref
        self.heatpump.delta_t = min(
            return_in_celsius - conditions.space_heating_set_temperature_in_celsius, self.NOMINAL_LIFT_IN_KELVIN
        )
        if self.heatpump.delta_t == 0:
            self.heatpump.delta_t = self.VANISHING_LIFT_IN_KELVIN
        heat_drawn_in_watt = -(
            mass_flow_in_kg_per_second
            * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            * self.heatpump.delta_t
        )
        outlet_in_celsius = return_in_celsius + (
            heat_drawn_in_watt
            / (mass_flow_in_kg_per_second * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius)
        )
        thermal_power_in_watt = hydronics.circuit_power_w(
            mass_flow_kg_per_s=mass_flow_in_kg_per_second, t_supply_c=outlet_in_celsius, t_return_c=return_in_celsius
        )
        self.heatpump.delta_t = outlet_in_celsius - return_in_celsius
        return HeatPumpOperation.cooling(
            conditions,
            thermal_power_in_watt=thermal_power_in_watt,
            electrical_power_cooling_in_watt=0.0,
            electrical_power_brine_pump_in_watt=self.electrical_input_power_brine_pump_in_watt,
            cop=0,
            eer=1,
            outlet_temperature_in_celsius=outlet_in_celsius,
            mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
        )

    def active_cooling(self, conditions: "HeatPumpStepConditions") -> "HeatPumpOperation":
        """Return an active cooling step: hplib's flow and outlet in cooling mode, the electricity at its EER.

        The circuit's water carries the heat drawn, which is negative, and the compressor's electricity is that heat
        over hplib's EER (:meth:`active_cooling_electrical_power_in_watt`).
        """
        self.heatpump.delta_t = self.NOMINAL_LIFT_IN_KELVIN
        return_in_celsius = conditions.space_heating_return_temperature_in_celsius
        results = self.hplib_result(
            conditions, return_temperature_in_celsius=return_in_celsius, mode=2, operation_mode="cooling_building"
        )
        thermal_power_in_watt = hydronics.circuit_power_w(
            mass_flow_kg_per_s=results.mass_flow_in_kg_per_second,
            t_supply_c=results.outlet_temperature_in_celsius,
            t_return_c=return_in_celsius,
        )
        return HeatPumpOperation.cooling(
            conditions,
            thermal_power_in_watt=thermal_power_in_watt,
            electrical_power_cooling_in_watt=self.active_cooling_electrical_power_in_watt(
                thermal_power_in_watt=thermal_power_in_watt, eer=results.eer
            ),
            electrical_power_brine_pump_in_watt=self.electrical_input_power_brine_pump_in_watt,
            cop=results.cop,
            eer=results.eer,
            outlet_temperature_in_celsius=results.outlet_temperature_in_celsius,
            mass_flow_in_kg_per_second=results.mass_flow_in_kg_per_second,
        )

    @staticmethod
    def stepped_energy_totals(
        *,
        operation: "HeatPumpOperation",
        state: "MoreAdvancedHeatPumpHPLibState",
        seconds_per_timestep: int,
    ) -> "HeatPumpEnergyTotals":
        """Return the step's thermal and electrical energies in Wh and the running totals after the step.

        Each energy is its power times the step length; each running total grows by the magnitude of its step's
        energy, so a cooling step's negative heat counts towards the thermal total.

        Args:
            operation: The step's powers.
            state: The heat pump's state at the start of the step, with the totals so far.
            seconds_per_timestep: The step length, in s.

        Returns:
            The step's energies and the totals after it.
        """
        seconds_per_hour = hydronics.UnitConversion.JOULES_PER_WATT_HOUR
        thermal_total_in_watt_hour = operation.total_thermal_power_in_watt * seconds_per_timestep / seconds_per_hour
        thermal_space_heating_in_watt_hour = (
            operation.thermal_power_space_heating_in_watt * seconds_per_timestep / seconds_per_hour
        )
        thermal_hot_water_in_watt_hour = (
            operation.thermal_power_hot_water_in_watt * seconds_per_timestep / seconds_per_hour
        )
        electrical_total_in_watt_hour = (
            operation.total_electrical_power_in_watt * seconds_per_timestep / seconds_per_hour
        )
        electrical_space_heating_in_watt_hour = (
            operation.electrical_power_space_heating_in_watt * seconds_per_timestep / seconds_per_hour
        )
        electrical_hot_water_in_watt_hour = (
            operation.electrical_power_hot_water_in_watt * seconds_per_timestep / seconds_per_hour
        )
        return HeatPumpEnergyTotals(
            thermal_total_in_watt_hour=thermal_total_in_watt_hour,
            thermal_space_heating_in_watt_hour=thermal_space_heating_in_watt_hour,
            thermal_hot_water_in_watt_hour=thermal_hot_water_in_watt_hour,
            electrical_total_in_watt_hour=electrical_total_in_watt_hour,
            electrical_space_heating_in_watt_hour=electrical_space_heating_in_watt_hour,
            electrical_hot_water_in_watt_hour=electrical_hot_water_in_watt_hour,
            cumulative_thermal_total_in_watt_hour=(
                state.cumulative_thermal_energy_tot_in_watt_hour + abs(thermal_total_in_watt_hour)
            ),
            cumulative_thermal_space_heating_in_watt_hour=(
                state.cumulative_thermal_energy_space_heating_in_watt_hour + abs(thermal_space_heating_in_watt_hour)
            ),
            cumulative_thermal_hot_water_in_watt_hour=(
                state.cumulative_thermal_energy_dhw_in_watt_hour + abs(thermal_hot_water_in_watt_hour)
            ),
            cumulative_electrical_total_in_watt_hour=(
                state.cumulative_electrical_energy_tot_in_watt_hour + abs(electrical_total_in_watt_hour)
            ),
            cumulative_electrical_space_heating_in_watt_hour=(
                state.cumulative_electrical_energy_space_heating_in_watt_hour + abs(electrical_space_heating_in_watt_hour)
            ),
            cumulative_electrical_hot_water_in_watt_hour=(
                state.cumulative_electrical_energy_dhw_in_watt_hour + abs(electrical_hot_water_in_watt_hour)
            ),
        )

    @staticmethod
    def switch_counters(*, on_off: float, state: "MoreAdvancedHeatPumpHPLibState") -> "HeatPumpSwitchCounters":
        """Return the space-heating, hot-water and on/off switch counters after a step.

        A counter grows by one when the machine enters its mode in this step: space heating or hot water from any
        other mode, and on from off. For example, a machine that was off and heats the building in this step counts
        one more space-heating switch and one more switch on.
        """
        return HeatPumpSwitchCounters(
            space_heating=state.counter_switch_space_heating + (1 if state.on_off_previous != on_off and on_off == 1 else 0),
            hot_water=state.counter_switch_dhw + (1 if state.on_off_previous != on_off and on_off == 2 else 0),
            on_off=state.counter_onoff + (1 if state.on_off_previous == 0 and on_off != 0 else 0),
        )

    def publish_primary_side(
        self, stsv: SingleTimeStepValues, on_off: float, conditions: "HeatPumpStepConditions", operation: "HeatPumpOperation"
    ) -> None:
        """Publish the brine or water source circuit of a machine with a primary circuit of its own.

        The source circuit carries the heat taken from the environment at a fixed 5 K difference; while the machine
        is off it moves nothing.

        Raises:
            ValueError: If the primary fluid's specific heat is not configured.
        """
        if self.specific_heat_capacity_of_primary_fluid is None:
            raise ValueError("specific heat capacity on primary side has to be a value not none!")
        temperature_difference_primary_side = self.PRIMARY_SIDE_TEMPERATURE_DIFFERENCE_IN_KELVIN
        m_dot_water_primary = operation.thermal_power_from_environment_in_watt / (
            self.specific_heat_capacity_of_primary_fluid * temperature_difference_primary_side
        )
        if on_off == 0:
            temperature_difference_primary_side = 0.0
            m_dot_water_primary = 0.0
        t_out_primary = conditions.source_temperature_in_celsius - temperature_difference_primary_side
        self.state.delta_t_primary_side = temperature_difference_primary_side
        stsv.set_output_value(self.m_dot_water_primary, m_dot_water_primary)
        stsv.set_output_value(self.temp_brine_primary_side_in, conditions.source_temperature_in_celsius)
        stsv.set_output_value(self.temp_brine_primary_side_out, t_out_primary)
        stsv.set_output_value(self.temperature_difference_primary_side, temperature_difference_primary_side)

    #: The temperature difference across the source circuit of a brine or water source machine, in K.
    PRIMARY_SIDE_TEMPERATURE_DIFFERENCE_IN_KELVIN: ClassVar[float] = 5.0

    def publish_operation(
        self,
        stsv: SingleTimeStepValues,
        on_off: float,
        conditions: "HeatPumpStepConditions",
        operation: "HeatPumpOperation",
        timers: "HeatPumpRunTimers",
    ) -> None:
        """Publish the step's circuits, powers, energies, totals and counters, and store them in the state.

        The energies and running totals come from :meth:`stepped_energy_totals`, the counters from
        :meth:`switch_counters`.
        """
        totals = self.stepped_energy_totals(
            operation=operation, state=self.state, seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep
        )
        counters = self.switch_counters(on_off=on_off, state=self.state)
        if self.parameters["Group"].iloc[0] in (2, 3, 5, 6):
            self.publish_primary_side(stsv, on_off, conditions, operation)

        stsv.set_output_value(self.p_th_space_heating, operation.thermal_power_space_heating_in_watt)
        stsv.set_output_value(self.p_th_tot, operation.total_thermal_power_in_watt)
        stsv.set_output_value(self.p_el_space_heating, operation.electrical_power_space_heating_in_watt)
        stsv.set_output_value(self.p_el_cooling, operation.electrical_power_cooling_in_watt)
        stsv.set_output_value(self.p_el_brine_pump, operation.electrical_power_brine_pump_in_watt)
        stsv.set_output_value(self.p_el_tot, operation.total_electrical_power_in_watt)
        stsv.set_output_value(self.cop, operation.cop)
        stsv.set_output_value(self.eer, operation.eer)
        stsv.set_output_value(self.heatpump_state, on_off)
        stsv.set_output_value(self.t_in_space_heating, conditions.space_heating_return_temperature_in_celsius)
        stsv.set_output_value(self.t_out_space_heating, operation.outlet_temperature_space_heating_in_celsius)
        stsv.set_output_value(self.m_dot_space_heating, operation.mass_flow_space_heating_in_kg_per_second)
        stsv.set_output_value(self.time_on_heating, timers.time_on_heating_in_seconds)
        stsv.set_output_value(self.time_on_cooling, timers.time_on_cooling_in_seconds)
        stsv.set_output_value(self.time_off, timers.time_off_in_seconds)
        stsv.set_output_value(self.thermal_power_from_environment, operation.thermal_power_from_environment_in_watt)
        stsv.set_output_value(
            self.thermal_power_delivered_for_space_heating, max(operation.thermal_power_space_heating_in_watt, 0.0)
        )
        stsv.set_output_value(
            self.thermal_power_drawn_for_cooling, max(-operation.thermal_power_space_heating_in_watt, 0.0)
        )
        stsv.set_output_value(self.thermal_energy_hp_tot_channel, totals.thermal_total_in_watt_hour)
        stsv.set_output_value(self.thermal_energy_hp_space_heating_channel, totals.thermal_space_heating_in_watt_hour)
        stsv.set_output_value(self.electrical_energy_hp_tot_channel, totals.electrical_total_in_watt_hour)
        stsv.set_output_value(self.electrical_energy_hp_space_heating_channel, totals.electrical_space_heating_in_watt_hour)
        stsv.set_output_value(self.cumulative_hp_thermal_energy_tot_channel, totals.cumulative_thermal_total_in_watt_hour)
        stsv.set_output_value(
            self.cumulative_hp_thermal_energy_space_heating_channel, totals.cumulative_thermal_space_heating_in_watt_hour
        )
        stsv.set_output_value(
            self.cumulative_hp_electrical_energy_tot_channel, totals.cumulative_electrical_total_in_watt_hour
        )
        stsv.set_output_value(
            self.cumulative_hp_electrical_energy_space_heating_channel, totals.cumulative_electrical_space_heating_in_watt_hour
        )
        stsv.set_output_value(self.counter_on_off_channel, counters.on_off)
        stsv.set_output_value(self.delta_t_hp_secondary_side_channel, self.heatpump.delta_t)

        if self.with_domestic_hot_water_preparation:
            stsv.set_output_value(self.p_th_dhw, operation.thermal_power_hot_water_in_watt)
            stsv.set_output_value(self.p_el_dhw, operation.electrical_power_hot_water_in_watt)
            stsv.set_output_value(self.t_in_dhw, conditions.hot_water_return_temperature_in_celsius)
            stsv.set_output_value(self.t_out_dhw, operation.outlet_temperature_hot_water_in_celsius)
            stsv.set_output_value(self.m_dot_dhw, operation.mass_flow_hot_water_in_kg_per_second)
            stsv.set_output_value(self.thermal_energy_hp_dhw_channel, totals.thermal_hot_water_in_watt_hour)
            stsv.set_output_value(self.electrical_energy_hp_dhw_channel, totals.electrical_hot_water_in_watt_hour)
            stsv.set_output_value(
                self.cumulative_hp_thermal_energy_dhw_channel, totals.cumulative_thermal_hot_water_in_watt_hour
            )
            stsv.set_output_value(
                self.cumulative_hp_electrical_energy_dhw_channel, totals.cumulative_electrical_hot_water_in_watt_hour
            )
            stsv.set_output_value(self.counter_switch_dhw_channel, counters.hot_water)
            stsv.set_output_value(self.counter_switch_space_heating_channel, counters.space_heating)

        self.state.time_on_heating = timers.time_on_heating_in_seconds
        self.state.time_on_cooling = timers.time_on_cooling_in_seconds
        self.state.time_off = timers.time_off_in_seconds
        self.state.on_off_previous = on_off
        self.state.cumulative_thermal_energy_tot_in_watt_hour = totals.cumulative_thermal_total_in_watt_hour
        self.state.cumulative_thermal_energy_space_heating_in_watt_hour = totals.cumulative_thermal_space_heating_in_watt_hour
        self.state.cumulative_thermal_energy_dhw_in_watt_hour = totals.cumulative_thermal_hot_water_in_watt_hour
        self.state.cumulative_electrical_energy_tot_in_watt_hour = totals.cumulative_electrical_total_in_watt_hour
        self.state.cumulative_electrical_energy_space_heating_in_watt_hour = totals.cumulative_electrical_space_heating_in_watt_hour
        self.state.cumulative_electrical_energy_dhw_in_watt_hour = totals.cumulative_electrical_hot_water_in_watt_hour
        self.state.counter_switch_space_heating = counters.space_heating
        self.state.counter_switch_dhw = counters.hot_water
        self.state.counter_onoff = counters.on_off
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
        size_of_energy_system = concrete(config.set_thermal_output_power_in_watt) * 1e-3

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
        space_heating_consumption_in_kwh: float
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
                space_heating_consumption_in_kwh = round(
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
            self.my_simulation_parameters.year, self.my_simulation_parameters.country
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
            consumption_for_space_heating_in_kwh=space_heating_consumption_in_kwh,
            loadtype=LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING_AND_DOMESTIC_HOT_WATER,
        )

        return opex_cost_data_class

    #: The decimals hplib's results are cached on: every temperature it is called with is rounded to 0.1 K.
    HPLIB_GRID_DECIMALS: ClassVar[int] = 1

    #: The grid hplib's results are cached on, in K.
    HPLIB_GRID_IN_KELVIN: ClassVar[float] = 10.0**-HPLIB_GRID_DECIMALS

    #: The decimals a return temperature's grid index is rounded to before it is floored: 47.0 °C times 10 is
    #: 469.99999999999994 in floats, and rounding to 9 decimals puts it on grid point 470 instead of 469.
    GRID_INDEX_ROUNDING_DECIMALS: ClassVar[int] = 9

    #: Below this weight of the upper grid point the return lies on the lower one, and only that is evaluated.
    GRID_POINT_SHARE_TOLERANCE: ClassVar[float] = 1e-12

    def get_cached_results_or_run_hplib_simulation(
        self,
        *,
        source_temperature_in_celsius: float,
        return_temperature_in_celsius: float,
        ambient_temperature_in_celsius: float,
        mode: int,
        operation_mode: str,
        minimal_thermal_power_in_watt: float,
    ) -> "HplibResult":
        """Return hplib's results at a return temperature, interpolated linearly between the cached grid points.

        hplib is called, and its results are cached, at grid points 0.1 K apart. The source and ambient temperatures
        come from the weather and stay constant while a step iterates, so they are rounded to the grid. The return
        temperature is the storage's step mean, which moves from iteration to iteration: it is not rounded, and the
        results of the two neighbouring grid points are interpolated linearly in it, so the heat pump's answer is
        continuous in the return and the storage's step mean has a fixed point. Every number of the result is
        interpolated (:meth:`HplibResult.interpolated`). For example, a return of 47.04 °C takes 60 % of hplib's
        results at 47.0 °C and 40 % of those at 47.1 °C; its outlet is 52.04 °C.

        Args:
            source_temperature_in_celsius: The source temperature, in °C, rounded to the grid.
            return_temperature_in_celsius: The return temperature, in °C, interpolated.
            ambient_temperature_in_celsius: The ambient temperature, in °C, rounded to the grid.
            mode: hplib's mode, 1 heating, 2 cooling.
            operation_mode: The circuit hplib is evaluated for, part of the cache key.
            minimal_thermal_power_in_watt: The minimal thermal power hplib modulates down to, in W.

        Returns:
            hplib's result at the return temperature.
        """
        cells_per_kelvin = 1.0 / self.HPLIB_GRID_IN_KELVIN
        lower_index = math.floor(round(return_temperature_in_celsius * cells_per_kelvin, self.GRID_INDEX_ROUNDING_DECIMALS))
        share_of_upper = return_temperature_in_celsius * cells_per_kelvin - lower_index

        def at_grid_point(index: int) -> "HplibResult":
            """Return hplib's cached result at the grid point with this index."""
            return self.cached_hplib_result_at_grid_point(
                source_temperature_in_celsius=source_temperature_in_celsius,
                return_temperature_in_celsius=index / cells_per_kelvin,
                ambient_temperature_in_celsius=ambient_temperature_in_celsius,
                mode=mode,
                operation_mode=operation_mode,
                minimal_thermal_power_in_watt=minimal_thermal_power_in_watt,
            )

        lower = at_grid_point(lower_index)
        if share_of_upper <= self.GRID_POINT_SHARE_TOLERANCE:
            return lower
        return HplibResult.interpolated(lower=lower, upper=at_grid_point(lower_index + 1), share_of_upper=share_of_upper)

    def cached_hplib_result_at_grid_point(
        self,
        *,
        source_temperature_in_celsius: float,
        return_temperature_in_celsius: float,
        ambient_temperature_in_celsius: float,
        mode: int,
        operation_mode: str,
        minimal_thermal_power_in_watt: float,
    ) -> "HplibResult":
        """Return hplib's result at one point of its 0.1 K grid, from the cache or computed and cached.

        The source, return and ambient temperatures are rounded to the grid; the stated SCOP's calibration
        (:class:`ScopCalibration`) is applied once, before the result is cached, so a cached result is the calibrated
        machine's.

        Args:
            source_temperature_in_celsius: The source temperature, in °C.
            return_temperature_in_celsius: The return temperature, in °C, a grid point.
            ambient_temperature_in_celsius: The ambient temperature, in °C.
            mode: hplib's mode, 1 heating, 2 cooling.
            operation_mode: The circuit hplib is evaluated for, part of the cache key.
            minimal_thermal_power_in_watt: The minimal thermal power hplib modulates down to, in W.

        Returns:
            hplib's result at the grid point.
        """
        source_on_grid_in_celsius = round(source_temperature_in_celsius, self.HPLIB_GRID_DECIMALS)
        return_on_grid_in_celsius = round(return_temperature_in_celsius, self.HPLIB_GRID_DECIMALS)
        ambient_on_grid_in_celsius = round(ambient_temperature_in_celsius, self.HPLIB_GRID_DECIMALS)

        my_data_class = CalculationRequest(
            t_in_primary=source_on_grid_in_celsius,
            t_in_secondary=return_on_grid_in_celsius,
            t_amb=ambient_on_grid_in_celsius,
            mode=mode,
            operation_mode=operation_mode,
        )
        my_json_key = my_data_class.get_key()
        my_hash_key = hashlib.sha256(my_json_key.encode("utf-8")).hexdigest()

        if my_hash_key in self.calculation_cache:
            results = self.calculation_cache[my_hash_key]
        else:
            results = self.heatpump.simulate(
                t_in_primary=source_on_grid_in_celsius,
                t_in_secondary=return_on_grid_in_celsius,
                t_amb=ambient_on_grid_in_celsius,
                mode=mode,
                p_th_min=minimal_thermal_power_in_watt,
            )
            # the stated SCOP's calibration, applied once before the result is cached
            results = self.scop_calibration.apply(self.heatpump, results, mode)

            self.calculation_cache[my_hash_key] = results

        return HplibResult.from_hplib(results)

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
                        time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                    )

                    # take only output values for cooling
                    cooling_output_power_values_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] < 0.0
                    ]
                    # for cooling enery use absolute value, not negative value
                    output_cooling_energy_in_kilowatt_hour = abs(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=cooling_output_power_values_in_watt,
                            time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                elif output.field_name == self.ThermalOutputPowerDHW:
                    dhw_heat_pump_heating_power_output_in_watt_series = postprocessing_results.iloc[:, index]
                    dhw_heat_pump_heating_energy_output_in_kilowatt_hour = (
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=dhw_heat_pump_heating_power_output_in_watt_series,
                            time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )

                elif output.field_name == self.ElectricalInputPowerSH:
                    # get electrical energie values for heating
                    electrical_energy_for_heating_in_kilowatt_hour = (
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=postprocessing_results.iloc[:, index],
                            time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                elif output.field_name == self.ElectricalInputPowerDHW:
                    dhw_heat_pump_total_electricity_consumption_in_watt_series = postprocessing_results.iloc[:, index]
                    dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour = (
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=dhw_heat_pump_total_electricity_consumption_in_watt_series,
                            time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )

                elif output.field_name == self.ElectricalInputPowerForCooling:
                    # get electrical energie values for cooling
                    electrical_energy_for_cooling_in_kilowatt_hour = (
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=postprocessing_results.iloc[:, index],
                            time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )

                elif output.field_name == self.TimeOnHeating:
                    # TimeOnHeating is a running counter of the current continuous heating streak
                    # (reset to 0 whenever heating stops), so count active timesteps instead of summing it.
                    heating_time_in_seconds = (
                        postprocessing_results.iloc[:, index] > 0
                    ).sum() * self.my_simulation_parameters.seconds_per_timestep
                    heating_time_in_hours = heating_time_in_seconds / 3600

                elif output.field_name == self.TimeOnCooling:
                    # Same running-counter caveat as TimeOnHeating above.
                    cooling_time_in_seconds = (
                        postprocessing_results.iloc[:, index] > 0
                    ).sum() * self.my_simulation_parameters.seconds_per_timestep
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
                except IndexError:
                    # Expected on the last element: time_index + 1 is out of bounds.
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
        ) = KpiHelperClass.compute_mean_max_min_values(list_or_pandas_series=temperature_diff_flow_and_return_in_celsius)

        (
            mean_flow_temperature_in_celsius,
            max_flow_temperature_in_celsius,
            min_flow_temperature_in_celsius,
        ) = KpiHelperClass.compute_mean_max_min_values(list_or_pandas_series=flow_temperature_list_in_celsius)

        (
            mean_return_temperature_in_celsius,
            max_return_temperature_in_celsius,
            min_return_temperature_in_celsius,
        ) = KpiHelperClass.compute_mean_max_min_values(list_or_pandas_series=return_temperature_list_in_celsius)

        # make kpi entries and append to list
        mean_flow_temperature_space_heating_entry = KpiEntry(
            name="Mean flow temperature of SH heat pump",
            unit="°C",
            value=mean_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(mean_flow_temperature_space_heating_entry)

        mean_return_temperature_space_heating_entry = KpiEntry(
            name="Mean return temperature of SH heat pump",
            unit="°C",
            value=mean_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(mean_return_temperature_space_heating_entry)

        mean_temperature_difference_space_heating_entry = KpiEntry(
            name="Mean temperature difference of SH heat pump",
            unit="°C",
            value=mean_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(mean_temperature_difference_space_heating_entry)

        max_flow_temperature_space_heating_entry = KpiEntry(
            name="Max flow temperature of SH heat pump",
            unit="°C",
            value=max_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(max_flow_temperature_space_heating_entry)

        max_return_temperature_space_heating_entry = KpiEntry(
            name="Max return temperature of SH heat pump",
            unit="°C",
            value=max_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(max_return_temperature_space_heating_entry)

        max_temperature_difference_space_heating_entry = KpiEntry(
            name="Max temperature difference of SH heat pump",
            unit="°C",
            value=max_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(max_temperature_difference_space_heating_entry)

        min_flow_temperature_space_heating_entry = KpiEntry(
            name="Min flow temperature of SH heat pump",
            unit="°C",
            value=min_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(min_flow_temperature_space_heating_entry)

        min_return_temperature_space_heating_entry = KpiEntry(
            name="Min return temperature of SH heat pump",
            unit="°C",
            value=min_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(min_return_temperature_space_heating_entry)

        min_temperature_difference_space_heating_entry = KpiEntry(
            name="Min temperature difference of SH heat pump",
            unit="°C",
            value=min_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        list_of_kpi_entries.append(min_temperature_difference_space_heating_entry)
        return list_of_kpi_entries


@dataclass
class MoreAdvancedHeatPumpHPLibState:
    """MoreAdvancedHeatPumpHPLibState class."""

    time_on_heating: int
    time_off: int
    time_on_cooling: int
    on_off_previous: float
    cumulative_thermal_energy_tot_in_watt_hour: float
    cumulative_thermal_energy_space_heating_in_watt_hour: float
    cumulative_thermal_energy_dhw_in_watt_hour: float
    cumulative_electrical_energy_tot_in_watt_hour: float
    cumulative_electrical_energy_space_heating_in_watt_hour: float
    cumulative_electrical_energy_dhw_in_watt_hour: float
    counter_switch_space_heating: int
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
            self.cumulative_thermal_energy_space_heating_in_watt_hour,
            self.cumulative_thermal_energy_dhw_in_watt_hour,
            self.cumulative_electrical_energy_tot_in_watt_hour,
            self.cumulative_electrical_energy_space_heating_in_watt_hour,
            self.cumulative_electrical_energy_dhw_in_watt_hour,
            self.counter_switch_space_heating,
            self.counter_switch_dhw,
            self.counter_onoff,
            self.delta_t_secondary_side,
            self.delta_t_primary_side,
        )


@dataclass_json
@dataclass
class MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig(ConfigBase):
    """Configuration of the hplib heat pump's space-heating controller.

    The on/off logic in front of the machine's space-heating side: it compares the buffer
    vessel's water temperature with the flow temperature the heat distribution system asks
    for, and it stops heating once the daily average outside temperature has risen above
    the heating threshold. The named default is :meth:`preset_standard`; the three fields
    that belong to the building and its emitter circuit rather than to the machine --
    :attr:`heat_distribution_system_type`,
    :attr:`set_heating_threshold_outside_temperature_in_celsius` and
    :attr:`set_heating_temperature_for_building_in_celsius` -- are sizable, so the preset
    leaves them ``AUTO`` and ``.resolve(ctx)`` copies them from the facts the building and
    the heat distribution controller contribute::

        MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard(
            "MoreAdvancedHeatPumpHPLibControllerSH"
        ).resolve(
            SizingContext(
                set_heating_temperature_in_celsius=20.0,
                heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
                set_heating_threshold_outside_temperature_in_celsius=18.0,
            )
        )

    Copying rather than restating is the point: the building decides the room temperature it
    is heated to, the emitter circuit decides which emitter it feeds and above which outside
    temperature nothing heats, and a generator controller that disagreed with either would
    heat water too cold for the room or into a circuit that has switched off.
    """

    MAIN_CLASS = "hisim.components.more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeating"

    component_id: ComponentID
    #: Which control law runs: 1 is the plain on/off switch, 2 adds a cooling state and is
    #: only admissible for floor heating, which is what the component checks before using it.
    mode: int = 1
    #: Daily average outside temperature above which nothing heats, copied from the emitter
    #: circuit. Sizable: left ``AUTO`` it is the threshold the heat distribution controller
    #: resolved to, so both switch off on the same day. ``None`` is a legal value and means
    #: the machine never stops for the season, whatever the weather does.
    set_heating_threshold_outside_temperature_in_celsius: Sizable[Optional[float]] = sized_field(
        rule=Size.SET_HEATING_THRESHOLD_OUTSIDE_TEMPERATURE_IN_CELSIUS, optional=True
    )
    #: Daily average outside temperature below which the machine does not cool in ``mode``
    #: 2. ``None`` means cooling is available at any outside temperature.
    set_cooling_threshold_outside_temperature_in_celsius: Optional[float] = 20.0
    #: How far the water temperature may rise above the flow temperature the distribution
    #: system asks for before the machine switches off, in kelvin.
    upper_temperature_offset_for_state_conditions_in_celsius: float = 5.0
    #: How far it may fall below that flow temperature before the machine switches on, in
    #: kelvin. Together with the upper offset this is the hysteresis band.
    lower_temperature_offset_for_state_conditions_in_celsius: float = 5.0
    #: Which emitter the circuit this controller heats into feeds, copied from the emitter
    #: circuit. Sizable: left ``AUTO`` it is the heat distribution controller's own emitter
    #: type. The component reads it to decide whether ``mode`` 2 is admissible at all.
    heat_distribution_system_type: Sizable[HeatDistributionSystemType] = sized_field(
        rule=Size.HEAT_DISTRIBUTION_SYSTEM_TYPE, value_type=HeatDistributionSystemType
    )
    #: The room temperature the building is heated to, copied from the building. Sizable: left
    #: ``AUTO`` it is the building's own setpoint. It floors the hysteresis band (hisim-q1rm).
    set_heating_temperature_for_building_in_celsius: Sizable[float] = sized_field(
        rule=Size.SET_HEATING_TEMPERATURE_IN_CELSIUS
    )

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig":
        """The one space-heating controller the fleet runs, taking its limits from the emitter circuit.

        The field defaults are that controller: the plain on/off law, a five-kelvin
        hysteresis band either side of the requested flow temperature, and no cooling below
        20 °C outside. What the preset does not fix is the emitter type, the heating
        threshold and the room setpoint, which stay ``AUTO`` so that they are copied from the
        heat distribution controller and the building instead of repeating their choices here.

        Args:
            name: Instance name of the controller in the simulation.

        Returns:
            The configuration, with its three sizable fields still ``AUTO``.
        """
        return cls(component_id=ComponentID(name=name))


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

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    WaterTemperatureInput = "WaterTemperatureInput"
    HeatingFlowTemperatureFromHeatDistributionSystem = "HeatingFlowTemperatureFromHeatDistributionSystem"

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    SimpleHotWaterStorageTemperatureModifier = "SimpleHotWaterStorageTemperatureModifier"

    # Outputs
    State_SH = "State_SH"

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
        self.set_heating_temperature_for_building_in_celsius: float = float(
            concrete(self.heatpump_controller_config.set_heating_temperature_for_building_in_celsius)
        )
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
        self.add_default_connections(self.get_default_connections_from_energy_management_system())

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

    def get_default_connections_from_energy_management_system(
        self,
    ):
        """Get energy management system default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.controller_l2_energy_management_system"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "L2GenericEnergyManagementSystem")
        connections = []
        ems_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.SimpleHotWaterStorageTemperatureModifier,
                ems_classname,
                component_class.SpaceHeatingWaterStorageTemperatureModifier,
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
                set_heating_threshold_temperature_in_celsius=concrete(
                    self.heatpump_controller_config.set_heating_threshold_outside_temperature_in_celsius
                ),
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
        switch_on_in_celsius, switch_off_in_celsius = self.heating_switch_temperatures_in_celsius(
            set_heating_flow_temperature_in_celsius,
            upper_temperature_offset_for_state_conditions_in_celsius,
            lower_temperature_offset_for_state_conditions_in_celsius,
            storage_temperature_modifier,
        )

        if self.controller_heatpumpmode == "heating":
            if water_temperature_input_in_celsius > switch_off_in_celsius or summer_heating_mode == "off":
                self.controller_heatpumpmode = "off"
                return

        elif self.controller_heatpumpmode == "off":
            if self.start_heating_if_due(water_temperature_input_in_celsius, switch_on_in_celsius, summer_heating_mode):
                return

        else:
            raise ValueError("unknown mode")

    def heating_switch_temperatures_in_celsius(
        self,
        set_heating_flow_temperature_in_celsius: float,
        upper_temperature_offset_for_state_conditions_in_celsius: float,
        lower_temperature_offset_for_state_conditions_in_celsius: float,
        storage_temperature_modifier: float,
    ) -> Tuple[float, float]:
        """Return the water temperatures at which the machine starts and stops heating.

        The hysteresis band puts the switch-on point ``lower_temperature_offset`` below the flow
        temperature the emitter circuit asks for, and the switch-off point ``upper_temperature_offset``
        above it. A floor-heating curve asks for barely more than the room setpoint in mild
        weather (about 23 °C at 21 °C), which put the switch-on point below the room temperature:
        the buffer, sitting at room temperature, never reached it, and the machine stayed off
        from spring to autumn while the room cooled to 18 °C (hisim-q1rm). The switch-on point is
        therefore never below the building's heating setpoint, since water colder than that
        cannot heat the room.

        The switch-off point is floored the same way, at least ``upper_temperature_offset`` above
        the switch-on point: a flow request below ``room setpoint - upper_temperature_offset``
        would otherwise put it below the switch-on point, and the machine would toggle every
        step. The invariant is ``switch-off - switch-on >= upper_temperature_offset``, the
        storage modifier included, since the modifier raises both points alike. Wherever the
        flow temperature is at or above the room setpoint -- which the heat distribution
        controller's heating curve guarantees, its lowest flow being the room setpoint -- the
        switch-off point is exactly ``flow + upper_temperature_offset + modifier``, as it always was.

        Args:
            set_heating_flow_temperature_in_celsius: The flow temperature the circuit asks for.
            upper_temperature_offset_for_state_conditions_in_celsius: The upper half of the band.
            lower_temperature_offset_for_state_conditions_in_celsius: The lower half of the band.
            storage_temperature_modifier: The energy management system's raise of the target.

        Returns:
            The switch-on and the switch-off temperature in °C.
        """
        switch_on_without_modifier_in_celsius = max(
            set_heating_flow_temperature_in_celsius - lower_temperature_offset_for_state_conditions_in_celsius,
            self.set_heating_temperature_for_building_in_celsius,
        )
        switch_off_without_modifier_in_celsius = (
            max(set_heating_flow_temperature_in_celsius, switch_on_without_modifier_in_celsius)
            + upper_temperature_offset_for_state_conditions_in_celsius
        )
        return (
            switch_on_without_modifier_in_celsius + storage_temperature_modifier,
            switch_off_without_modifier_in_celsius + storage_temperature_modifier,
        )

    def start_heating_if_due(
        self,
        water_temperature_input_in_celsius: float,
        switch_on_temperature_in_celsius: float,
        summer_heating_mode: str,
    ) -> bool:
        """Switch an idle machine to heating if the water is below the switch-on point in the heating season.

        Returns:
            Whether the machine now heats.
        """
        if water_temperature_input_in_celsius < switch_on_temperature_in_celsius and summer_heating_mode == "on":
            self.controller_heatpumpmode = "heating"
            return True
        return False

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
        cooling_set_temperature = set_heating_flow_temperature_in_celsius
        # Todo: Check if storage_temperature_modifier is neccessary for the switch-off point
        switch_on_in_celsius, switch_off_in_celsius = self.heating_switch_temperatures_in_celsius(
            set_heating_flow_temperature_in_celsius,
            upper_temperature_offset_for_state_conditions_in_celsius,
            lower_temperature_offset_for_state_conditions_in_celsius,
            storage_temperature_modifier,
        )

        if self.controller_heatpumpmode == "heating":
            if water_temperature_input_in_celsius >= switch_off_in_celsius or summer_heating_mode == "off":
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
            if self.start_heating_if_due(water_temperature_input_in_celsius, switch_on_in_celsius, summer_heating_mode):
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
    """Configuration of the hplib heat pump's domestic-hot-water controller.

    The hysteresis in front of the machine's hot-water side: it switches the machine on
    when the DHW vessel has cooled to :attr:`t_min_dhw_storage_in_celsius` and off again
    when it has reached :attr:`t_max_dhw_storage_in_celsius`. The named default is
    :meth:`preset_standard`, the 40/60 °C band the fleet runs::

        MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard("HeatPumpControllerDHW")

    Nothing here depends on the building or on the machine beside it, which is why no
    field is sizable and the preset takes nothing but the instance name.
    """

    MAIN_CLASS = "hisim.components.more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHW"

    component_id: ComponentID
    #: lower set temperature of DHW Storage, given in °C
    t_min_dhw_storage_in_celsius: float = 40.0
    #: upper set temperature of DHW Storage, given in °C
    t_max_dhw_storage_in_celsius: float = 60.0

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MoreAdvancedHeatPumpHPLibControllerDHWConfig":
        """The one hot-water controller the fleet runs, reheating the vessel from 40 to 60 °C.

        The field defaults are that controller: the 40/60 °C band that keeps the vessel above the
        legionella temperature without cycling the machine on every tap.

        Args:
            name: Instance name of the controller in the simulation.

        Returns:
            The configuration, fully concrete -- the class has no sizable field.
        """
        return cls(component_id=ComponentID(name=name))


@unique
class HeatPumpDhwState(int, Enum):

    """The hot-water states of the hplib heat pump's hot-water controller, with the signal values it publishes.

    The heat pump reads the value as its hot-water switch: 2 asks for a hot-water charge, 0 for none.
    """

    #: No hot-water charge.
    OFF = 0
    #: A hot-water charge.
    ON = 2


class MoreAdvancedHeatPumpHPLibControllerDHW(Component):
    """Heat Pump Controller for DHW.

    It takes data from DHW Storage --> generic hot water storage modular
    sends signal to the heat pump for activation or deactivation.

    """

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    WaterTemperatureInputFromDHWStorage = "WaterTemperatureInputFromDHWStorage"
    DHWStorageTemperatureModifier = "DHWStorageTemperatureModifier"

    # Outputs
    State_dhw = "StateDHW"
    #: The hot-water supply temperature the controller aims at: ``t_max`` plus the energy manager's raise.
    SupplyTemperatureSetForDHWInCelsius = "SupplyTemperatureSetForDHWInCelsius"

    #: How far below its set temperature the tank's start temperature may stay for the charge to end, K. The heat
    #: pump's hot-water supply stops at the set temperature (``t_max`` plus the energy manager's raise), so the tank
    #: approaches it but never passes it; the charge ends once the tank is within 0.5 K of it, as a boiler's charge
    #: ends at its target. The surplus switch-on stops at the same point below ``t_max``: were it at ``t_max``, a tank
    #: between ``t_max - 0.5 K`` and ``t_max`` would be switched on by the raise and off without it, and the energy
    #: manager's raise, which follows the heat pump's draw, would toggle it within a step.
    SWITCH_OFF_TOLERANCE_IN_KELVIN: ClassVar[float] = 0.5

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

        self.state_dhw: HeatPumpDhwState
        self.previous_state_dhw: HeatPumpDhwState
        self.water_temperature_input_from_dhw_storage_in_celsius_previous: float
        self.water_temperature_input_from_dhw_storage_in_celsius: float
        self.supply_temperature_set_for_dhw_in_celsius: float

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
            self.DHWStorageTemperatureModifier,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=False,
        )

        self.state_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.State_dhw,
            LoadTypes.ANY,
            Units.ANY,
            output_description=(
                "The hot-water mode the controller commands the heat pump: 2 charges the hot-water tank, 0 does not."
            ),
        )

        self.supply_temperature_set_for_dhw_in_celsius_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.SupplyTemperatureSetForDHWInCelsius,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            output_description=(
                "The hot-water supply temperature the controller aims at, t_max plus the energy manager's raise "
                "(60 °C by default). The heat pump's hot-water supply stops at it, so a charge ends at its target "
                "inside the step."
            ),
        )

        self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())
        self.add_default_connections(self.get_default_connections_from_energy_management_system())

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
                # the tank's start-of-step temperature T0: the controller decides on a value the step's
                # iteration does not move
                component_class.WaterTemperatureAtStartOfStepInCelsius,
            )
        )
        return connections

    def get_default_connections_from_energy_management_system(
        self,
    ):
        """Get energy management system default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.controller_l2_energy_management_system"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "L2GenericEnergyManagementSystem")
        connections = []
        ems_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerDHW.DHWStorageTemperatureModifier,
                ems_classname,
                component_class.DomesticHotWaterStorageTemperatureModifier,
            )
        )
        return connections

    def build(
        self,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """

        self.state_dhw = HeatPumpDhwState.OFF
        self.water_temperature_input_from_dhw_storage_in_celsius = self.INITIAL_STORAGE_TEMPERATURE_IN_CELSIUS
        self.supply_temperature_set_for_dhw_in_celsius = self.config.t_max_dhw_storage_in_celsius

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

    #: The tank temperature the controller assumes before it has read one, in °C: the lower end of the default band.
    INITIAL_STORAGE_TEMPERATURE_IN_CELSIUS: ClassVar[float] = 40.0

    @staticmethod
    def next_state(
        *,
        state: "HeatPumpDhwState",
        storage_temperature_in_celsius: float,
        raise_in_kelvin: float,
        minimum_temperature_in_celsius: float,
        maximum_temperature_in_celsius: float,
    ) -> "HeatPumpDhwState":
        """Return the hot-water state after one decision on the tank's start-of-step temperature.

        The transitions, applied in this order, the last that applies winning:

        | condition | next state |
        |---|---|
        | the tank is below ``minimum_temperature_in_celsius`` | ON |
        | the tank is at or above ``maximum + raise - SWITCH_OFF_TOLERANCE_IN_KELVIN`` | OFF |
        | a raise above 0 and the tank below ``maximum - SWITCH_OFF_TOLERANCE_IN_KELVIN`` | ON |
        | none of these | the state as it was |

        The raise is the energy manager's increase of the set temperature while it has surplus electricity. For
        example, with the 40/60 °C band and no raise a tank at 50 °C keeps its state, one at 39 °C switches on and
        one at 59.5 °C switches off; with a 10 K raise, a tank at 59 °C switches on and charges until 69.5 °C.

        Args:
            state: The state before the decision.
            storage_temperature_in_celsius: The tank's start-of-step temperature, in °C.
            raise_in_kelvin: The energy manager's raise of the set temperature, in K, 0 or more.
            minimum_temperature_in_celsius: The tank temperature below which a charge starts, in °C.
            maximum_temperature_in_celsius: The set temperature without a raise, in °C.

        Returns:
            The state after the decision.
        """
        next_state = state
        if storage_temperature_in_celsius < minimum_temperature_in_celsius:
            next_state = HeatPumpDhwState.ON
        switch_off_tolerance_in_kelvin = MoreAdvancedHeatPumpHPLibControllerDHW.SWITCH_OFF_TOLERANCE_IN_KELVIN
        if storage_temperature_in_celsius >= (
            maximum_temperature_in_celsius + raise_in_kelvin - switch_off_tolerance_in_kelvin
        ):
            # the tank has reached its target, to which the heat pump's supply is capped
            next_state = HeatPumpDhwState.OFF
        if raise_in_kelvin > 0 and storage_temperature_in_celsius < (
            maximum_temperature_in_celsius - switch_off_tolerance_in_kelvin
        ):
            # on with surplus electricity, below where the unraised charge ends, so a raise that comes and goes
            # within a step cannot switch the charge on and off
            next_state = HeatPumpDhwState.ON
        return next_state

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Decide whether the heat pump charges the hot-water tank, and publish the decision and the set temperature.

        The decision (:meth:`next_state`) is taken on the tank's start-of-step temperature, which does not change
        while the step iterates. Under ``force_convergence`` the controller keeps its decision.
        """
        if not force_convergence:
            storage_temperature_in_celsius = stsv.get_input_value(self.water_temperature_input_channel)
            if storage_temperature_in_celsius == 0:
                # The simulator starts every step from zeroed outputs, so a controller simulated before the tank
                # reads 0 °C on the first pass and would start a charge; it keeps the temperature it read last
                # instead. This guard goes once the controller restores its decision on every pass.
                storage_temperature_in_celsius = self.water_temperature_input_from_dhw_storage_in_celsius_previous
            self.water_temperature_input_from_dhw_storage_in_celsius = storage_temperature_in_celsius
            raise_in_kelvin = stsv.get_input_value(self.storage_temperature_modifier_channel)
            self.supply_temperature_set_for_dhw_in_celsius = self.config.t_max_dhw_storage_in_celsius + raise_in_kelvin
            self.state_dhw = self.next_state(
                state=self.state_dhw,
                storage_temperature_in_celsius=storage_temperature_in_celsius,
                raise_in_kelvin=raise_in_kelvin,
                minimum_temperature_in_celsius=self.config.t_min_dhw_storage_in_celsius,
                maximum_temperature_in_celsius=self.config.t_max_dhw_storage_in_celsius,
            )

        self.previous_state_dhw = self.state_dhw
        self.water_temperature_input_from_dhw_storage_in_celsius_previous = (
            self.water_temperature_input_from_dhw_storage_in_celsius
        )

        stsv.set_output_value(self.state_dhw_channel, self.state_dhw.value)
        stsv.set_output_value(
            self.supply_temperature_set_for_dhw_in_celsius_channel, self.supply_temperature_set_for_dhw_in_celsius
        )

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
