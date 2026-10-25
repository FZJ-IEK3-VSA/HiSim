"""The circulation pump every device that pumps its own water loop runs: one flow law, in four modes.

A boiler, a solar collector, a district-heating substation, an electric heater, a heat pump in its fixed-flow mode and
the heat distribution system each move the water of a loop they own. Each holds a :class:`CirculationPump` and asks
it, in every pass, which flow it runs and where its outlet ends. The pump is a subcomponent
(``system_docs/component_design_principles.md``, section C): built once from the device's configuration, without
ports, never seen by the simulator.

A device on a loop that another device pumps holds no pump for that loop; it reads the flow from its input (the
boiler behind the heat distribution system, for example).
"""

import enum
import math
from dataclasses import dataclass
from typing import ClassVar, NamedTuple

from hisim import hydronics
from hisim.subcomponents.base import Subcomponent


class PumpMode(enum.Enum):
    """How a circulation pump decides its flow.

    For example, a solar collector's pump runs ``FLOW_MATCHES_FIXED_LIFT`` at 20 K, a boiler's ``FLOW_MATCHES_CONTROLLER_LIFT``, and
    the heat distribution system's ``CONSTANT_FLOW`` at its design flow.

    * ``CONSTANT_FLOW``: the design flow, ``m = m_design``, whatever the heat.
    * ``FLOW_MATCHES_FIXED_LIFT``: the "temperature-sensor pump", the flow that carries the heat over a fixed lift,
      ``m = Q / (c dT_target)``.
    * ``FLOW_MATCHES_CONTROLLER_LIFT``: the flow that carries the heat over the lift the device's controller asks for in each step,
      ``m = Q / (c lift)``; the controller computes the lift as its supply set temperature minus the temperature it
      reads.
    * ``FLOW_MATCHES_TARGET_SUPPLY_TEMPERATURE``: the flow that brings the device's own return up to a supply set temperature,
      ``m = Q / (c (T_set - T_ret))``. Unlike ``FLOW_MATCHES_CONTROLLER_LIFT``, the lift is computed from the return the device
      sees in the pass, not from a temperature the controller read. No recorded system uses it yet.
    * ``EXTERNAL_SIGNAL``: a share of the largest flow that a controller decides, ``m = signal m_max``, the way a
      PWM or 0-10 V input drives a pump.
    """

    CONSTANT_FLOW = "constant_flow"
    FLOW_MATCHES_FIXED_LIFT = "flow_matches_fixed_lift"
    FLOW_MATCHES_TARGET_SUPPLY_TEMPERATURE = "flow_matches_target_supply_temperature"
    FLOW_MATCHES_CONTROLLER_LIFT = "flow_matches_controller_lift"
    EXTERNAL_SIGNAL = "external_signal"


class PumpOperatingPoint(NamedTuple):
    """The flow a circulation pump runs in one pass and the temperature its water leaves the device at.

    For example, a pump at a 20 K target lift that carries 1672 W from a 40 °C return runs 0.02 kg/s and supplies
    60 °C: ``PumpOperatingPoint(0.02, 60.0, False)``. A ``NamedTuple``, not a dataclass, because the pump returns one
    in every pass of every step (``coding_style.md``, §2 rule 12).

    Attributes:
        mass_flow_in_kg_per_second: The flow through the device, in kg/s, 0 or more.
        supply_temperature_in_celsius: The temperature the water leaves the device at, before any limit the device
            sets on it, in °C: the return plus the lift the flow carries the heat over.
        flow_is_clamped: Whether the flow the mode asks for lay outside the pump's bounds (or the signal outside 0 to
            1), so that the pump runs the nearer bound and the lift follows from the heat at that flow.
    """

    mass_flow_in_kg_per_second: float
    supply_temperature_in_celsius: float
    flow_is_clamped: bool

    @classmethod
    def standing(cls, *, return_temperature_in_celsius: float) -> "PumpOperatingPoint":
        """Return the point of a pump that stands: no flow, and the water at the return temperature.

        For example, a solar collector without heat at its pump's smallest flow does not run its pump, and its point
        is ``standing(return_temperature_in_celsius=45.0)``, that is ``(0.0, 45.0, False)``.

        Args:
            return_temperature_in_celsius: The return temperature, in °C.

        Returns:
            The standing point.
        """
        return cls(0.0, return_temperature_in_celsius, False)


@dataclass(frozen=True)
class CirculationPump(Subcomponent):
    """A circulation pump: the flow it runs for the heat its device puts into the water, in one of four modes.

    The device calls :meth:`operating_point` with the heat it adds, the return temperature and, for a controller lift,
    the lift its controller asks for; the pump answers with the flow and the supply temperature. The controlled modes
    (``FLOW_MATCHES_FIXED_LIFT``, ``FLOW_MATCHES_CONTROLLER_LIFT``) keep the flow between their smallest and largest flow; at a bound,
    the outlet is the return plus the heat over ``c`` times that bound, and the result says ``flow_is_clamped``. The
    pump reports a clamp, it does not refuse one. A device whose heat depends on its own outlet (a solar collector)
    evaluates its heat again at the clamped flow; the pump never needs the device's physics.

    Built through the classmethod factories (:meth:`constant_flow`, :meth:`flow_matches_fixed_lift`,
    :meth:`flow_matches_fixed_lift_per_area`, :meth:`flow_matches_controller_lift`,
    :meth:`flow_matches_controller_lift_sized_for`, :meth:`flow_matches_target_supply_temperature`,
    :meth:`external_signal`), which a device feeds from its own configuration fields. For example, a solar pump with
    a 20 K target lift and 5 to 40 kg/(m² h) on 4 m² is ``CirculationPump.flow_matches_fixed_lift_per_area(target_lift_in_kelvin=
    20.0, minimal_mass_flow_in_kg_per_hour_per_m2=5.0, maximal_mass_flow_in_kg_per_hour_per_m2=40.0, area_m2=4.0)``.

    Attributes:
        mode: How the pump decides its flow.
        minimal_mass_flow_in_kg_per_second: The smallest flow a controlled mode runs, in kg/s, 0 or more.
        maximal_mass_flow_in_kg_per_second: The largest flow a controlled mode runs, in kg/s, above the smallest;
            infinite for a pump without a bound. In ``EXTERNAL_SIGNAL`` the flow at a signal of 1, finite; in
            ``FLOW_MATCHES_TARGET_SUPPLY_TEMPERATURE`` finite too, the flow it runs when the heat cannot reach the set temperature.
        design_mass_flow_in_kg_per_second: The flow of a ``CONSTANT_FLOW`` pump, in kg/s, above 0; NaN in other modes.
        target_lift_in_kelvin: The lift a ``FLOW_MATCHES_FIXED_LIFT`` pump holds, in K, above 0; NaN in other modes.
    """

    mode: PumpMode
    minimal_mass_flow_in_kg_per_second: float = 0.0
    maximal_mass_flow_in_kg_per_second: float = math.inf
    design_mass_flow_in_kg_per_second: float = math.nan
    target_lift_in_kelvin: float = math.nan

    #: The specific heat of the water the pump moves, in J/(kg K).
    SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN: ClassVar[float] = hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K
    #: Seconds in an hour, to turn a flow per hour into one per second.
    SECONDS_PER_HOUR: ClassVar[float] = 3600.0

    def __post_init__(self) -> None:
        """Refuse a setting the mode cannot run, so a bad pump fails when its device is built.

        Every mode reads its own fields and leaves the others at their defaults:

        * ``CONSTANT_FLOW``: a finite design flow above 0, no bounds.
        * ``FLOW_MATCHES_FIXED_LIFT``: a finite target lift above 0, and bounds ``0 <= m_min <= m_max``, ``m_max > 0``.
        * ``FLOW_MATCHES_CONTROLLER_LIFT``: the same bounds.
        * ``FLOW_MATCHES_TARGET_SUPPLY_TEMPERATURE``: the same bounds, with a finite largest flow.
        * ``EXTERNAL_SIGNAL``: a finite largest flow above 0, no smallest flow.

        For example, ``CirculationPump.constant_flow(design_mass_flow_in_kg_per_second=0.3)`` is accepted,
        ``CirculationPump(PumpMode.FLOW_MATCHES_FIXED_LIFT, target_lift_in_kelvin=0.0)`` and ``CirculationPump(
        PumpMode.CONSTANT_FLOW, design_mass_flow_in_kg_per_second=0.3, target_lift_in_kelvin=5.0)`` are refused.

        Raises:
            ValueError: If the mode is no :class:`PumpMode`, a field the mode needs is missing or out of range, or a
                field the mode does not read is set.
        """
        mode = self.mode
        if not isinstance(mode, PumpMode):
            raise ValueError(f"A circulation pump needs a PumpMode, got {mode!r}.")
        if mode is not PumpMode.CONSTANT_FLOW and not math.isnan(self.design_mass_flow_in_kg_per_second):
            raise ValueError(
                f"A {mode.value} pump does not read a design flow, got design_mass_flow_in_kg_per_second="
                f"{self.design_mass_flow_in_kg_per_second!r}; only a constant-flow pump runs its design flow."
            )
        if mode is not PumpMode.FLOW_MATCHES_FIXED_LIFT and not math.isnan(self.target_lift_in_kelvin):
            raise ValueError(
                f"A {mode.value} pump does not read a target lift, got target_lift_in_kelvin="
                f"{self.target_lift_in_kelvin!r}; only a fixed-lift pump holds a fixed lift."
            )
        largest = self.maximal_mass_flow_in_kg_per_second
        if mode is PumpMode.CONSTANT_FLOW:
            self.check_that_a_constant_flow_pump_has_no_bounds()
            design = self.design_mass_flow_in_kg_per_second
            if not math.isfinite(design) or design <= 0.0:
                raise ValueError(
                    f"A constant-flow pump needs a finite design flow above 0 kg/s, got {design!r}: it runs that flow "
                    "whatever the heat."
                )
        elif mode is PumpMode.EXTERNAL_SIGNAL:
            if self.minimal_mass_flow_in_kg_per_second != 0.0:
                raise ValueError(
                    "An external-signal pump does not read a smallest flow: its flow is the signal times its largest "
                    f"flow, got minimal_mass_flow_in_kg_per_second={self.minimal_mass_flow_in_kg_per_second!r}."
                )
            if not math.isfinite(largest) or largest <= 0.0:
                raise ValueError(
                    f"An external-signal pump needs a finite largest flow above 0 kg/s, got {largest!r}: a signal of "
                    "1 runs that flow."
                )
        else:
            self.check_the_bounds_of_a_controlled_mode()
            lift = self.target_lift_in_kelvin
            if mode is PumpMode.FLOW_MATCHES_FIXED_LIFT and (not math.isfinite(lift) or lift <= 0.0):
                raise ValueError(
                    f"A fixed-lift pump needs a finite target lift above 0 K, got {lift!r}: its flow is the heat "
                    "over c times that lift."
                )
            if mode is PumpMode.FLOW_MATCHES_TARGET_SUPPLY_TEMPERATURE and not math.isfinite(largest):
                raise ValueError(
                    f"A target-supply-temperature pump needs a finite largest flow, got {largest!r}: it runs that "
                    "flow when the heat cannot reach the set temperature."
                )

    def check_that_a_constant_flow_pump_has_no_bounds(self) -> None:
        """Refuse a smallest or largest flow on a constant pump, which runs its design flow and nothing else.

        For example, a constant pump of 0.3 kg/s with a largest flow of 0.5 kg/s is refused: the bound would never
        act, and a reader would think it did.

        Raises:
            ValueError: If either bound is not its default, 0 and infinity.
        """
        if self.minimal_mass_flow_in_kg_per_second != 0.0 or self.maximal_mass_flow_in_kg_per_second != math.inf:
            raise ValueError(
                "A constant-flow pump does not read a smallest or a largest flow, got "
                f"minimal_mass_flow_in_kg_per_second={self.minimal_mass_flow_in_kg_per_second!r} and "
                f"maximal_mass_flow_in_kg_per_second={self.maximal_mass_flow_in_kg_per_second!r}; it runs its "
                "design flow."
            )

    def check_the_bounds_of_a_controlled_mode(self) -> None:
        """Refuse bounds a controlled mode cannot keep its flow between: ``0 <= m_min <= m_max``, ``m_max > 0``.

        For example, 5 to 40 kg/(m² h) on 4 m² (0.0056 to 0.0444 kg/s) is accepted; a smallest flow of -0.1 kg/s, a
        largest of 0 kg/s or a smallest above the largest is refused. A largest flow of infinity is a pump without an
        upper bound.

        Raises:
            ValueError: If the smallest flow is negative or not finite, the largest is NaN or not above 0, or the
                smallest exceeds the largest.
        """
        smallest = self.minimal_mass_flow_in_kg_per_second
        largest = self.maximal_mass_flow_in_kg_per_second
        if not math.isfinite(smallest) or smallest < 0.0:
            raise ValueError(
                f"A {self.mode.value} pump needs a finite smallest flow of at least 0 kg/s, got {smallest!r}."
            )
        if math.isnan(largest) or largest <= 0.0:
            raise ValueError(
                f"A {self.mode.value} pump needs a largest flow above 0 kg/s (infinity for none), got {largest!r}."
            )
        if smallest > largest:
            raise ValueError(
                f"A {self.mode.value} pump's smallest flow {smallest!r} kg/s exceeds its largest flow {largest!r} kg/s."
            )

    @classmethod
    def constant_flow(cls, *, design_mass_flow_in_kg_per_second: float) -> "CirculationPump":
        """Return a pump that runs its design flow, whatever the heat.

        For example, the heat distribution system's pump of a floor heating designed for 7000 W over 7 K runs
        ``constant(design_mass_flow_in_kg_per_second=0.24)``.

        Args:
            design_mass_flow_in_kg_per_second: The flow, in kg/s, finite and above 0.

        Returns:
            The pump.

        Raises:
            ValueError: If the design flow is not finite or not above 0 kg/s.
        """
        return cls(mode=PumpMode.CONSTANT_FLOW, design_mass_flow_in_kg_per_second=design_mass_flow_in_kg_per_second)

    @classmethod
    def flow_matches_fixed_lift(
        cls,
        *,
        target_lift_in_kelvin: float,
        minimal_mass_flow_in_kg_per_second: float = 0.0,
        maximal_mass_flow_in_kg_per_second: float = math.inf,
    ) -> "CirculationPump":
        """Return a temperature-sensor pump: the flow that carries the heat over a fixed lift, within bounds.

        For example, a district-heating substation's hot-water pump that carries its connected load over 100 K is
        ``flow_matches_fixed_lift(target_lift_in_kelvin=100.0)``, without bounds.

        Args:
            target_lift_in_kelvin: The lift the pump holds, in K, finite and above 0.
            minimal_mass_flow_in_kg_per_second: The smallest flow, in kg/s, 0 or more.
            maximal_mass_flow_in_kg_per_second: The largest flow, in kg/s, infinity for none.

        Returns:
            The pump.

        Raises:
            ValueError: If the lift is not finite or not above 0 K, or the bounds are invalid.
        """
        return cls(
            mode=PumpMode.FLOW_MATCHES_FIXED_LIFT,
            target_lift_in_kelvin=target_lift_in_kelvin,
            minimal_mass_flow_in_kg_per_second=minimal_mass_flow_in_kg_per_second,
            maximal_mass_flow_in_kg_per_second=maximal_mass_flow_in_kg_per_second,
        )

    @classmethod
    def flow_matches_fixed_lift_per_area(
        cls,
        *,
        target_lift_in_kelvin: float,
        minimal_mass_flow_in_kg_per_hour_per_m2: float,
        maximal_mass_flow_in_kg_per_hour_per_m2: float,
        area_m2: float,
    ) -> "CirculationPump":
        """Return a temperature-sensor pump whose bounds are given per m² of the area it serves, per hour.

        A solar collector's pump is rated this way, so collector fields of any size run alike per m². The bounds are
        ``specific flow * area / 3600``. For example, 5 to 40 kg/(m² h) on 4 m² give 0.0056 to 0.0444 kg/s.

        Args:
            target_lift_in_kelvin: The lift the pump holds, in K, finite and above 0.
            minimal_mass_flow_in_kg_per_hour_per_m2: The smallest flow per m², in kg/(m² h).
            maximal_mass_flow_in_kg_per_hour_per_m2: The largest flow per m², in kg/(m² h).
            area_m2: The area the pump serves, in m².

        Returns:
            The pump.

        Raises:
            ValueError: If the lift or the bounds in kg/s are invalid.
        """
        return cls.flow_matches_fixed_lift(
            target_lift_in_kelvin=target_lift_in_kelvin,
            minimal_mass_flow_in_kg_per_second=minimal_mass_flow_in_kg_per_hour_per_m2 * area_m2 / cls.SECONDS_PER_HOUR,
            maximal_mass_flow_in_kg_per_second=maximal_mass_flow_in_kg_per_hour_per_m2 * area_m2 / cls.SECONDS_PER_HOUR,
        )

    @classmethod
    def flow_matches_controller_lift(
        cls, *, minimal_mass_flow_in_kg_per_second: float = 0.0, maximal_mass_flow_in_kg_per_second: float = math.inf
    ) -> "CirculationPump":
        """Return a controller-lift pump: the flow that carries the heat over the lift the controller asks for.

        For example, an electric heater's hot-water pump, which carries whatever power the heater regulates over the
        lift its controller asks for, is ``flow_matches_controller_lift()``, without bounds.

        Args:
            minimal_mass_flow_in_kg_per_second: The smallest flow, in kg/s, 0 or more.
            maximal_mass_flow_in_kg_per_second: The largest flow, in kg/s, infinity for none.

        Returns:
            The pump.

        Raises:
            ValueError: If the bounds are invalid.
        """
        return cls(
            mode=PumpMode.FLOW_MATCHES_CONTROLLER_LIFT,
            minimal_mass_flow_in_kg_per_second=minimal_mass_flow_in_kg_per_second,
            maximal_mass_flow_in_kg_per_second=maximal_mass_flow_in_kg_per_second,
        )

    @classmethod
    def flow_matches_controller_lift_sized_for(
        cls, *, maximal_heat_power_in_watt: float, minimal_lift_at_maximal_heat_in_kelvin: float
    ) -> "CirculationPump":
        """Return a controller-lift pump whose largest flow carries a maximal heat over a minimal lift.

        ``m_max = Q_max / (c lift_min)``. A boiler's pump is sized this way: on a lift close to 0 K the controller-lift flow
        would have no bound, so the pump stops at the flow that carries the full heat over 5 K. For example, 10800 W
        over 5 K give a largest flow of 0.517 kg/s.

        Args:
            maximal_heat_power_in_watt: The largest heat the device puts into the water, in W, above 0.
            minimal_lift_at_maximal_heat_in_kelvin: The smallest lift the pump allows at that heat, in K, above 0.

        Returns:
            The pump.

        Raises:
            ValueError: If the resulting largest flow is not finite or not above 0 kg/s.
        """
        return cls.flow_matches_controller_lift(
            maximal_mass_flow_in_kg_per_second=maximal_heat_power_in_watt
            / (cls.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * minimal_lift_at_maximal_heat_in_kelvin)
        )

    @classmethod
    def flow_matches_target_supply_temperature(
        cls, *, maximal_mass_flow_in_kg_per_second: float, minimal_mass_flow_in_kg_per_second: float = 0.0
    ) -> "CirculationPump":
        """Return a pump that brings the device's return up to a supply set temperature it is given in each call.

        For example, ``flow_matches_target_supply_temperature(maximal_mass_flow_in_kg_per_second=0.5)`` carrying 8360 W from a 40 °C
        return to a 60 °C set temperature runs 0.1 kg/s. The bounds are flows, so a clamp keeps the flow exactly at
        the bound and the supply misses the set temperature.

        Args:
            maximal_mass_flow_in_kg_per_second: The largest flow, in kg/s, finite and above 0.
            minimal_mass_flow_in_kg_per_second: The smallest flow, in kg/s, 0 or more.

        Returns:
            The pump.

        Raises:
            ValueError: If the bounds are invalid or the largest flow is not finite.
        """
        return cls(
            mode=PumpMode.FLOW_MATCHES_TARGET_SUPPLY_TEMPERATURE,
            minimal_mass_flow_in_kg_per_second=minimal_mass_flow_in_kg_per_second,
            maximal_mass_flow_in_kg_per_second=maximal_mass_flow_in_kg_per_second,
        )

    @classmethod
    def external_signal(cls, *, maximal_mass_flow_in_kg_per_second: float) -> "CirculationPump":
        """Return a pump whose flow a controller decides as a share of its largest flow.

        For example, ``external_signal(maximal_mass_flow_in_kg_per_second=0.5)`` runs 0.2 kg/s at a signal of 0.4.

        Args:
            maximal_mass_flow_in_kg_per_second: The flow at a signal of 1, in kg/s, finite and above 0.

        Returns:
            The pump.

        Raises:
            ValueError: If the largest flow is not finite or not above 0 kg/s.
        """
        return cls(mode=PumpMode.EXTERNAL_SIGNAL, maximal_mass_flow_in_kg_per_second=maximal_mass_flow_in_kg_per_second)

    def operating_point(  # pylint: disable=too-many-return-statements  # one return per mode and clamp, no extra call in the hot path
        self,
        *,
        heat_power_in_watt: float,
        return_temperature_in_celsius: float,
        lift_to_supply_set_temperature_in_kelvin: float = math.nan,
        supply_set_temperature_in_celsius: float = math.nan,
        signal: float = math.nan,
    ) -> PumpOperatingPoint:
        """Return the flow the pump runs for a heat, and the temperature the water leaves the device at.

        Per mode, with ``c`` the specific heat of water:

        * ``CONSTANT_FLOW``: ``m = m_design``, supply ``T_ret + Q / (m c)``.
        * ``FLOW_MATCHES_FIXED_LIFT``: ``m = Q / (c dT_target)``, supply ``T_ret + dT_target``. Outside the bounds the flow is the
          nearer bound and the supply ``T_ret + Q / (c m)``.
        * ``FLOW_MATCHES_CONTROLLER_LIFT``: the lift is the one asked for, kept between the lifts the heat gives at the
          largest and the smallest flow, ``Q / (c m_max)`` and ``Q / (c m_min)``; ``m = Q / (c lift)``, supply
          ``T_ret + lift``. A clamped lift gives the bound's flow to within rounding. The pump clamps the lift rather
          than the flow because that is the variable this mode holds; it also keeps the boiler's results bit for bit.
        * ``FLOW_MATCHES_TARGET_SUPPLY_TEMPERATURE``: ``m = Q / (c (T_set - T_ret))``, supply ``T_set``. Outside the bounds the flow
          is the nearer bound and the supply ``T_ret + Q / (c m)``. Without heat the pump runs its smallest flow and
          supplies the return. If the heat cannot reach the set temperature (the return at or above it while the
          device heats, or below it while the device cools), the pump runs its largest flow, clamped.
        * ``EXTERNAL_SIGNAL``: ``m = signal m_max``, the signal kept between 0 and 1, supply ``T_ret + Q / (c m)``.

        Without flow the supply is the return. For example, a fixed-lift pump at 20 K and 0.0056 to 0.0444 kg/s
        carrying 1672 W from 40 °C runs 0.02 kg/s and supplies 60 °C; carrying 200 W it runs 0.0056 kg/s, clamped,
        and supplies 48.6 °C.

        Args:
            heat_power_in_watt: The heat the device puts into the water, in W; negative for heat it takes out.
            return_temperature_in_celsius: The temperature the water enters the device at, in °C.
            lift_to_supply_set_temperature_in_kelvin: ``FLOW_MATCHES_CONTROLLER_LIFT`` only: the lift to the supply set
                temperature the device's controller asks for, in K, above 0. Not read by the other modes.
            supply_set_temperature_in_celsius: ``FLOW_MATCHES_TARGET_SUPPLY_TEMPERATURE`` only: the temperature the supply should
                reach, in °C. Not read by the other modes.
            signal: ``EXTERNAL_SIGNAL`` only: the share of the largest flow, dimensionless, 0 to 1. Not read by the
                other modes.

        Returns:
            The flow, the supply temperature and whether the flow is clamped.
        """
        specific_heat = self.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN
        mode = self.mode
        if mode is PumpMode.FLOW_MATCHES_CONTROLLER_LIFT:
            lift_in_kelvin = max(
                lift_to_supply_set_temperature_in_kelvin,
                heat_power_in_watt / (specific_heat * self.maximal_mass_flow_in_kg_per_second),
            )
            smallest = self.minimal_mass_flow_in_kg_per_second
            if smallest > 0.0:
                lift_in_kelvin = min(lift_in_kelvin, heat_power_in_watt / (specific_heat * smallest))
            return PumpOperatingPoint(
                heat_power_in_watt / (specific_heat * lift_in_kelvin),
                return_temperature_in_celsius + lift_in_kelvin,
                lift_in_kelvin != lift_to_supply_set_temperature_in_kelvin,
            )
        if mode is PumpMode.FLOW_MATCHES_FIXED_LIFT:
            mass_flow_in_kg_per_second = heat_power_in_watt / (specific_heat * self.target_lift_in_kelvin)
            if mass_flow_in_kg_per_second > self.maximal_mass_flow_in_kg_per_second:
                bound_in_kg_per_second = self.maximal_mass_flow_in_kg_per_second
            elif mass_flow_in_kg_per_second < self.minimal_mass_flow_in_kg_per_second:
                bound_in_kg_per_second = self.minimal_mass_flow_in_kg_per_second
            else:
                return PumpOperatingPoint(
                    mass_flow_in_kg_per_second, return_temperature_in_celsius + self.target_lift_in_kelvin, False
                )
            return PumpOperatingPoint(
                bound_in_kg_per_second,
                self.supply_temperature_in_celsius(
                    heat_power_in_watt=heat_power_in_watt,
                    return_temperature_in_celsius=return_temperature_in_celsius,
                    mass_flow_in_kg_per_second=bound_in_kg_per_second,
                ),
                True,
            )
        if mode is PumpMode.FLOW_MATCHES_TARGET_SUPPLY_TEMPERATURE:
            if heat_power_in_watt == 0.0:
                return PumpOperatingPoint(
                    self.minimal_mass_flow_in_kg_per_second,
                    return_temperature_in_celsius,
                    self.minimal_mass_flow_in_kg_per_second > 0.0,
                )
            lift_in_kelvin = supply_set_temperature_in_celsius - return_temperature_in_celsius
            if heat_power_in_watt * lift_in_kelvin > 0.0:
                mass_flow_in_kg_per_second = heat_power_in_watt / (specific_heat * lift_in_kelvin)
            else:
                mass_flow_in_kg_per_second = math.inf
            bound_in_kg_per_second = min(
                max(mass_flow_in_kg_per_second, self.minimal_mass_flow_in_kg_per_second),
                self.maximal_mass_flow_in_kg_per_second,
            )
            if bound_in_kg_per_second == mass_flow_in_kg_per_second:
                return PumpOperatingPoint(mass_flow_in_kg_per_second, supply_set_temperature_in_celsius, False)
            return PumpOperatingPoint(
                bound_in_kg_per_second,
                self.supply_temperature_in_celsius(
                    heat_power_in_watt=heat_power_in_watt,
                    return_temperature_in_celsius=return_temperature_in_celsius,
                    mass_flow_in_kg_per_second=bound_in_kg_per_second,
                ),
                True,
            )
        if mode is PumpMode.CONSTANT_FLOW:
            return PumpOperatingPoint(
                self.design_mass_flow_in_kg_per_second,
                self.supply_temperature_in_celsius(
                    heat_power_in_watt=heat_power_in_watt,
                    return_temperature_in_celsius=return_temperature_in_celsius,
                    mass_flow_in_kg_per_second=self.design_mass_flow_in_kg_per_second,
                ),
                False,
            )
        share = min(max(signal, 0.0), 1.0)
        mass_flow_in_kg_per_second = share * self.maximal_mass_flow_in_kg_per_second
        return PumpOperatingPoint(
            mass_flow_in_kg_per_second,
            self.supply_temperature_in_celsius(
                heat_power_in_watt=heat_power_in_watt,
                return_temperature_in_celsius=return_temperature_in_celsius,
                mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
            ),
            share != signal,
        )

    @classmethod
    def supply_temperature_in_celsius(
        cls, *, heat_power_in_watt: float, return_temperature_in_celsius: float, mass_flow_in_kg_per_second: float
    ) -> float:
        """Return the temperature water leaves at after a heat raised it at a flow: ``T_ret + Q / (m c)``.

        Without flow (0 kg/s) the water does not move and leaves at the return. For example, 1672 W at 0.02 kg/s from
        40 °C supply 60 °C.

        Args:
            heat_power_in_watt: The heat put into the water, in W.
            return_temperature_in_celsius: The temperature the water enters at, in °C.
            mass_flow_in_kg_per_second: The flow, in kg/s, 0 or more.

        Returns:
            The supply temperature, in °C.
        """
        if mass_flow_in_kg_per_second <= 0.0:
            return return_temperature_in_celsius
        return return_temperature_in_celsius + heat_power_in_watt / (
            mass_flow_in_kg_per_second * cls.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN
        )
