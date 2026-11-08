"""The heat pump's COP of heating as a constant share of the Carnot COP, and the compressor that combines it with hplib.

:class:`CarnotEfficiencyCop` is the law, ``cop = eta * (outlet + 273.15) / (max(outlet - source, minimal lift) + d)``,
with ``eta`` and ``d`` fitted per source type (:class:`HeatSourceType`, air or brine) to the Keymark-certified units of
hplib's raw exports (decision (2) of ``roadmap/heat_pump_model_rework_spec.md``). :class:`CarnotCopHeatPump` keeps
hplib's thermal power, outlet and mass flow and replaces hplib's COP plane by that law; it answers like
``hplib.HeatPump.simulate``, so the EN 14825 rating and the SCOP calibration
(:mod:`hisim.components.more_advanced_heat_pump.seasonal_cop`) read it as they read hplib.
"""

import math
from enum import Enum, unique
from typing import Any, ClassVar, Dict, Tuple

from hisim.subcomponents.base import Subcomponent


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
