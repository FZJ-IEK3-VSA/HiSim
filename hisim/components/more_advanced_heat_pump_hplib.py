"""More advanced heat pump module.

See library on https://github.com/FZJ-IEK3-VSA/HPLib/tree/main/HPLib

two controller: one dhw controller and one building heating controller

priority on dhw, if there is a demand from both in one timestep

preparation on district heating for water/water heatpumps

"""

import hashlib

import importlib
from enum import Enum, unique
from dataclasses import dataclass
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
from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.loadtypes import LoadTypes, Units, InandOutputType, OutputPostprocessingRules, ComponentType
from hisim.components.configuration import (
    PhysicsConfig,
    EmissionFactorsAndCostsForFuelsConfig,
)

from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiTagEnumClass
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.economics.facts import CostRelevance


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


class StandardizedSeasonalCop:
    """The seasonal COP of an hplib heat pump by the bin method of EN 14825, average climate.

    What a manufacturer states on the datasheet or ErP fiche is a SCOP rated this way, so
    computing the same figure for hplib's curve fit is what lets the fit be calibrated to it
    (:class:`ScopCalibration`). The method, in the simplified form the calibration needs:

    * the heating season is :attr:`BINS`, one outdoor temperature per bin with its hours;
    * the building's demand falls linearly from the design load at :attr:`DESIGN_TEMPERATURE` to
      zero at :attr:`HEATING_LIMIT_TEMPERATURE`;
    * the design load is the machine's thermal output at A-7/W52 divided by the part load of
      A-7, :attr:`DESIGN_SIZING_PART_LOAD` (the machine covers A-7 exactly, as EN 14825's
      reference sizing has it);
    * the outlet temperature follows the application's line, :attr:`FLOW_LINES`, through two
      points and extended beyond them;
    * the machine modulates at the COP hplib gives for the bin (hplib's own 25 % electrical floor
      is the only part-load limit it knows), and whatever the demand exceeds its output is met by
      an electric back-up heater at COP 1, as is a bin where hplib's COP is 1 or less.

    Source: EN 14825:2022, average climate (bins -10 to +15 °C, 4910 h) and the variable-outlet
    lines of its low- (W35) and medium-temperature (W55) applications. For a brine/water machine
    (hplib group 2) the brine enters at :attr:`BRINE_TEMPERATURE` in every bin, EN 14825's rating
    condition, while the ambient term still follows the bin.

    Example::

        StandardizedSeasonalCop.of(heatpump, ScopApplication.W55)   # 3.40 for hplib's generic air/water fit
    """

    #: Outdoor bin temperature in °C and its hours in the EN 14825 average heating season.
    BINS: ClassVar[Tuple[Tuple[int, int], ...]] = (
        (-10, 1), (-9, 25), (-8, 23), (-7, 24), (-6, 27), (-5, 68), (-4, 91), (-3, 89), (-2, 165),
        (-1, 173), (0, 240), (1, 280), (2, 320), (3, 357), (4, 356), (5, 303), (6, 330), (7, 326),
        (8, 348), (9, 335), (10, 315), (11, 215), (12, 169), (13, 151), (14, 105), (15, 74),
    )

    #: Design outdoor temperature of the average climate, °C.
    DESIGN_TEMPERATURE: ClassVar[float] = -10.0

    #: Outdoor temperature at which the heating demand is zero, °C.
    HEATING_LIMIT_TEMPERATURE: ClassVar[float] = 16.0

    #: The rating point the design load is taken from: A-7/W52.
    SIZING_OUTDOOR_TEMPERATURE: ClassVar[float] = -7.0
    SIZING_FLOW_TEMPERATURE: ClassVar[float] = 52.0

    #: Part load at A-7, (-7 - 16) / (-10 - 16) = 0.885: the design load is the A-7 output over it.
    DESIGN_SIZING_PART_LOAD: ClassVar[float] = (SIZING_OUTDOOR_TEMPERATURE - HEATING_LIMIT_TEMPERATURE) / (
        DESIGN_TEMPERATURE - HEATING_LIMIT_TEMPERATURE
    )

    #: Brine temperature of every bin for a brine/water machine, °C.
    BRINE_TEMPERATURE: ClassVar[float] = 0.0

    #: hplib's secondary-side temperature rise: the outlet is the inlet plus this, K.
    SECONDARY_TEMPERATURE_RISE: ClassVar[float] = 5.0

    #: Outlet-temperature line per application, as two (outdoor °C, outlet °C) points.
    FLOW_LINES: ClassVar[Dict[str, Tuple[Tuple[float, float], Tuple[float, float]]]] = {
        "W35": ((-7.0, 34.0), (12.0, 24.0)),
        "W55": ((-7.0, 52.0), (12.0, 30.0)),
    }

    @classmethod
    def flow_temperature(cls, application: "ScopApplication", outdoor_temperature: float) -> float:
        """Return the application's outlet temperature at one outdoor temperature, °C."""
        (t_1, f_1), (t_2, f_2) = cls.FLOW_LINES[application.value]
        return f_1 + (f_2 - f_1) * (outdoor_temperature - t_1) / (t_2 - t_1)

    @classmethod
    def part_load(cls, outdoor_temperature: float) -> float:
        """Return the building's demand at one outdoor temperature as a share of the design load."""
        return (outdoor_temperature - cls.HEATING_LIMIT_TEMPERATURE) / (
            cls.DESIGN_TEMPERATURE - cls.HEATING_LIMIT_TEMPERATURE
        )

    @classmethod
    def mean_outlet_temperature(cls, application: "ScopApplication") -> float:
        """Return the application's outlet temperature averaged over the season, weighted by heat, °C.

        28.9 °C for W35 and 40.7 °C for W55: where the rating actually spends its heat, which is
        why :class:`ScopCalibration` anchors each rating's factor there.
        """
        weights = [(hours * cls.part_load(outdoor), outdoor) for outdoor, hours in cls.BINS]
        return sum(weight * cls.flow_temperature(application, outdoor) for weight, outdoor in weights) / sum(
            weight for weight, _outdoor in weights
        )

    @classmethod
    def of(
        cls, heatpump: Any, application: "ScopApplication", calibration: Optional["ScopCalibration"] = None
    ) -> float:
        """Return the seasonal COP of one hplib heat pump for one application.

        Args:
            heatpump: An ``hplib.HeatPump`` of group 1 (air/water) or 2 (brine/water); it is only
                read, through pure ``simulate`` calls.
            application: The rating's application, which picks the outlet-temperature line.
            calibration: Applied to every call when given, so the calibrated machine is rated.

        Returns:
            Heat delivered over electricity drawn across the season.
        """
        def run(outdoor: float, flow: float) -> Tuple[float, float]:
            source = outdoor if heatpump.group_id == 1 else cls.BRINE_TEMPERATURE
            result = heatpump.simulate(
                t_in_primary=source,
                t_in_secondary=flow - cls.SECONDARY_TEMPERATURE_RISE,
                t_amb=outdoor,
                mode=1,
                p_th_min=0,
            )
            if calibration is not None:
                result = calibration.apply(heatpump, result, 1)
            return float(result["P_th"]), float(result["COP"])

        design_load, _cop = run(cls.SIZING_OUTDOOR_TEMPERATURE, cls.SIZING_FLOW_TEMPERATURE)
        design_load /= cls.DESIGN_SIZING_PART_LOAD
        heat = 0.0
        electricity = 0.0
        for outdoor, hours in cls.BINS:
            demand = design_load * cls.part_load(outdoor)
            output, cop = run(outdoor, cls.flow_temperature(application, outdoor))
            by_heat_pump = min(demand, output) if cop > 1 else 0.0
            heat += hours * demand
            electricity += hours * ((by_heat_pump / cop if by_heat_pump else 0.0) + demand - by_heat_pump)
        return heat / electricity


@unique
class ScopApplication(str, Enum):
    """The two EN 14825 applications a datasheet rates a heat pump's SCOP for."""

    W35 = "W35"
    W55 = "W55"


class ScopCalibration:
    """Scales an hplib heat pump's COP to the SCOP its datasheet states (hisim-4g9.15).

    hplib evaluates a linear curve fit; for ``model="Generic"`` it is the fit over its whole
    database group, so the simulated machine is the average unit of its group. A stated
    standardised SCOP says how much better or worse the real unit is. Every heating call multiplies
    hplib's COP by a factor for its own outlet temperature and divides the compressor's
    electricity by it; the thermal output is unchanged, so the building gets the same heat for
    less or more electricity. The factors are solved (:meth:`of`) so that the calibrated machine,
    rated by :class:`StandardizedSeasonalCop`, gives exactly the stated SCOPs.

    * Both ratings stated: the factor is linear in the outlet temperature between each rating's
      anchor and held at the nearer anchor's value outside. The anchors are the heat-weighted
      mean outlets of the two rating lines (28.9 and 40.7 °C), where each rating spends its heat;
      anchoring at the nominal 35 / 55 °C instead is ill-conditioned and drives the 55 °C factor
      to 0.2–0.5 for wide pairs (decision with Noah, 2026-09-23; to be checked against measured
      units, hisim-4g9.15 follow-up bead).
    * One stated: its factor everywhere.
    * The heating rod is never calibrated: a call where hplib runs the rod alone (COP 1) is left
      as it is, and in hplib's compressor-plus-rod branch only the compressor's share is scaled.
    * Cooling (mode 2) is out of scope and left as hplib returns it.

    Example::

        calibration = ScopCalibration.of(heatpump, scop_w35=4.6, scop_w55=3.4)
        calibration.apply(heatpump, heatpump.simulate(...), mode=1)
    """

    #: How close the calibrated rating must come to the stated SCOP, and how many rounds it may take.
    TOLERANCE: ClassVar[float] = 1e-5
    MAXIMUM_ITERATIONS: ClassVar[int] = 50

    def __init__(self, factors: Dict["ScopApplication", float]) -> None:
        """Hold the factor per stated application; an empty map calibrates nothing."""
        self.factors = dict(factors)

    @classmethod
    def of(cls, heatpump: Any, scop_w35: Optional[float], scop_w55: Optional[float]) -> "ScopCalibration":
        """Return the factors that bring the fit, rated by the bin method, to the stated SCOPs.

        Starts from ``stated / rated`` per application and repeats ``k <- k * stated / rated`` on the
        calibrated machine until every rating is within :attr:`TOLERANCE`: the back-up heater's
        share of the season is not scaled and, with two ratings, each factor reaches into the other
        rating's line, so one ratio is not exact. Converges in about ten rounds.

        Raises:
            ValueError: When the ratings do not converge within :attr:`MAXIMUM_ITERATIONS`.
        """
        stated = {
            application: float(value)
            for application, value in ((ScopApplication.W35, scop_w35), (ScopApplication.W55, scop_w55))
            if value is not None
        }
        factors = {application: value / StandardizedSeasonalCop.of(heatpump, application) for application, value in stated.items()}
        for _round in range(cls.MAXIMUM_ITERATIONS):
            calibration = cls(factors)
            rated = {
                application: StandardizedSeasonalCop.of(heatpump, application, calibration) for application in stated
            }
            if all(abs(rated[application] - value) < cls.TOLERANCE for application, value in stated.items()):
                return calibration
            factors = {application: factors[application] * value / rated[application] for application, value in stated.items()}
        raise ValueError(
            f"the SCOP calibration to {({a.value: v for a, v in stated.items()})} did not converge in "
            f"{cls.MAXIMUM_ITERATIONS} rounds (last ratings {({a.value: round(r, 4) for a, r in rated.items()})})."
        )

    def factor(self, outlet_temperature: float) -> float:
        """Return the factor for one outlet temperature, °C."""
        if not self.factors:
            return 1.0
        if len(self.factors) == 1:
            return next(iter(self.factors.values()))
        low = StandardizedSeasonalCop.mean_outlet_temperature(ScopApplication.W35)
        high = StandardizedSeasonalCop.mean_outlet_temperature(ScopApplication.W55)
        share = min(max((outlet_temperature - low) / (high - low), 0.0), 1.0)
        return self.factors[ScopApplication.W35] + share * (
            self.factors[ScopApplication.W55] - self.factors[ScopApplication.W35]
        )

    def apply(self, heatpump: Any, results: Dict[str, Any], mode: int) -> Dict[str, Any]:
        """Return hplib's results for one call with the calibration applied (a new dictionary)."""
        if not self.factors or mode != 1:
            return results
        cop = float(results["COP"])
        if cop <= 1:
            return results
        factor = self.factor(float(results["T_out"]))
        p_th = float(results["P_th"])
        p_el = float(results["P_el"])
        rod = float(heatpump.p_th_ref)
        compressor = float(heatpump.p_el_ref)
        if abs(p_el - (compressor + rod)) < 1e-6 * max(p_el, 1.0):
            # hplib's compressor-plus-rod branch: only the compressor's share is calibrated.
            calibrated_el = compressor / factor + rod
        else:
            calibrated_el = p_el / factor
        calibrated = dict(results)
        calibrated["P_el"] = calibrated_el
        calibrated["COP"] = p_th / calibrated_el
        return calibrated


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
    #: The unit's standardised SCOP as its datasheet states it (EN 14825, average climate) for the
    #: low- (W35) and medium-temperature (W55) application. Either, both or neither: a stated one
    #: calibrates hplib's fit to it (:class:`ScopCalibration`); unset keeps the fit as hplib ships it.
    #: Air/water and brine/water only; above 1, at most 10, and W55 not above W35.
    standardized_scop_en14825_w35: Optional[float] = None
    standardized_scop_en14825_w55: Optional[float] = None
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
    set_thermal_output_power_in_watt: Sizable[float] = sized_field(rule=Size.HEATING_LOAD_IN_WATT)

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
    ThermalPowerIsConstantForDHW = "ThermalPowerIsConstantForDHW"  # true/false
    MaxThermalPowerValueForDHW = "MaxThermalPowerValueForDHW"  # max. Leistungswert
    TemperatureInputPrimary = "TemperatureInputPrimary"  # °C
    TemperatureInputSecondarySH = "TemperatureInputSecondarySH"  # °C
    TemperatureInputSecondaryDHW = "TemperatureInputSecondaryDHW"  # °C
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

        self.electrical_input_power_brine_pump_in_watt = config.electrical_input_power_brine_pump_in_watt
        if self.electrical_input_power_brine_pump_in_watt is None:
            self.electrical_input_power_brine_pump_in_watt = 0.0

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
        self.scop_calibration = self.calibration_of(config, self.heatpump)

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
                field_name=self.TemperatureInputSecondaryDHW,
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
            self.set_temperature_hp_sh: ComponentInput = self.add_input(
                self.component_name,
                self.SetHeatingTemperatureSH,
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
            output_description="Electricity input power for SH in Watt",
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
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            output_description="Temperature Input SH in °C",
        )

        self.t_out_sh: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TemperatureOutputSH,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            output_description="Temperature Output SH in °C",
        )

        self.m_dot_sh: ComponentOutput = self.add_output(
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
                component_class.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    #: The largest standardised SCOP a configuration may state.
    MAXIMUM_STANDARDIZED_SCOP: ClassVar[float] = 10.0

    @classmethod
    def calibration_of(cls, config: MoreAdvancedHeatPumpHPLibConfig, heatpump: Any) -> ScopCalibration:
        """Return the SCOP calibration a configuration asks for, refusing an impossible one.

        Raises:
            ValueError: When a stated SCOP is not above 1 or above :attr:`MAXIMUM_STANDARDIZED_SCOP`,
                when the W55 rating exceeds the W35 one, or when the machine is neither air/water
                nor brine/water, the two groups EN 14825's rating here covers.
        """
        w35, w55 = config.standardized_scop_en14825_w35, config.standardized_scop_en14825_w55
        given = {"standardized_scop_en14825_w35": w35, "standardized_scop_en14825_w55": w55}
        stated: Dict[str, float] = {name: value for name, value in given.items() if value is not None}
        if not stated:
            return ScopCalibration({})
        for name, value in stated.items():
            if not 1.0 < value <= cls.MAXIMUM_STANDARDIZED_SCOP:
                raise ValueError(
                    f"{config.component_id}: {name} is {value}; a standardised SCOP is above 1 and at "
                    f"most {cls.MAXIMUM_STANDARDIZED_SCOP}."
                )
        if w35 is not None and w55 is not None and w55 > w35:
            raise ValueError(
                f"{config.component_id}: the W55 SCOP {w55} is above the W35 SCOP {w35}; a unit rated for "
                "55 °C water cannot outperform its 35 °C rating."
            )
        if heatpump.group_id not in (1, 2):
            raise ValueError(
                f"{config.component_id}: a standardised SCOP calibrates air/water (group 1) and brine/water "
                f"(group 2) machines only, and this one is hplib group {heatpump.group_id}."
            )
        return ScopCalibration.of(heatpump, w35, w55)

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
                PositionHotWaterStorageInSystemSetup.SERIES,
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
            if self.specific_heat_capacity_of_primary_fluid is not None:
                specific_heat_capacity_of_primary_fluid = self.specific_heat_capacity_of_primary_fluid
            else:
                raise ValueError("specific heat capacity on primary side has to be a value not none!")

            temperature_difference_primary_side = 5.0
            m_dot_water_primary = thermal_power_from_environment / (specific_heat_capacity_of_primary_fluid *
                                                                    temperature_difference_primary_side)

            if on_off == 0:
                temperature_difference_primary_side = 0.0
                m_dot_water_primary = 0.0

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
            # The stated SCOP's calibration (hisim-4g9.15), applied once before the result is cached.
            results = self.scop_calibration.apply(self.heatpump, results, mode)

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
class CalculationRequest:
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
    """Configuration of the hplib heat pump's space-heating controller.

    The on/off logic in front of the machine's space-heating side: it compares the buffer
    vessel's water temperature with the flow temperature the heat distribution system asks
    for, and it stops heating once the daily average outside temperature has risen above
    the heating threshold. The named default is :meth:`preset_standard`; the two fields
    that belong to the emitter circuit rather than to the machine --
    :attr:`heat_distribution_system_type` and
    :attr:`set_heating_threshold_outside_temperature_in_celsius` -- are sizable, so the
    preset leaves them ``AUTO`` and ``.resolve(ctx)`` copies them from the heat
    distribution controller's facts::

        MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard(
            "MoreAdvancedHeatPumpHPLibControllerSH"
        ).resolve(
            SizingContext(
                heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
                set_heating_threshold_outside_temperature_in_celsius=18.0,
            )
        )

    Copying rather than restating is the point: the emitter circuit already decides which
    emitter it feeds and above which outside temperature nothing heats, and a generator
    controller that disagreed with it would heat into a circuit that has switched off.
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

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig":
        """The one space-heating controller the fleet runs, taking its limits from the emitter circuit.

        The field defaults are that controller: the plain on/off law, a five-kelvin
        hysteresis band either side of the requested flow temperature, and no cooling below
        20 °C outside. What the preset does not fix is the emitter type and the heating
        threshold, which stay ``AUTO`` so that both are copied from the heat distribution
        controller instead of repeating its choices here.

        Args:
            name: Instance name of the controller in the simulation.

        Returns:
            The configuration, with its two sizable fields still ``AUTO``.
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
    #: set thermal power delivered for dhw on constant value --> max. Value of heatpump.
    #: false: modulation, true: constant power for dhw
    thermalpower_dhw_is_constant: bool = False
    #: max. Power of Heatpump for not modulation dhw production; only read when
    #: ``thermalpower_dhw_is_constant`` is true
    p_th_max_dhw_in_watt: float = 5000.0

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MoreAdvancedHeatPumpHPLibControllerDHWConfig":
        """The one hot-water controller the fleet runs, reheating the vessel from 40 to 60 °C.

        The field defaults are that controller: a modulating machine, so the constant-power
        limit below is not read, and the 40/60 °C band that keeps the vessel above the
        legionella temperature without cycling the machine on every tap.

        Args:
            name: Instance name of the controller in the simulation.

        Returns:
            The configuration, fully concrete -- the class has no sizable field.
        """
        return cls(component_id=ComponentID(name=name))


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
                component_class.WaterTemperatureToHeatGenerator,
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

        self.state_dhw = 0
        self.water_temperature_input_from_dhw_storage_in_celsius = 40.0
        self.thermalpower_dhw_is_constant = self.config.thermalpower_dhw_is_constant
        self.p_th_max_dhw = self.config.p_th_max_dhw_in_watt

        if self.thermalpower_dhw_is_constant:
            print(f"INFO: DHW Power is constant with {self.p_th_max_dhw} Watt.")
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
