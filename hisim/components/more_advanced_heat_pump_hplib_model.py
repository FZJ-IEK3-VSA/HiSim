"""The hplib heat pump's model parts: the COP law, the adapter to hplib, the backup rod, the SCOP calibration, one step.

``MoreAdvancedHeatPumpHPLib`` in ``hisim/components/more_advanced_heat_pump_hplib.py`` is the component; this module
holds what it computes with and that needs no component: the Carnot-efficiency COP law (:class:`CarnotEfficiencyCop`)
and the machine that combines it with hplib's thermal power (:class:`CarnotCopHeatPump`), the backup heating rod that
covers the compressor's shortfall (:class:`BackupHeatingRod`, :class:`RodAssistedHeating`), hplib's result as a typed
value (:class:`HplibResult`) and its cache key (:class:`CalculationRequest`), the EN 14825 rating and the calibration
to a datasheet SCOP (:class:`StandardizedSeasonalCop`, :class:`ScopCalibration`), and the frozen values one step of the
heat pump passes between its pure functions (:class:`HeatPumpOperation` and its companions).
"""

import math
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Callable, ClassVar, Dict, Mapping, Optional, Tuple

import numpy as np

from hisim import hydronics
from hisim.subcomponents.base import Subcomponent


@unique
class ScopApplication(str, Enum):
    """The two EN 14825 applications a datasheet rates a heat pump's SCOP for."""

    W35 = "W35"
    W55 = "W55"


@unique
class HeatSourceType(str, Enum):
    """The kind of heat source a heat pump draws from, which picks the parameters of its COP law.

    Air for an air/water machine, brine for a brine/water (ground-source) machine. A water/water machine takes the
    brine parameters (see :attr:`CarnotEfficiencyCop.SOURCE_TYPE_OF_HPLIB_GROUP`).
    """

    AIR = "air"
    BRINE = "brine"


class CarnotEfficiencyCop:
    """The heat pump's coefficient of performance of heating as a constant share of the Carnot COP.

    The law, with every temperature in °C and the lift in K::

        cop = eta * (outlet + 273.15) / (max(outlet - source, MINIMAL_LIFT_IN_KELVIN) + d)

    ``eta`` (:attr:`CARNOT_EFFICIENCY`) is the share of the ideal (Carnot) COP the machine reaches; ``d``
    (:attr:`TEMPERATURE_OFFSET_IN_KELVIN`) adds the temperature differences in the condenser and the evaporator to
    the lift between the water's outlet and the source. This is the constant-Carnot-effectiveness model of the IBPSA
    and AixLib Modelica libraries and the principle of EN 15316-4-2. For example, an air/water machine at A-7/W52
    gets ``0.451 * 325.15 / (59 + 4.45) = 2.31``, at A7/W35 ``4.28``.

    Source of ``eta`` and ``d``: one pooled least-squares fit per source type of the relative error over the ten
    EN 14825 / EN 14511 rating points (A-7/W34, A2/W30, A7/W27, A12/W24, A-7/W52, A2/W42, A7/W36, A12/W30 at part
    load; A7/W35, A7/W55 at full load) of the Keymark-certified units in hplib's raw Keymark exports, 1,743 air/water
    and 176 brine/water units (brine at 0 °C), evaluated in hisim-x2mu and hisim-heat-pump-model-rework-fpqz.1
    (owner decision (2) of 2026-10-21; spec ``roadmap/heat_pump_model_rework_spec.md``). Calibrated to each unit's
    own SCOP pair (:class:`ScopCalibration`), the law misses the certified points by 7.7 % (air) and 9.6 % (brine) on
    average, where hplib's Generic plane missed by 12.2 % and 10.0 %; at A-7/W52 by +12 % instead of -33 %.

    The law grows without bound as the lift shrinks and turns negative where the source is warmer than the outlet
    (for example air at 30 °C and a 25 °C outlet). The lift is therefore held at no less than
    :attr:`MINIMAL_LIFT_IN_KELVIN`, the smallest lift a certified rating point has (A12/W24, 12 K): below it the law
    is not evidenced, and holding the lift keeps the COP at its value at the edge of the data (air: 8.15 at W24)
    instead of extrapolating towards infinity.
    """

    #: The share of the Carnot COP the machine reaches, dimensionless, per source type: the pooled Keymark fit
    #: (1,743 air/water units: 0.4515; 176 brine/water units: 0.5677).
    CARNOT_EFFICIENCY: ClassVar[Dict[HeatSourceType, float]] = {HeatSourceType.AIR: 0.451, HeatSourceType.BRINE: 0.568}

    #: The temperature differences of the condenser and the evaporator added to the lift, in K, per source type: the
    #: same fit (air 4.454 K, brine 4.498 K).
    TEMPERATURE_OFFSET_IN_KELVIN: ClassVar[Dict[HeatSourceType, float]] = {
        HeatSourceType.AIR: 4.45,
        HeatSourceType.BRINE: 4.50,
    }

    #: The smallest lift between outlet and source the law is evaluated at, in K: the lift of the EN 14825 A12/W24
    #: point, the smallest of any certified rating point in the fit.
    MINIMAL_LIFT_IN_KELVIN: ClassVar[float] = 12.0

    #: The offset between °C and K.
    ZERO_CELSIUS_IN_KELVIN: ClassVar[float] = 273.15

    #: The source type of each hplib group: 1 and 4 air/water (regulated, on/off), 2 and 5 brine/water, 3 and 6
    #: water/water. The Keymark data hold no water/water units; a water source is a liquid one like brine, so it
    #: takes the brine parameters.
    SOURCE_TYPE_OF_HPLIB_GROUP: ClassVar[Dict[int, HeatSourceType]] = {
        1: HeatSourceType.AIR,
        4: HeatSourceType.AIR,
        2: HeatSourceType.BRINE,
        5: HeatSourceType.BRINE,
        3: HeatSourceType.BRINE,
        6: HeatSourceType.BRINE,
    }

    @classmethod
    def cop(
        cls, source_type: HeatSourceType, source_temperature_in_celsius: float, outlet_temperature_in_celsius: float
    ) -> float:
        """Return the COP of heating at one source and one outlet temperature, dimensionless.

        For example, ``cop(HeatSourceType.AIR, -7.0, 52.0)`` is 2.31 and ``cop(HeatSourceType.BRINE, 0.0, 35.0)``
        4.43; ``cop(HeatSourceType.AIR, 30.0, 25.0)`` holds the lift at 12 K and gives 8.17.

        Args:
            source_type: The kind of heat source, which picks ``eta`` and ``d``.
            source_temperature_in_celsius: The temperature of the source the machine draws from (the outdoor air,
                the brine or the well water), in °C.
            outlet_temperature_in_celsius: The temperature of the water leaving the machine, in °C.

        Returns:
            The coefficient of performance.
        """
        lift_in_kelvin = max(outlet_temperature_in_celsius - source_temperature_in_celsius, cls.MINIMAL_LIFT_IN_KELVIN)
        return (
            cls.CARNOT_EFFICIENCY[source_type]
            * (outlet_temperature_in_celsius + cls.ZERO_CELSIUS_IN_KELVIN)
            / (lift_in_kelvin + cls.TEMPERATURE_OFFSET_IN_KELVIN[source_type])
        )

    @classmethod
    def source_type_of(cls, hplib_group: float) -> HeatSourceType:
        """Return the source type of an hplib group (1 to 6).

        For example, group 1 (regulated air/water) gives :attr:`HeatSourceType.AIR` and group 3 (water/water)
        :attr:`HeatSourceType.BRINE`.

        Args:
            hplib_group: hplib's group of the machine.

        Returns:
            The source type whose parameters the machine's COP law takes.

        Raises:
            ValueError: For a group hplib does not define.
        """
        group = int(hplib_group)
        if group not in cls.SOURCE_TYPE_OF_HPLIB_GROUP:
            raise ValueError(f"hplib defines groups 1 to 6, and this heat pump is hplib group {hplib_group:g}.")
        return cls.SOURCE_TYPE_OF_HPLIB_GROUP[group]


class CarnotCopHeatPump(Subcomponent):
    """An hplib heat pump's compressor whose COP of heating is the Carnot-efficiency law, without hplib's heating rod.

    hplib gives a heat pump's thermal power, outlet and mass flow from fitted planes; this machine keeps those and
    replaces hplib's COP plane by :class:`CarnotEfficiencyCop` (step (a) of the heat pump rework, spec
    ``roadmap/heat_pump_model_rework_spec.md``). Its :meth:`simulate` answers like ``hplib.HeatPump.simulate``, so the
    EN 14825 rating and the SCOP calibration read it as they read hplib:

    * The thermal power is hplib's compressor capacity, ``P_el,fit * COP_fit`` of hplib's own planes, raised to the
      minimal thermal power where hplib's regulated compressor can reach it (hplib's own rule).
    * The COP is the Carnot-efficiency law at the source and hplib's outlet, and the electrical power is the thermal
      power over it; the mass flow carries the thermal power over hplib's lift, as hplib computes it.
    * hplib's heating rod is gone from the machine's answer. hplib runs its rod alone where its own COP plane falls to
      1 (for an 8 kW Generic air/water machine at A-7/W56.7 and A0/W69.0, booking its 8.1 kW reference power at COP
      1), and adds the rod to the compressor where the compressor alone cannot reach the minimal power. Here the first
      is the compressor at hplib's switch, ``P_el,fit`` (continuous there, so the 3.3 kW jump of hplib's switch is
      gone; decision 12 of the spec), and the second the compressor at its maximum, ``P_el,ref * COP_fit``. The
      outlet of hplib's switch stays as the compressor's maximal outlet
      (:meth:`maximal_outlet_temperature_in_celsius`), above which the heat pump's backup heating rod
      (:class:`BackupHeatingRod`, decision 8) adds the heat the compressor cannot.
    * Cooling (mode 2) is hplib's own answer.

    Attributes:
        hplib_heat_pump: The wrapped ``hplib.HeatPump``.
        source_type: The kind of source, from hplib's group.
    """

    #: The hplib groups whose compressor modulates (1 air, 2 brine, 3 water); 4 to 6 are on/off machines.
    REGULATED_HPLIB_GROUPS: ClassVar[Tuple[int, ...]] = (1, 2, 3)

    #: hplib's floor of the compressor's electricity, as a share of its electricity at the floor's outdoor
    #: temperature, dimensionless; regulated groups only.
    HPLIB_ELECTRICAL_FLOOR_SHARE: ClassVar[float] = 0.25

    #: The outdoor temperature hplib evaluates that floor at (air: as the source, brine: as the ambient), in °C.
    HPLIB_ELECTRICAL_FLOOR_OUTDOOR_TEMPERATURE_IN_CELSIUS: ClassVar[float] = -7.0

    #: The coefficient of performance of hplib's own COP plane at and below which hplib runs its heating rod alone,
    #: dimensionless: hplib then books its reference thermal power as both heat and electricity.
    HPLIB_HEATING_ROD_ALONE_MAXIMAL_COP: ClassVar[float] = 1.0

    def __init__(self, hplib_heat_pump: Any) -> None:
        """Wrap an ``hplib.HeatPump``; its group picks the source type of the COP law."""
        self.hplib_heat_pump = hplib_heat_pump
        self.source_type = CarnotEfficiencyCop.source_type_of(hplib_heat_pump.group_id)

    @property
    def group_id(self) -> float:
        """Return hplib's group of the machine, 1 to 6."""
        return float(self.hplib_heat_pump.group_id)

    @property
    def p_th_ref(self) -> float:
        """Return hplib's reference thermal power, in W."""
        return float(self.hplib_heat_pump.p_th_ref)

    @property
    def p_el_ref(self) -> float:
        """Return hplib's reference electrical power, in W: the regulated compressor's maximum."""
        return float(self.hplib_heat_pump.p_el_ref)

    @property
    def delta_t(self) -> float:
        """Return the lift of the water across the machine that hplib computes the outlet and the flow with, in K."""
        return float(self.hplib_heat_pump.delta_t)

    @delta_t.setter
    def delta_t(self, lift_in_kelvin: float) -> None:
        """Set the lift of the water across the machine, in K, on the wrapped hplib heat pump."""
        self.hplib_heat_pump.delta_t = lift_in_kelvin

    def fitted_electrical_power_in_watt(
        self,
        source_temperature_in_celsius: float,
        outlet_temperature_in_celsius: float,
        ambient_temperature_in_celsius: float,
    ) -> float:
        """Return the compressor's electricity from hplib's fitted plane, with hplib's floor, in W.

        The same arithmetic as ``hplib.HeatPump.simulate`` in heating: ``P_el,ref * (p1 T_in + p2 T_out + p3 + p4
        T_amb)``, with the ambient equal to the source for an air machine, and for a regulated machine at least a
        quarter of that plane at -7 °C (air: source and ambient, brine: ambient). It is needed where hplib runs its
        rod alone, because hplib then reports the rod's power instead of the compressor's. For example, the 8 kW
        Generic air/water machine draws about 4.8 kW at 0 °C outside and a 69 °C outlet, hplib's switch.

        Args:
            source_temperature_in_celsius: The source temperature, in °C.
            outlet_temperature_in_celsius: The outlet temperature, in °C.
            ambient_temperature_in_celsius: The outdoor temperature, in °C; an air machine reads the source instead.

        Returns:
            The compressor's electrical power, in W.
        """
        heat_pump = self.hplib_heat_pump
        group = int(heat_pump.group_id)
        ambient_in_celsius = source_temperature_in_celsius if group in (1, 4) else ambient_temperature_in_celsius

        def plane(source_in_celsius: float, outdoor_in_celsius: float) -> float:
            """Return hplib's electrical plane at one source and outdoor temperature, in W."""
            return float(heat_pump.p_el_ref) * float(
                heat_pump.p1_p_el_h * source_in_celsius
                + heat_pump.p2_p_el_h * outlet_temperature_in_celsius
                + heat_pump.p3_p_el_h
                + heat_pump.p4_p_el_h * outdoor_in_celsius
            )

        electrical_power_in_watt = plane(source_temperature_in_celsius, ambient_in_celsius)
        if group not in self.REGULATED_HPLIB_GROUPS:
            return electrical_power_in_watt
        floor_in_celsius = self.HPLIB_ELECTRICAL_FLOOR_OUTDOOR_TEMPERATURE_IN_CELSIUS
        if group == 1:
            floor_in_watt = plane(floor_in_celsius, floor_in_celsius)
        elif group == 2:
            floor_in_watt = plane(source_temperature_in_celsius, floor_in_celsius)
        else:
            floor_in_watt = plane(source_temperature_in_celsius, ambient_in_celsius)
        return max(electrical_power_in_watt, self.HPLIB_ELECTRICAL_FLOOR_SHARE * floor_in_watt)

    def maximal_outlet_temperature_in_celsius(
        self, source_temperature_in_celsius: float, ambient_temperature_in_celsius: float
    ) -> float:
        """Return the highest outlet the compressor reaches at a source temperature, in °C: hplib's COP-1 line.

        hplib's COP plane ``p1 T_in + p2 T_out + p3 + p4 T_amb`` (the ambient equal to the source for an air machine)
        falls with the outlet; where it reaches :attr:`HPLIB_HEATING_ROD_ALONE_MAXIMAL_COP`, hplib switched to its
        heating rod alone. That outlet stands in for the compressor's maximal outlet until the configured maximal
        supply temperature of bead hisim-heat-pump-outlet-limit-2vq7 replaces it (spec
        ``roadmap/heat_pump_model_rework_spec.md``, §3.1, decision 8). For example, the 8 kW Generic air/water machine
        reaches 56.7 °C at -7 °C outside, 69.0 °C at 0 °C and 81.4 °C at 7 °C; the Generic brine/water machine about
        72.3 °C at a 0 °C brine. A plane that does not fall with the outlet has no such line, and the limit is
        infinite.

        Args:
            source_temperature_in_celsius: The source temperature, in °C.
            ambient_temperature_in_celsius: The outdoor temperature, in °C; an air machine reads the source instead.

        Returns:
            The compressor's maximal outlet temperature, in °C.
        """
        heat_pump = self.hplib_heat_pump
        group = int(heat_pump.group_id)
        ambient_in_celsius = source_temperature_in_celsius if group in (1, 4) else ambient_temperature_in_celsius
        outlet_slope_per_kelvin = float(heat_pump.p2_cop)
        if outlet_slope_per_kelvin >= 0.0:
            return math.inf
        return (
            self.HPLIB_HEATING_ROD_ALONE_MAXIMAL_COP
            - float(heat_pump.p1_cop) * source_temperature_in_celsius
            - float(heat_pump.p3_cop)
            - float(heat_pump.p4_cop) * ambient_in_celsius
        ) / outlet_slope_per_kelvin

    def simulate(
        self, t_in_primary: float, t_in_secondary: float, t_amb: float, mode: int = 1, p_th_min: float = 0.0
    ) -> Dict[str, Any]:
        """Return the machine's answer at one operating point, with the keys and units of ``hplib.HeatPump.simulate``.

        For example, an 8 kW Generic air/water machine at 2 °C outside and a 25 °C return (outlet 30 °C) keeps
        hplib's 7.87 kW and gets the Carnot COP 4.21 instead of hplib's 4.73, so it draws 1.87 kW instead of 1.67 kW.

        Args:
            t_in_primary: The source temperature, in °C (outdoor air, brine or well water).
            t_in_secondary: The return temperature of the water, in °C; the outlet is it plus :attr:`delta_t`.
            t_amb: The outdoor temperature, in °C.
            mode: 1 heating, 2 cooling.
            p_th_min: The lowest thermal power the machine modulates down to, in W.

        Returns:
            ``T_in``, ``T_out``, ``T_amb``, ``COP``, ``EER``, ``P_el``, ``P_th`` and ``m_dot`` as hplib names them.
        """
        results: Dict[str, Any] = dict(
            self.hplib_heat_pump.simulate(
                t_in_primary=t_in_primary, t_in_secondary=t_in_secondary, t_amb=t_amb, mode=mode, p_th_min=0.0
            )
        )
        if mode != 1:
            return results
        outlet_in_celsius = float(results["T_out"])
        hplib_cop = float(results["COP"])
        if hplib_cop <= self.HPLIB_HEATING_ROD_ALONE_MAXIMAL_COP:
            # Where hplib runs its rod alone, the compressor continues as at hplib's switch, its plane's COP at 1.
            compressor_cop = 1.0
            thermal_power_in_watt = self.fitted_electrical_power_in_watt(t_in_primary, outlet_in_celsius, t_amb)
        else:
            compressor_cop = hplib_cop
            thermal_power_in_watt = float(results["P_th"])
        if thermal_power_in_watt < p_th_min and int(self.group_id) in self.REGULATED_HPLIB_GROUPS:
            # hplib's rule: the compressor draws more to reach the minimum, as far as its maximum allows.
            thermal_power_in_watt = min(p_th_min, self.p_el_ref * compressor_cop)
        cop = CarnotEfficiencyCop.cop(self.source_type, t_in_primary, outlet_in_celsius)
        results["COP"] = cop
        results["P_th"] = thermal_power_in_watt
        results["P_el"] = thermal_power_in_watt / cop
        results["m_dot"] = abs(thermal_power_in_watt / (self.delta_t * float(self.hplib_heat_pump.cp)))
        return results


@dataclass(frozen=True)
class RodAssistedHeating:
    """One heating circuit's step shared between the compressor and the backup heating rod.

    The compressor delivers what it can at its COP, the rod adds the missing heat at a COP of 1, and the circuit's water
    carries both. The flow and outlet are what the circuit publishes: the averaged flow (the full-load flow times the
    part-load ratio) at the outlet that flow reaches with the delivered heat. For example, a whole step of 0.4 kg/s from
    60 °C to 65 °C whose compressor stops at 62 °C delivers 3344 W from the compressor and 5016 W from the rod, at
    0.4 kg/s and 65 °C. A step that runs nothing has no flow, no heat and no electricity, its outlet is its return and
    its COP 0.
    """

    #: The heat the compressor delivers, in W.
    compressor_thermal_power_in_watt: float
    #: The electricity the compressor draws for it, in W.
    compressor_electrical_power_in_watt: float
    #: The electricity the backup heating rod draws, in W; it is also the rod's heat (COP 1).
    heating_rod_electrical_power_in_watt: float
    #: The fraction of the step the compressor runs at its full output, from 0 to 1: the brine pump runs with it.
    compressor_running_share: float
    #: The circuit's averaged mass flow, in kg/s.
    mass_flow_in_kg_per_second: float
    #: The circuit's outlet temperature, in °C.
    outlet_temperature_in_celsius: float

    @property
    def thermal_power_in_watt(self) -> float:
        """Return the heat the circuit's water carries, the compressor's and the rod's, in W."""
        return self.compressor_thermal_power_in_watt + self.heating_rod_electrical_power_in_watt

    @property
    def electrical_power_in_watt(self) -> float:
        """Return the electricity of the compressor and the rod, in W."""
        return self.compressor_electrical_power_in_watt + self.heating_rod_electrical_power_in_watt

    @property
    def cop(self) -> float:
        """Return the circuit's coefficient of performance with the rod, heat over electricity; 0 without electricity.

        For example, 3344 W from the compressor at a COP of 2.5 and 5016 W from the rod give 8360 W for 6353.6 W, a COP
        of 1.32.
        """
        electrical_power_in_watt = self.electrical_power_in_watt
        if electrical_power_in_watt <= 0.0:
            return 0.0
        return self.thermal_power_in_watt / electrical_power_in_watt


@dataclass(frozen=True)
class BackupHeatingRod(Subcomponent):
    """The electric backup heating rod inside the heat pump: it adds the heat the compressor cannot, at a COP of 1.

    Real air/water and brine/water units ship with an electric heating element for the coldest days (decision 8 of
    ``roadmap/heat_pump_model_rework_spec.md``). The rod heats the same water as the compressor and runs only where the
    compressor falls short, in one of two cases:

    1. the heat requested in the step exceeds the compressor's thermal capacity at the current source and outlet
       temperature (hplib's thermal power until step d of the spec replaces the capacity surface);
    2. the required outlet exceeds the compressor's maximal outlet
       (:meth:`CarnotCopHeatPump.maximal_outlet_temperature_in_celsius`): the compressor lifts the circuit's flow to
       its maximal outlet, and the rod lifts it on to the required outlet, ``m c (T_required - T_max)``.

    The decision is one of energy over the whole step (:meth:`heating`): the compressor runs at its full output for as
    much of the step as the request needs, and the rod covers only what the compressor cannot deliver in the whole step,
    ``E_rod = min(Q_requested - Q_compressor,max, P_rod * dt)``. What the rod cannot cover either is not delivered. Where
    the compressor copes, the rod does not run. Its electricity is the heat pump's output ``ElectricalInputPowerHeatingRod``
    with its own energy port; the rod is no part of the EN 14825 rating or the SCOP calibration.

    Source of the default rating (:attr:`DEFAULT_LARGEST_RATED_ELECTRICAL_POWER_IN_WATT`, spec §3.1): the residential
    indoor units of the main manufacturers carry one element class of 6 or 9 kW, switchable in 3 kW stages by the
    installer (Vaillant uniTOWER plus / VWZ MEH 97: 9 kW; Viessmann Vitocal 250-A: 9 kW in 3/6/9 kW stages; Daikin
    Altherma 3: 3, 6 or 9 kW; Bosch Compress 7000i AW: 9 kW; Stiebel Eltron HPA-O and WPL: 8.8 kW), and the installer's
    stage setting keeps the rod below the compressor's rated heat; the heat pump's sizing law takes the smaller of the
    two.

    Attributes:
        rated_electrical_power_in_watt: The rod's rated electrical power, in W, 0 or more; 0 is a heat pump without one.
    """

    rated_electrical_power_in_watt: float

    #: The rod's rated electrical power where the heat pump's rated thermal power is larger, in W: the 9 kW element
    #: class of the residential indoor units (see the class docstring).
    DEFAULT_LARGEST_RATED_ELECTRICAL_POWER_IN_WATT: ClassVar[float] = 9000.0

    def __post_init__(self) -> None:
        """Refuse a rated power that is negative or not finite, which would book negative or infinite electricity.

        Raises:
            ValueError: If the rated power is negative, infinite or not a number.
        """
        if not math.isfinite(self.rated_electrical_power_in_watt) or self.rated_electrical_power_in_watt < 0.0:
            raise ValueError(
                "The backup heating rod's rated electrical power must be a finite number of watts, 0 or more, got "
                f"{self.rated_electrical_power_in_watt}."
            )

    def heating(
        self,
        *,
        part_load_ratio: float,
        mass_flow_in_kg_per_second: float,
        return_temperature_in_celsius: float,
        required_outlet_temperature_in_celsius: float,
        compressor_thermal_capacity_in_watt: float,
        compressor_maximal_outlet_temperature_in_celsius: float,
        compressor_cop: float,
    ) -> RodAssistedHeating:
        """Return a heating circuit's step shared between the compressor and the rod.

        All powers are averages over the step. The request is the part-load ratio's share of the full-load heat at the
        required outlet, ``P_req = r m c (T_required - T_return)``. The compressor's most over the whole step is the
        smaller of its capacity and what its flow carries up to its maximal outlet, ``P_cmax = min(P_cap, m c
        (min(T_max, T_required) - T_return))``, at least 0. The compressor delivers ``min(P_req, P_cmax)`` and runs that
        share of the step at full output; the rod adds ``min(P_req - P_compressor, P_rod)``. The circuit publishes the
        averaged flow ``r m`` at the required outlet, or, where the rod cannot cover the rest, at the lower outlet that
        flow reaches with the delivered heat.

        For example, 0.4 kg/s from a 60 °C return to a 65 °C required outlet (8360 W), a compressor of 8 kW capacity
        whose maximal outlet is 62 °C at a COP of 2.5, and a 9 kW rod: at ratio 1 the compressor delivers 3344 W for
        1337.6 W and the rod 5016 W; at ratio 0.4 the request of 3344 W is what the compressor delivers over the whole
        step at its smaller lift, so the rod stays off, at 0.16 kg/s and 65 °C. With a 2 kW rod at ratio 1 the circuit
        gets 5344 W and an outlet of 63.2 °C.

        Args:
            part_load_ratio: The fraction of the step the request asks for at full load, from 0 to 1.
            mass_flow_in_kg_per_second: The circuit's flow at full load, in kg/s.
            return_temperature_in_celsius: The circuit's return temperature, in °C.
            required_outlet_temperature_in_celsius: The outlet the step asks for, in °C.
            compressor_thermal_capacity_in_watt: The compressor's thermal capacity at the step's temperatures, in W.
            compressor_maximal_outlet_temperature_in_celsius: The compressor's maximal outlet, in °C.
            compressor_cop: The compressor's COP at its own outlet, the lower of the required and the maximal outlet,
                dimensionless; above 0 wherever the compressor delivers heat.

        Returns:
            The step's heat, electricity, flow and outlet.

        Raises:
            ValueError: If the compressor delivers heat at a COP of 0 or less, which would book infinite or negative
                electricity.
        """
        heat_per_kelvin_in_watt_per_kelvin = mass_flow_in_kg_per_second * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K
        full_load_heat_in_watt = heat_per_kelvin_in_watt_per_kelvin * max(
            required_outlet_temperature_in_celsius - return_temperature_in_celsius, 0.0
        )
        requested_heat_in_watt = part_load_ratio * full_load_heat_in_watt
        mass_flow_averaged_in_kg_per_second = part_load_ratio * mass_flow_in_kg_per_second
        if requested_heat_in_watt <= 0.0:
            # Nothing requested: the step runs nothing (ratio 0), or its water passes at no lift.
            return RodAssistedHeating(
                compressor_thermal_power_in_watt=0.0,
                compressor_electrical_power_in_watt=0.0,
                heating_rod_electrical_power_in_watt=0.0,
                compressor_running_share=0.0,
                mass_flow_in_kg_per_second=mass_flow_averaged_in_kg_per_second,
                outlet_temperature_in_celsius=return_temperature_in_celsius,
            )
        compressor_maximal_heat_in_watt = max(
            min(
                compressor_thermal_capacity_in_watt,
                heat_per_kelvin_in_watt_per_kelvin
                * (
                    min(compressor_maximal_outlet_temperature_in_celsius, required_outlet_temperature_in_celsius)
                    - return_temperature_in_celsius
                ),
            ),
            0.0,
        )
        compressor_heat_in_watt = min(requested_heat_in_watt, compressor_maximal_heat_in_watt)
        rod_heat_in_watt = min(requested_heat_in_watt - compressor_heat_in_watt, self.rated_electrical_power_in_watt)
        if compressor_heat_in_watt + rod_heat_in_watt < requested_heat_in_watt:
            outlet_in_celsius = return_temperature_in_celsius + (compressor_heat_in_watt + rod_heat_in_watt) / (
                mass_flow_averaged_in_kg_per_second * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K
            )
        else:
            outlet_in_celsius = required_outlet_temperature_in_celsius
        # The heat the published flow carries, so the booked heat is exactly the flow's; the compressor books the rest.
        carried_heat_in_watt = hydronics.circuit_power_w(
            mass_flow_kg_per_s=mass_flow_averaged_in_kg_per_second,
            t_supply_c=outlet_in_celsius,
            t_return_c=return_temperature_in_celsius,
        )
        compressor_heat_in_watt = max(carried_heat_in_watt - rod_heat_in_watt, 0.0)
        if compressor_heat_in_watt > 0.0 and not compressor_cop > 0.0:
            raise ValueError(
                f"A running compressor needs a coefficient of performance above 0 to book its electricity, got "
                f"{compressor_cop} for {compressor_heat_in_watt} W of heat."
            )
        return RodAssistedHeating(
            compressor_thermal_power_in_watt=compressor_heat_in_watt,
            compressor_electrical_power_in_watt=compressor_heat_in_watt / compressor_cop if compressor_heat_in_watt else 0.0,
            heating_rod_electrical_power_in_watt=rod_heat_in_watt,
            compressor_running_share=(
                min(compressor_heat_in_watt / compressor_maximal_heat_in_watt, 1.0)
                if compressor_maximal_heat_in_watt > 0.0
                else 0.0
            ),
            mass_flow_in_kg_per_second=mass_flow_averaged_in_kg_per_second,
            outlet_temperature_in_celsius=outlet_in_celsius,
        )


class StandardizedSeasonalCop(Subcomponent):
    """The seasonal COP of a heat pump by the bin method of EN 14825, average climate.

    What a manufacturer states on the datasheet or ErP fiche is a SCOP rated this way, so
    computing the same figure for the simulated machine (:class:`CarnotCopHeatPump`: hplib's thermal
    power, the Carnot-efficiency COP) is what lets it be calibrated to it (:class:`ScopCalibration`).
    The method, in the simplified form the calibration needs:

    * the heating season is :attr:`BINS`, one outdoor temperature per bin with its hours;
    * the building's demand falls linearly from the design load at :attr:`DESIGN_TEMPERATURE` to
      zero at :attr:`HEATING_LIMIT_TEMPERATURE`;
    * the design load is the machine's thermal output at A-7/W52 divided by the part load of
      A-7, :attr:`DESIGN_SIZING_PART_LOAD` (the machine covers A-7 exactly, as EN 14825's
      reference sizing has it);
    * the outlet temperature follows the application's line, :attr:`FLOW_LINES`, through two
      points and extended beyond them;
    * the machine modulates at the COP it has in the bin (hplib's own 25 % electrical floor is the
      only part-load limit it knows), and whatever the demand exceeds its output is met by an
      electric back-up heater at COP 1, as is a bin where the machine's COP is 1 or less.

    Source: EN 14825:2022, average climate (bins -10 to +15 °C, 4910 h) and the variable-outlet
    lines of its low- (W35) and medium-temperature (W55) applications. For a brine/water machine
    (hplib group 2) the brine enters at :attr:`BRINE_TEMPERATURE` in every bin, EN 14825's rating
    condition, while the ambient term still follows the bin. Any other hplib group is refused.

    Example::

        StandardizedSeasonalCop.of(heatpump, ScopApplication.W55)   # 3.33 for the air/water Carnot law
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

    #: Part load at A-7, (-7 - 16) / (-10 - 16) ≈ 0.885: the design load is the A-7 output over it.
    DESIGN_SIZING_PART_LOAD: ClassVar[float] = (SIZING_OUTDOOR_TEMPERATURE - HEATING_LIMIT_TEMPERATURE) / (
        DESIGN_TEMPERATURE - HEATING_LIMIT_TEMPERATURE
    )

    #: Brine temperature of every bin for a brine/water machine, °C.
    BRINE_TEMPERATURE: ClassVar[float] = 0.0

    #: hplib's secondary-side temperature rise: the outlet is the inlet plus this, K.
    SECONDARY_TEMPERATURE_RISE: ClassVar[float] = 5.0

    #: The hplib groups the rating covers, by the kind of machine each is.
    RATED_GROUPS: ClassVar[Dict[int, str]] = {1: "air/water", 2: "brine/water"}

    #: Outlet-temperature line per application, as two (outdoor °C, outlet °C) points.
    FLOW_LINES: ClassVar[Dict[ScopApplication, Tuple[Tuple[float, float], Tuple[float, float]]]] = {
        ScopApplication.W35: ((-7.0, 34.0), (12.0, 24.0)),
        ScopApplication.W55: ((-7.0, 52.0), (12.0, 30.0)),
    }

    @classmethod
    def flow_temperature(cls, application: ScopApplication, outdoor_temperature: float) -> float:
        """Return the application's outlet temperature at one outdoor temperature, °C."""
        (t_1, f_1), (t_2, f_2) = cls.FLOW_LINES[application]
        return f_1 + (f_2 - f_1) * (outdoor_temperature - t_1) / (t_2 - t_1)

    @classmethod
    def part_load(cls, outdoor_temperature: float) -> float:
        """Return the building's demand at one outdoor temperature as a share of the design load."""
        return (outdoor_temperature - cls.HEATING_LIMIT_TEMPERATURE) / (
            cls.DESIGN_TEMPERATURE - cls.HEATING_LIMIT_TEMPERATURE
        )

    @classmethod
    def mean_outlet_temperature(cls, application: ScopApplication) -> float:
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
        cls, heatpump: Any, application: ScopApplication, calibration: Optional["ScopCalibration"] = None
    ) -> float:
        """Return the seasonal COP of one heat pump for one application.

        Args:
            heatpump: A machine of hplib group 1 (air/water) or 2 (brine/water) that answers
                ``simulate`` as ``hplib.HeatPump`` does, in HiSim a :class:`CarnotCopHeatPump`; it is
                only read, through pure ``simulate`` calls.
            application: The rating's application, which picks the outlet-temperature line.
            calibration: Applied to every call when given, so the calibrated machine is rated.

        Returns:
            Heat delivered over electricity drawn across the season.

        Raises:
            ValueError: When the machine is of any other hplib group, for which the bins' source
                temperature is not defined here.
        """
        if heatpump.group_id not in cls.RATED_GROUPS:
            raise ValueError(
                "the EN 14825 rating here covers air/water (hplib group 1) and brine/water (group 2) "
                f"machines only, and this one is hplib group {heatpump.group_id:g}."
            )

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
                result = calibration.apply(result, 1)
            return float(result["P_th"]), float(result["COP"])

        design_load, _cop = run(cls.SIZING_OUTDOOR_TEMPERATURE, cls.SIZING_FLOW_TEMPERATURE)
        design_load /= cls.DESIGN_SIZING_PART_LOAD
        heat = 0.0
        electricity = 0.0
        for outdoor, hours in cls.BINS:
            demand = design_load * cls.part_load(outdoor)
            output, cop = run(outdoor, cls.flow_temperature(application, outdoor))
            by_heat_pump = min(demand, output) if cop > CarnotCopHeatPump.HPLIB_HEATING_ROD_ALONE_MAXIMAL_COP else 0.0
            heat += hours * demand
            electricity += hours * ((by_heat_pump / cop if by_heat_pump else 0.0) + demand - by_heat_pump)
        return heat / electricity


class ScopCalibration(Subcomponent):
    """Scales a heat pump's COP to the SCOP its datasheet states (hisim-component-physics-4g9.15).

    The simulated machine's COP is the Carnot-efficiency law fitted to the whole Keymark population
    of its source type (:class:`CarnotEfficiencyCop`), so uncalibrated it is the average certified
    unit. A stated standardised SCOP says how much better or worse the real unit is. Every heating
    call multiplies the COP by a factor for its own outlet temperature and divides the compressor's
    electricity by it; the thermal output is unchanged, so the building gets the same heat for
    less or more electricity. The factors are solved (:meth:`of`) so that the calibrated machine,
    rated by :class:`StandardizedSeasonalCop`, gives exactly the stated SCOPs.

    * Both ratings stated: the factor is linear in the outlet temperature between the two
      :attr:`ANCHORS` and held at the nearer anchor's value outside. The anchors are the
      heat-weighted mean outlets of the two rating lines (28.9 and 40.7 °C), where each rating
      spends its heat; anchoring at the nominal 35 / 55 °C instead is ill-conditioned and drives
      the 55 °C factor to 0.2–0.5 for wide pairs (decision with Noah, 2026-09-23; the check against
      measured units is hisim-x2mu).
    * One stated: its factor everywhere.
    * The machine has no heating rod (:class:`CarnotCopHeatPump`), so every heating call is the
      compressor's and is scaled.
    * Cooling (mode 2) is out of scope and left as hplib returns it.

    What the solve rests on: each rating is continuous and non-decreasing in each factor (a bin
    whose calibrated COP falls to 1 switches to the back-up heater at COP 1, which draws the same
    electricity, so there is no jump); it is 1 when the factors are so small that every bin runs
    its back-up heater, and however large they grow it stays bounded by the back-up heater's share
    of the season, which no factor scales. So a bracketed solve finds a rating's factor whenever
    one exists, and a stated SCOP outside what the fit can reach is refused by name.

    Example::

        calibration = ScopCalibration.of(heatpump, scop_w35=4.6, scop_w55=3.4)
        calibration.apply(heatpump.simulate(...), mode=1)
    """

    #: How close the calibrated rating must come to the stated SCOP.
    TOLERANCE: ClassVar[float] = 1e-5

    #: The factors the solve searches between. At 0.001 no calibrated COP comes near 1, so every bin
    #: runs its back-up heater and the rating is 1; at 1000 the compressor draws a thousandth of
    #: its electricity, and the back-up heater's share is all that still bounds a rating.
    MINIMUM_FACTOR: ClassVar[float] = 1e-3
    MAXIMUM_FACTOR: ClassVar[float] = 1e3

    #: The rounds one bracketed solve may take; the Illinois method needs twenty to thirty.
    MAXIMUM_ITERATIONS: ClassVar[int] = 100

    #: The outlet temperatures the W35 and the W55 factor hold at, °C (28.9 and 40.7): the
    #: heat-weighted mean outlets of the two rating lines, computed once.
    ANCHORS: ClassVar[Tuple[float, float]] = (
        StandardizedSeasonalCop.mean_outlet_temperature(ScopApplication.W35),
        StandardizedSeasonalCop.mean_outlet_temperature(ScopApplication.W55),
    )

    def __init__(self, factors: Dict[ScopApplication, float]) -> None:
        """Hold the factor per stated application; an empty map calibrates nothing."""
        self.factors = dict(factors)

    @classmethod
    def of(
        cls,
        heatpump: Any,
        scop_w35: Optional[float],
        scop_w55: Optional[float],
        machine: str = "the Carnot-efficiency COP",
    ) -> "ScopCalibration":
        """Return the factors that bring the machine, rated by the bin method, to the stated SCOPs.

        One rating: its factor is the root of ``rated(factor) = stated`` (:meth:`solve`). Two
        ratings: for a given W55 factor the W35 factor that rates W35 exactly is solved, and the
        W55 rating of that pair rises with the W55 factor (the W55 factor moves the W55 line's hot
        bins more than the W35 factor it displaces moves its mild ones; checked on hplib's Generic
        air/water and brine/water fits for W35 ratings from 1.1 to 10 over the whole factor range,
        and on the Carnot-efficiency law of both source types for the pairs of the tests), so the
        W55 factor is solved on it in turn. A single ratio ``stated / rated`` is not exact:
        the back-up heater's share of the season is not scaled, and with two ratings each factor
        reaches into the other rating's line. A typical pair takes a few hundred ratings, some tens
        of milliseconds.

        Args:
            heatpump: A :class:`CarnotCopHeatPump` of hplib group 1 or 2, only read.
            scop_w35: The stated W35 rating, or ``None``.
            scop_w55: The stated W55 rating, or ``None``.
            machine: How a refusal names the machine, e.g. "the air/water Carnot-efficiency COP".

        Raises:
            ValueError: When no factors between :attr:`MINIMUM_FACTOR` and :attr:`MAXIMUM_FACTOR`
                reach the stated SCOPs; the message names the rating that cannot be met and the
                limit the fit reaches.
        """
        stated = {
            application: float(value)
            for application, value in ((ScopApplication.W35, scop_w35), (ScopApplication.W55, scop_w55))
            if value is not None
        }
        if not stated:
            return cls({})
        ratings = " and ".join(f"{application.value} {value}" for application, value in stated.items())
        refused = f"the stated SCOP{'s' if len(stated) > 1 else ''} {ratings} cannot be met: "
        lowest, highest = cls.MINIMUM_FACTOR, cls.MAXIMUM_FACTOR
        if len(stated) == 1:
            ((application, target),) = stated.items()

            def single(factor: float) -> float:
                """Return the rating of this application with one factor."""
                return StandardizedSeasonalCop.of(heatpump, application, cls({application: factor}))

            at_lowest, at_highest = single(lowest), single(highest)
            if target <= at_lowest:
                raise ValueError(refused + cls.unreachable(machine, application, at_lowest, False, False, None))
            if target >= at_highest:
                raise ValueError(refused + cls.unreachable(machine, application, at_highest, True, True, None))
            return cls({application: cls.solve(single, target, cls.TOLERANCE / 10)})

        w35, w55 = stated[ScopApplication.W35], stated[ScopApplication.W55]

        def rated(application: ScopApplication, factor_w35: float, factor_w55: float) -> float:
            calibration = cls({ScopApplication.W35: factor_w35, ScopApplication.W55: factor_w55})
            return StandardizedSeasonalCop.of(heatpump, application, calibration)

        def w35_factor(factor_w55: float) -> float:
            # Solved finer than the outer solve's tolerance, so its rest does not stall that solve.
            return cls.solve(lambda factor: rated(ScopApplication.W35, factor, factor_w55), w35, cls.TOLERANCE / 1000)

        def w55_rating(factor_w55: float) -> float:
            return rated(ScopApplication.W55, w35_factor(factor_w55), factor_w55)

        highest_w35 = rated(ScopApplication.W35, highest, highest)
        if w35 >= highest_w35:
            raise ValueError(refused + cls.unreachable(machine, ScopApplication.W35, highest_w35, True, True, None))
        # The W55 factors at which some W35 factor in range still rates W35 exactly: from where the
        # highest W35 factor just reaches it to where the lowest one just does.
        first = cls.solve(lambda factor: rated(ScopApplication.W35, highest, factor), w35, cls.TOLERANCE / 1000)
        last = cls.solve(lambda factor: rated(ScopApplication.W35, lowest, factor), w35, cls.TOLERANCE / 1000)
        condition = f"with W35 at {w35}, "
        lowest_w55 = w55_rating(first)
        if w55 <= lowest_w55:
            # At the lowest W55 factor the W55 factor itself would have to go lower; above it, the W35
            # factor that holds W35 would have to go above the range.
            anchor, beyond = (ScopApplication.W55, False) if first <= lowest else (ScopApplication.W35, True)
            raise ValueError(
                refused
                + condition
                + cls.unreachable(machine, ScopApplication.W55, lowest_w55, False, beyond, anchor)
            )
        highest_w55 = w55_rating(last)
        if w55 >= highest_w55:
            # Mirrored: the W55 factor would have to go higher, or the W35 factor below the range.
            anchor, beyond = (ScopApplication.W55, True) if last >= highest else (ScopApplication.W35, False)
            raise ValueError(
                refused
                + condition
                + cls.unreachable(machine, ScopApplication.W55, highest_w55, True, beyond, anchor)
            )
        factor_w55 = cls.solve(w55_rating, w55, cls.TOLERANCE / 10)
        return cls({ScopApplication.W35: w35_factor(factor_w55), ScopApplication.W55: factor_w55})

    @classmethod
    def unreachable(
        cls,
        machine: str,
        application: ScopApplication,
        limit: float,
        above: bool,
        factor_above: bool,
        anchor: Optional[ScopApplication],
    ) -> str:
        """Return the sentence that refuses one rating beyond what the fit reaches.

        Args:
            machine: How the sentence names the fit.
            application: The rating that cannot be met.
            limit: The furthest the fit rates it.
            above: Whether the stated rating is above that limit (else below it).
            factor_above: Whether reaching it would need a factor above :attr:`MAXIMUM_FACTOR`
                (else below :attr:`MINIMUM_FACTOR`).
            anchor: The anchor whose factor would have to leave the range, or ``None`` for a
                single rating, whose factor holds everywhere.
        """
        bound = f"above {cls.MAXIMUM_FACTOR:g}" if factor_above else f"below {cls.MINIMUM_FACTOR:g}"
        where = ""
        if anchor is not None:
            temperature = cls.ANCHORS[0 if anchor is ScopApplication.W35 else 1]
            where = f" at the {anchor.value} anchor ({temperature:.1f} °C)"
        sentence = (
            f"{machine} cannot rate {'above' if above else 'below'} {application.value} {limit:.2f}; a "
            f"{'higher' if above else 'lower'} {application.value} rating would need a factor {bound}{where}"
        )
        if above and factor_above and anchor in (None, application):
            sentence += ", and the back-up heater's share of the season, which no factor scales, caps it there"
        return sentence + "."

    @classmethod
    def solve(cls, rating: Callable[[float], float], target: float, tolerance: float) -> float:
        """Return the factor at which a non-decreasing rating meets a target, to a tolerance.

        The Illinois method (regula falsi that halves the stale end's miss when the same end moves
        twice) on the factor's logarithm between :attr:`MINIMUM_FACTOR` and :attr:`MAXIMUM_FACTOR`;
        a step that would leave the bracket bisects instead. A target the rating does not cross
        in that range returns the nearer bound: the callers check the bounds first where a miss
        must be refused, and the nested solve relies on the clamp.

        Raises:
            RuntimeError: When :attr:`MAXIMUM_ITERATIONS` rounds pass without meeting the
                tolerance, which a continuous rating does not allow.
        """
        low, high = math.log(cls.MINIMUM_FACTOR), math.log(cls.MAXIMUM_FACTOR)
        miss_low = rating(cls.MINIMUM_FACTOR) - target
        if miss_low >= 0:
            return cls.MINIMUM_FACTOR
        miss_high = rating(cls.MAXIMUM_FACTOR) - target
        if miss_high <= 0:
            return cls.MAXIMUM_FACTOR
        moved = 0
        for _round in range(cls.MAXIMUM_ITERATIONS):
            point = (low * miss_high - high * miss_low) / (miss_high - miss_low)
            if not low < point < high:
                point = (low + high) / 2
            miss = rating(math.exp(point)) - target
            if abs(miss) < tolerance:
                return math.exp(point)
            if miss < 0:
                low, miss_low = point, miss
                if moved < 0:
                    miss_high /= 2
                moved = -1
            else:
                high, miss_high = point, miss
                if moved > 0:
                    miss_low /= 2
                moved = 1
        raise RuntimeError(
            f"the SCOP calibration's solve for a rating of {target} did not converge in "
            f"{cls.MAXIMUM_ITERATIONS} rounds (bracket {math.exp(low):.6g} to {math.exp(high):.6g})."
        )

    def factor(self, outlet_temperature: float) -> float:
        """Return the factor for one outlet temperature, °C: linear between the anchors, held outside."""
        if not self.factors:
            return 1.0
        if len(self.factors) == 1:
            return next(iter(self.factors.values()))
        return float(
            np.interp(
                outlet_temperature,
                self.ANCHORS,
                (self.factors[ScopApplication.W35], self.factors[ScopApplication.W55]),
            )
        )

    def apply(self, results: Dict[str, Any], mode: int) -> Dict[str, Any]:
        """Return a machine's results for one call with the calibration applied (a new dictionary).

        In heating the COP is multiplied by the factor at the call's outlet and the electricity divided by it; the
        thermal power stays. For example, a W55 factor of 0.8 turns a COP of 3.0 at 6 kW into 2.4, and 2.0 kW of
        electricity into 2.5 kW. Without factors, and in cooling, the results are returned as they are.

        Args:
            results: A :class:`CarnotCopHeatPump`'s answer, with ``COP``, ``P_th``, ``P_el`` and ``T_out``.
            mode: 1 heating, 2 cooling.

        Returns:
            The calibrated answer.
        """
        if not self.factors or mode != 1:
            return results
        factor = self.factor(float(results["T_out"]))
        calibrated = dict(results)
        calibrated["P_el"] = float(results["P_el"]) / factor
        calibrated["COP"] = float(results["COP"]) * factor
        return calibrated


@dataclass(frozen=True)
class HplibResult:

    """The machine's answer at one operating point, the numbers the heat pump computes with.

    An adapter between the result dictionary of :class:`CarnotCopHeatPump` (hplib's keys: hplib's thermal power,
    outlet and flow, the Carnot-efficiency COP) and the heat pump: the heat pump reads typed fields instead of
    dictionary keys, and the interpolation between two cached grid points is one pure function
    (:meth:`interpolated`).
    """

    #: The water temperature leaving the heat pump's secondary side, in °C (hplib's ``T_out``).
    outlet_temperature_in_celsius: float
    #: The secondary side's mass flow, in kg/s (hplib's ``m_dot``).
    mass_flow_in_kg_per_second: float
    #: The machine's thermal power, hplib's compressor capacity, in W (``P_th``).
    thermal_power_in_watt: float
    #: The machine's electrical power, the thermal power over the COP, in W (``P_el``).
    electrical_power_in_watt: float
    #: The coefficient of performance, dimensionless (``COP``).
    cop: float
    #: The energy efficiency ratio of cooling, dimensionless (``EER``).
    eer: float

    @staticmethod
    def from_hplib(results: Mapping[str, Any]) -> "HplibResult":
        """Return the fields of a machine's result dictionary, as floats.

        For example, ``{"T_out": 35.0, "m_dot": 0.38, "P_th": 7900.0, "P_el": 1880.0, "COP": 4.2, "EER": 0.0}`` gives a
        result with an outlet of 35 °C and a COP of 4.2.

        Args:
            results: The machine's result, calibrated or not, with the keys ``T_out``, ``m_dot``, ``P_th``, ``P_el``,
                ``COP`` and ``EER``.

        Returns:
            The typed result.
        """
        return HplibResult(
            outlet_temperature_in_celsius=float(results["T_out"]),
            mass_flow_in_kg_per_second=float(results["m_dot"]),
            thermal_power_in_watt=float(results["P_th"]),
            electrical_power_in_watt=float(results["P_el"]),
            cop=float(results["COP"]),
            eer=float(results["EER"]),
        )

    @staticmethod
    def interpolated(*, lower: "HplibResult", upper: "HplibResult", share_of_upper: float) -> "HplibResult":
        """Return the linear interpolation between hplib's results at two neighbouring return temperatures.

        Every field is ``lower + share_of_upper (upper - lower)``. For example, a return of 47.04 °C on the 0.1 K grid
        takes 60 % of the result at 47.0 °C and 40 % of the one at 47.1 °C. The machine's answers are continuous in the
        return (hplib's jump to its heating rod is gone, :class:`CarnotCopHeatPump`), so every return is interpolated.

        Args:
            lower: The result at the lower grid point.
            upper: The result at the upper grid point.
            share_of_upper: The upper point's weight, dimensionless, from 0 to 1.

        Returns:
            The interpolated result.
        """

        def between(lower_value: float, upper_value: float) -> float:
            """Return the value at ``share_of_upper`` of the way from ``lower_value`` to ``upper_value``."""
            return lower_value + share_of_upper * (upper_value - lower_value)

        return HplibResult(
            outlet_temperature_in_celsius=between(
                lower.outlet_temperature_in_celsius, upper.outlet_temperature_in_celsius
            ),
            mass_flow_in_kg_per_second=between(lower.mass_flow_in_kg_per_second, upper.mass_flow_in_kg_per_second),
            thermal_power_in_watt=between(lower.thermal_power_in_watt, upper.thermal_power_in_watt),
            electrical_power_in_watt=between(lower.electrical_power_in_watt, upper.electrical_power_in_watt),
            cop=between(lower.cop, upper.cop),
            eer=between(lower.eer, upper.eer),
        )


@dataclass(frozen=True)
class HeatPumpStepConditions:

    """The temperatures the hplib heat pump reads in one step.

    A set temperature the configuration does not read is None: the space-heating set temperature is read only in
    the fixed-flow mode and for passive cooling, the hot-water set temperature only with hot-water preparation.
    """

    #: The source temperature on the primary side, in °C.
    source_temperature_in_celsius: float
    #: The outside air temperature, in °C.
    ambient_temperature_in_celsius: float
    #: The return temperature of the space-heating circuit, in °C.
    space_heating_return_temperature_in_celsius: float
    #: The return temperature of the hot-water circuit, the tank's step mean, in °C; 0 without hot water.
    hot_water_return_temperature_in_celsius: float
    #: The space-heating set temperature, in °C, or None where it is not read.
    space_heating_set_temperature_in_celsius: Optional[float]
    #: The hot-water supply temperature the hot-water controller aims at, in °C, or None without hot water.
    hot_water_supply_set_temperature_in_celsius: Optional[float]


@dataclass(frozen=True)
class HeatPumpRunTimers:

    """How long the heat pump has been heating, cooling and off, in s.

    Only the timer of the current mode runs; the two others are 0. Space heating and hot water share the heating
    timer.
    """

    #: How long the machine has been heating, in s.
    time_on_heating_in_seconds: int
    #: How long the machine has been cooling, in s.
    time_on_cooling_in_seconds: int
    #: How long the machine has been off, in s.
    time_off_in_seconds: int


@dataclass(frozen=True)
class HeatPumpModeTrack:
    """The heat pump's mode in a step and its run timers after that step, on one of its two tracks.

    The control track holds the mode the heat pump runs on after its minimum running and idle times; its timers feed
    those minimum times and the start-up ramp, so it decides what the machine does. The published track holds the
    mode the machine ran, where a space-heating step at a part-load ratio of 0 is off; its mode and timers are what
    the heat pump publishes as its on/off state, ``TimeOnHeating`` and ``TimeOff``, and what its switch counters and
    KPIs count. For example, at 900 s a space-heating step at ratio 0 after 600 s of heating is
    ``HeatPumpModeTrack(1, HeatPumpRunTimers(1500, 0, 0))`` on the control track and
    ``HeatPumpModeTrack(0, HeatPumpRunTimers(0, 0, 900))`` on the published one.
    """

    #: The mode: 1 space heating, 2 hot water, -1 cooling, 0 off.
    on_off: float
    #: The run timers after the step in that mode, in s.
    timers: HeatPumpRunTimers


@dataclass(frozen=True)
class HeatPumpSwitchCounters:

    """How often the heat pump has entered space heating, hot water and operation, since the run began.

    Each count is a number of switches, dimensionless.
    """

    #: Switches into space heating.
    space_heating: int
    #: Switches into hot water.
    hot_water: int
    #: Switches from off into any mode.
    on_off: int


@dataclass(frozen=True)
class HeatPumpOperation:

    """The hplib heat pump's powers, outlets and flows in one step, whatever mode it runs in.

    Each mode fills it through one factory (:meth:`space_heating`, :meth:`hot_water`, :meth:`cooling`,
    :meth:`idle`); the circuit that does not run has no flow and its outlet is its return. The totals are
    properties, summed in one order, so every output derives from the same numbers. A heating circuit's heat is the
    compressor's and the backup heating rod's (:class:`RodAssistedHeating`); its electricity for space heating or hot
    water is the compressor's, and the rod's electricity is its own field.
    """

    #: The heat the space-heating circuit carries, in W; negative while cooling.
    thermal_power_space_heating_in_watt: float
    #: The heat the hot-water circuit carries, in W.
    thermal_power_hot_water_in_watt: float
    #: The compressor's electricity for space heating, in W.
    electrical_power_space_heating_in_watt: float
    #: The compressor's electricity for hot water, in W.
    electrical_power_hot_water_in_watt: float
    #: The compressor's electricity for active cooling, in W.
    electrical_power_cooling_in_watt: float
    #: The brine or well pump's electricity, in W.
    electrical_power_brine_pump_in_watt: float
    #: The backup heating rod's electricity, in W, in space heating or hot water; also its heat.
    electrical_power_heating_rod_in_watt: float
    #: The coefficient of performance of heating, the circuit's heat over the compressor's and the rod's
    #: electricity, dimensionless; 0 while not heating.
    cop: float
    #: The energy efficiency ratio of cooling, dimensionless.
    eer: float
    #: The space-heating circuit's outlet temperature, in °C.
    outlet_temperature_space_heating_in_celsius: float
    #: The hot-water circuit's outlet temperature, in °C.
    outlet_temperature_hot_water_in_celsius: float
    #: The space-heating circuit's mass flow, in kg/s.
    mass_flow_space_heating_in_kg_per_second: float
    #: The hot-water circuit's mass flow, in kg/s.
    mass_flow_hot_water_in_kg_per_second: float

    @property
    def total_thermal_power_in_watt(self) -> float:
        """Return the heat of both circuits, in W."""
        return self.thermal_power_hot_water_in_watt + self.thermal_power_space_heating_in_watt

    @property
    def total_electrical_power_in_watt(self) -> float:
        """Return the electricity of the compressor in every mode, of the backup heating rod and of the brine pump, in W."""
        return (
            self.electrical_power_hot_water_in_watt
            + self.electrical_power_space_heating_in_watt
            + self.electrical_power_cooling_in_watt
            + self.electrical_power_brine_pump_in_watt
            + self.electrical_power_heating_rod_in_watt
        )

    @property
    def thermal_power_from_environment_in_watt(self) -> float:
        """Return the heat taken from the source, the heat delivered minus the electricity, in W."""
        return self.total_thermal_power_in_watt - self.total_electrical_power_in_watt

    @staticmethod
    def space_heating(
        conditions: HeatPumpStepConditions,
        *,
        heating: RodAssistedHeating,
        electrical_power_brine_pump_in_watt: float,
        eer: float,
    ) -> "HeatPumpOperation":
        """Return a step that heats the building; the hot-water circuit idles at its return.

        The brine pump runs the compressor's share of the step, so it draws that share of its power.
        """
        return HeatPumpOperation(
            thermal_power_space_heating_in_watt=heating.thermal_power_in_watt,
            thermal_power_hot_water_in_watt=0.0,
            electrical_power_space_heating_in_watt=heating.compressor_electrical_power_in_watt,
            electrical_power_hot_water_in_watt=0.0,
            electrical_power_cooling_in_watt=0.0,
            electrical_power_brine_pump_in_watt=heating.compressor_running_share * electrical_power_brine_pump_in_watt,
            electrical_power_heating_rod_in_watt=heating.heating_rod_electrical_power_in_watt,
            cop=heating.cop,
            eer=eer,
            outlet_temperature_space_heating_in_celsius=heating.outlet_temperature_in_celsius,
            outlet_temperature_hot_water_in_celsius=conditions.hot_water_return_temperature_in_celsius,
            mass_flow_space_heating_in_kg_per_second=heating.mass_flow_in_kg_per_second,
            mass_flow_hot_water_in_kg_per_second=0.0,
        )

    @staticmethod
    def hot_water(
        conditions: HeatPumpStepConditions,
        *,
        heating: RodAssistedHeating,
        electrical_power_brine_pump_in_watt: float,
        eer: float,
    ) -> "HeatPumpOperation":
        """Return a step that heats the hot water; the space-heating circuit idles at its return.

        The brine pump runs the compressor's share of the step, so it draws that share of its power.
        """
        return HeatPumpOperation(
            thermal_power_space_heating_in_watt=0.0,
            thermal_power_hot_water_in_watt=heating.thermal_power_in_watt,
            electrical_power_space_heating_in_watt=0.0,
            electrical_power_hot_water_in_watt=heating.compressor_electrical_power_in_watt,
            electrical_power_cooling_in_watt=0.0,
            electrical_power_brine_pump_in_watt=heating.compressor_running_share * electrical_power_brine_pump_in_watt,
            electrical_power_heating_rod_in_watt=heating.heating_rod_electrical_power_in_watt,
            cop=heating.cop,
            eer=eer,
            outlet_temperature_space_heating_in_celsius=conditions.space_heating_return_temperature_in_celsius,
            outlet_temperature_hot_water_in_celsius=heating.outlet_temperature_in_celsius,
            mass_flow_space_heating_in_kg_per_second=0.0,
            mass_flow_hot_water_in_kg_per_second=heating.mass_flow_in_kg_per_second,
        )

    @staticmethod
    def cooling(
        conditions: HeatPumpStepConditions,
        *,
        thermal_power_in_watt: float,
        electrical_power_cooling_in_watt: float,
        electrical_power_brine_pump_in_watt: float,
        cop: float,
        eer: float,
        outlet_temperature_in_celsius: float,
        mass_flow_in_kg_per_second: float,
    ) -> "HeatPumpOperation":
        """Return a step that cools the building through the space-heating circuit; the hot water idles."""
        return HeatPumpOperation(
            thermal_power_space_heating_in_watt=thermal_power_in_watt,
            thermal_power_hot_water_in_watt=0.0,
            electrical_power_space_heating_in_watt=0.0,
            electrical_power_hot_water_in_watt=0.0,
            electrical_power_cooling_in_watt=electrical_power_cooling_in_watt,
            electrical_power_brine_pump_in_watt=electrical_power_brine_pump_in_watt,
            electrical_power_heating_rod_in_watt=0.0,
            cop=cop,
            eer=eer,
            outlet_temperature_space_heating_in_celsius=outlet_temperature_in_celsius,
            outlet_temperature_hot_water_in_celsius=conditions.hot_water_return_temperature_in_celsius,
            mass_flow_space_heating_in_kg_per_second=mass_flow_in_kg_per_second,
            mass_flow_hot_water_in_kg_per_second=0.0,
        )

    @staticmethod
    def idle(conditions: HeatPumpStepConditions) -> "HeatPumpOperation":
        """Return a step with the machine off: no heat, no electricity, both circuits idle at their returns.

        The COP and EER are 0 rather than undefined, since post-processing cannot read a missing value.
        """
        return HeatPumpOperation(
            thermal_power_space_heating_in_watt=0.0,
            thermal_power_hot_water_in_watt=0.0,
            electrical_power_space_heating_in_watt=0.0,
            electrical_power_hot_water_in_watt=0.0,
            electrical_power_cooling_in_watt=0.0,
            electrical_power_brine_pump_in_watt=0.0,
            electrical_power_heating_rod_in_watt=0.0,
            cop=0.0,
            eer=0.0,
            outlet_temperature_space_heating_in_celsius=conditions.space_heating_return_temperature_in_celsius,
            outlet_temperature_hot_water_in_celsius=conditions.hot_water_return_temperature_in_celsius,
            mass_flow_space_heating_in_kg_per_second=0.0,
            mass_flow_hot_water_in_kg_per_second=0.0,
        )


@dataclass(frozen=True)
class HeatPumpEnergyTotals:

    """The hplib heat pump's thermal and electrical energies of one step and its running totals after it, in Wh.

    The running totals grow by the magnitude of each step's energy.
    """

    #: The heat of both circuits in the step, in Wh.
    thermal_total_in_watt_hour: float
    #: The heat of the space-heating circuit in the step, in Wh.
    thermal_space_heating_in_watt_hour: float
    #: The heat of the hot-water circuit in the step, in Wh.
    thermal_hot_water_in_watt_hour: float
    #: All electricity of the step, in Wh.
    electrical_total_in_watt_hour: float
    #: The compressor's electricity for space heating in the step, in Wh.
    electrical_space_heating_in_watt_hour: float
    #: The compressor's electricity for hot water in the step, in Wh.
    electrical_hot_water_in_watt_hour: float
    #: The running total of the heat of both circuits, in Wh.
    cumulative_thermal_total_in_watt_hour: float
    #: The running total of the space-heating heat, in Wh.
    cumulative_thermal_space_heating_in_watt_hour: float
    #: The running total of the hot-water heat, in Wh.
    cumulative_thermal_hot_water_in_watt_hour: float
    #: The running total of all electricity, in Wh.
    cumulative_electrical_total_in_watt_hour: float
    #: The running total of the space-heating electricity, in Wh.
    cumulative_electrical_space_heating_in_watt_hour: float
    #: The running total of the hot-water electricity, in Wh.
    cumulative_electrical_hot_water_in_watt_hour: float


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
