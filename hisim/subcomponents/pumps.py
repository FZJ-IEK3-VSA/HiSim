"""The circulation pumps of the devices that pump their own water loop: one class per flow law.

A boiler, a solar collector, a district-heating substation, an electric heater, a heat pump in its fixed-flow mode and
the heat distribution system each move the water of a loop they own. Each holds one pump and asks it, in every pass,
which flow it runs and where its outlet ends. The pumps are a strategy: a device picks its pump class once, when it is
built, and calls its ``operating_point``, which takes exactly the values that law reads. Each pump is a subcomponent
(``system_docs/component_design_principles.md``, section C): built once from the device's configuration, without
ports, never seen by the simulator.

* :class:`ConstantFlowPump`: the design flow, whatever the heat (the heat distribution system, a heat pump's fixed flow).
* :class:`FixedLiftPump`: the "temperature-sensor pump", the flow that carries the heat over a fixed lift (the solar
  collector, district heating's hot water).
* :class:`ControllerLiftPump`: the flow that carries the heat over the lift the device's controller asks for in each
  step (the boiler, the electric heater's hot water).
* :class:`TargetSupplyTemperaturePump`: the flow that brings the device's own return up to a supply set temperature.

A device on a loop that another device pumps holds no pump for that loop; it reads the flow from its input (the
boiler behind the heat distribution system, for example).
"""

import math
from dataclasses import dataclass
from typing import ClassVar, NamedTuple

from hisim import hydronics
from hisim.subcomponents.base import Subcomponent


class PumpOperatingPoint(NamedTuple):
    """The flow a circulation pump runs in one pass and the temperature its water leaves the device at.

    For example, a pump at a 20 K target lift that carries 1672 W from a 40 °C return runs 0.02 kg/s and supplies
    60 °C. A ``NamedTuple``, not a dataclass, because the pump returns one in every pass of every step
    (``coding_style.md``, §2 rule 12).

    Attributes:
        mass_flow_in_kg_per_second: The flow through the device, in kg/s, 0 or more.
        supply_temperature_in_celsius: The temperature the water leaves the device at, before any limit the device
            sets on it, in °C: the return plus the lift the flow carries the heat over.
        flow_is_clamped: Whether the flow the pump's law asks for lay outside the pump's bounds, so that the pump runs
            the nearer bound and the lift follows from the heat at that flow.
    """

    mass_flow_in_kg_per_second: float
    supply_temperature_in_celsius: float
    flow_is_clamped: bool


class PumpedWater:
    """What every circulation pump knows about the water it moves: its specific heat, its outlet and its flow bounds.

    The pumps call these functions; they do not inherit them (``coding_style.md``, §2 rule 10). Every pump supplies
    at the outlet law :meth:`supply_temperature_in_celsius`, and the bounded pumps (:class:`FixedLiftPump`,
    :class:`ControllerLiftPump`, :class:`TargetSupplyTemperaturePump`) check and keep their bounds with the other two.
    """

    #: The specific heat of the water the pumps move, in J/(kg K).
    SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN: ClassVar[float] = hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K

    @classmethod
    def supply_temperature_in_celsius(
        cls, *, heat_power_in_watt: float, return_temperature_in_celsius: float, mass_flow_in_kg_per_second: float
    ) -> float:
        """Return the temperature in °C water leaves at after a heat in W raised it at a flow in kg/s, ``T_ret + Q / (m c)``.

        Without flow (0 kg/s or less) the water does not move and leaves at the return. For example, 1672 W at 0.02 kg/s
        from 40 °C supply 60 °C.
        """
        if mass_flow_in_kg_per_second <= 0.0:
            return return_temperature_in_celsius
        return return_temperature_in_celsius + heat_power_in_watt / (
            mass_flow_in_kg_per_second * cls.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN
        )

    @staticmethod
    def check_mass_flow_bounds(*, minimal_mass_flow_in_kg_per_second: float, maximal_mass_flow_in_kg_per_second: float, pump_name: str) -> None:
        """Refuse bounds a pump cannot keep its flow between: ``0 <= m_min <= m_max``, ``m_max > 0``, both in kg/s.

        For example, 5 to 40 kg/(m² h) on 4 m² (0.0056 to 0.0444 kg/s) is accepted; a smallest flow of -0.1 kg/s, a
        largest of 0 kg/s or a smallest above the largest is refused. A largest flow of infinity is a pump without an
        upper bound.

        Args:
            minimal_mass_flow_in_kg_per_second: The smallest flow the pump runs, in kg/s.
            maximal_mass_flow_in_kg_per_second: The largest flow the pump runs, in kg/s.
            pump_name: The pump class, named in the error message.

        Raises:
            ValueError: If the smallest flow is negative or not finite, the largest is NaN or not above 0, or the
                smallest exceeds the largest.
        """
        smallest = minimal_mass_flow_in_kg_per_second
        largest = maximal_mass_flow_in_kg_per_second
        if not math.isfinite(smallest) or smallest < 0.0:
            raise ValueError(f"A {pump_name} needs a finite smallest flow of at least 0 kg/s, got {smallest!r}.")
        if math.isnan(largest) or largest <= 0.0:
            raise ValueError(f"A {pump_name} needs a largest flow above 0 kg/s (infinity for none), got {largest!r}.")
        if smallest > largest:
            raise ValueError(f"A {pump_name}'s smallest flow {smallest!r} kg/s exceeds its largest flow {largest!r} kg/s.")

    @classmethod
    def operating_point_at_nearer_bound(
        cls,
        *,
        mass_flow_in_kg_per_second: float,
        heat_power_in_watt: float,
        return_temperature_in_celsius: float,
        minimal_mass_flow_in_kg_per_second: float,
        maximal_mass_flow_in_kg_per_second: float,
    ) -> PumpOperatingPoint:
        """Return the operating point of a pump whose law asks for a flow outside its bounds: the nearer bound, clamped.

        The pump runs the bound nearer to the flow asked for, and the water leaves at the return plus the heat over
        ``c`` times that bound (:meth:`supply_temperature_in_celsius`), not at the lift the law aimed at. For example,
        a pump bounded to 0.005 to 0.05 kg/s asked for 0.0024 kg/s by 200 W from 40 °C runs 0.005 kg/s and supplies
        49.6 °C. A bounded pump calls this only after it has found its flow outside the bounds, so that a flow within
        them costs no extra call.

        Args:
            mass_flow_in_kg_per_second: The flow the pump's law asks for, in kg/s, outside the bounds.
            heat_power_in_watt: The heat the device puts into the water, in W.
            return_temperature_in_celsius: The temperature the water enters the device at, in °C.
            minimal_mass_flow_in_kg_per_second: The pump's smallest flow, in kg/s.
            maximal_mass_flow_in_kg_per_second: The pump's largest flow, in kg/s.

        Returns:
            The bound, the supply at that bound and ``flow_is_clamped=True``.
        """
        bound_in_kg_per_second = min(max(mass_flow_in_kg_per_second, minimal_mass_flow_in_kg_per_second), maximal_mass_flow_in_kg_per_second)
        return PumpOperatingPoint(
            mass_flow_in_kg_per_second=bound_in_kg_per_second,
            supply_temperature_in_celsius=cls.supply_temperature_in_celsius(
                heat_power_in_watt=heat_power_in_watt,
                return_temperature_in_celsius=return_temperature_in_celsius,
                mass_flow_in_kg_per_second=bound_in_kg_per_second,
            ),
            flow_is_clamped=True,
        )


@dataclass(frozen=True, kw_only=True)
class ConstantFlowPump(Subcomponent):
    """A pump that runs its design flow whatever the heat, so its outlet follows the heat.

    ``m = m_design``, supply ``T_ret + Q / (m c)``. For example, the heat distribution system pumps its design flow
    with one, and a heat pump in its fixed-flow mode its nominal flow.

    Attributes:
        design_mass_flow_in_kg_per_second: The flow the pump runs, in kg/s, finite and above 0.
    """

    design_mass_flow_in_kg_per_second: float

    def __post_init__(self) -> None:
        """Refuse a design flow that is not finite or not above 0 kg/s, so a bad pump fails when its device is built.

        Raises:
            ValueError: If the design flow is not finite or not above 0 kg/s.
        """
        design = self.design_mass_flow_in_kg_per_second
        if not math.isfinite(design) or design <= 0.0:
            raise ValueError(f"A ConstantFlowPump needs a finite design flow above 0 kg/s, got {design!r}: it runs that flow whatever the heat.")

    def operating_point(self, *, heat_power_in_watt: float, return_temperature_in_celsius: float) -> PumpOperatingPoint:
        """Return the design flow in kg/s and the outlet in °C a heat in W gives on it from a return in °C, never clamped."""
        return PumpOperatingPoint(
            mass_flow_in_kg_per_second=self.design_mass_flow_in_kg_per_second,
            supply_temperature_in_celsius=PumpedWater.supply_temperature_in_celsius(
                heat_power_in_watt=heat_power_in_watt,
                return_temperature_in_celsius=return_temperature_in_celsius,
                mass_flow_in_kg_per_second=self.design_mass_flow_in_kg_per_second,
            ),
            flow_is_clamped=False,
        )


@dataclass(frozen=True, kw_only=True)
class FixedLiftPump(Subcomponent):
    """A temperature-sensor pump: the flow that carries the heat over a fixed lift, within a smallest and a largest flow.

    ``m = Q / (c dT_target)``, supply ``T_ret + dT_target``. Outside its bounds it runs the nearer bound and the
    outlet follows the heat at that flow (:meth:`PumpedWater.operating_point_at_nearer_bound`); the result says
    ``flow_is_clamped``. A device whose heat depends on its own outlet (a solar collector) evaluates its heat again at
    the clamped flow. For example, a solar pump with a 20 K target lift and 0.0056 to 0.0444 kg/s is
    ``FixedLiftPump(target_lift_in_kelvin=20.0, minimal_mass_flow_in_kg_per_second=0.0056,
    maximal_mass_flow_in_kg_per_second=0.0444)``.

    Attributes:
        target_lift_in_kelvin: The lift the pump holds, in K, finite and above 0.
        minimal_mass_flow_in_kg_per_second: The smallest flow the pump runs, in kg/s, 0 or more.
        maximal_mass_flow_in_kg_per_second: The largest flow the pump runs, in kg/s, not below the smallest; infinite
            for a pump without an upper bound.
    """

    target_lift_in_kelvin: float
    minimal_mass_flow_in_kg_per_second: float = 0.0
    maximal_mass_flow_in_kg_per_second: float = math.inf

    def __post_init__(self) -> None:
        """Refuse a target lift that is not finite or not above 0 K, and bounds the pump cannot keep.

        Raises:
            ValueError: If the lift is not finite or not above 0 K, or the bounds fail
                :meth:`PumpedWater.check_mass_flow_bounds`.
        """
        lift = self.target_lift_in_kelvin
        if not math.isfinite(lift) or lift <= 0.0:
            raise ValueError(f"A FixedLiftPump needs a finite target lift above 0 K, got {lift!r}: its flow is the heat over c times that lift.")
        PumpedWater.check_mass_flow_bounds(
            minimal_mass_flow_in_kg_per_second=self.minimal_mass_flow_in_kg_per_second,
            maximal_mass_flow_in_kg_per_second=self.maximal_mass_flow_in_kg_per_second,
            pump_name=type(self).__name__,
        )

    def operating_point(self, *, heat_power_in_watt: float, return_temperature_in_celsius: float) -> PumpOperatingPoint:
        """Return the flow that carries a heat over the target lift, and the temperature the water leaves at.

        For example, at 20 K and 0.0056 to 0.0444 kg/s, 1672 W from 40 °C run 0.02 kg/s and supply 60 °C; 200 W run
        0.0056 kg/s, clamped, and supply 48.5 °C. A heat taken out (negative) asks for a negative flow, so the pump
        runs its smallest flow.

        Args:
            heat_power_in_watt: The heat the device puts into the water, in W; negative for heat it takes out.
            return_temperature_in_celsius: The temperature the water enters the device at, in °C.

        Returns:
            The flow, the supply temperature and whether the flow is clamped.
        """
        mass_flow_in_kg_per_second = heat_power_in_watt / (PumpedWater.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * self.target_lift_in_kelvin)
        if self.minimal_mass_flow_in_kg_per_second <= mass_flow_in_kg_per_second <= self.maximal_mass_flow_in_kg_per_second:
            return PumpOperatingPoint(
                mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
                supply_temperature_in_celsius=return_temperature_in_celsius + self.target_lift_in_kelvin,
                flow_is_clamped=False,
            )
        return PumpedWater.operating_point_at_nearer_bound(
            mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
            heat_power_in_watt=heat_power_in_watt,
            return_temperature_in_celsius=return_temperature_in_celsius,
            minimal_mass_flow_in_kg_per_second=self.minimal_mass_flow_in_kg_per_second,
            maximal_mass_flow_in_kg_per_second=self.maximal_mass_flow_in_kg_per_second,
        )


@dataclass(frozen=True, kw_only=True)
class ControllerLiftPump(Subcomponent):
    """A pump that carries the heat over the lift the device's controller asks for in each step, within bounds.

    ``m = Q / (c lift)``, supply ``T_ret + lift``. The controller computes the lift as its supply set temperature
    minus the temperature it reads. The pump keeps the lift between the rises the heat gives at its largest and its
    smallest flow, so its flow stays within its bounds. For example, the boiler's pump (:meth:`sized_for`) and the
    electric heater's hot-water pump, without bounds, are of this kind.

    Attributes:
        minimal_mass_flow_in_kg_per_second: The smallest flow the pump runs, in kg/s, 0 or more.
        maximal_mass_flow_in_kg_per_second: The largest flow the pump runs, in kg/s, not below the smallest; infinite
            for a pump without an upper bound.
    """

    minimal_mass_flow_in_kg_per_second: float = 0.0
    maximal_mass_flow_in_kg_per_second: float = math.inf

    def __post_init__(self) -> None:
        """Refuse bounds the pump cannot keep (:meth:`PumpedWater.check_mass_flow_bounds`), so a bad pump fails when built."""
        PumpedWater.check_mass_flow_bounds(
            minimal_mass_flow_in_kg_per_second=self.minimal_mass_flow_in_kg_per_second,
            maximal_mass_flow_in_kg_per_second=self.maximal_mass_flow_in_kg_per_second,
            pump_name=type(self).__name__,
        )

    @classmethod
    def sized_for(cls, *, maximal_heat_power_in_watt: float, minimal_lift_at_maximal_heat_in_kelvin: float) -> "ControllerLiftPump":
        """Return a controller-lift pump whose largest flow carries a maximal heat over a minimal lift.

        ``m_max = Q_max / (c lift_min)``. A boiler's pump is sized this way: on a lift close to 0 K the controller-lift
        flow would have no bound, so the pump stops at the flow that carries the full heat over 5 K. For example,
        10800 W over 5 K give a largest flow of 0.517 kg/s.

        Args:
            maximal_heat_power_in_watt: The largest heat the device puts into the water, in W, finite and above 0.
            minimal_lift_at_maximal_heat_in_kelvin: The smallest lift the pump allows at that heat, in K, above 0.

        Returns:
            The pump, without a smallest flow.

        Raises:
            ValueError: If the lift is NaN or not above 0 K, or the resulting largest flow is not finite or not above
                0 kg/s.
        """
        if math.isnan(minimal_lift_at_maximal_heat_in_kelvin) or minimal_lift_at_maximal_heat_in_kelvin <= 0.0:
            raise ValueError(
                f"A ControllerLiftPump sized for a minimal lift of {minimal_lift_at_maximal_heat_in_kelvin!r} K has no largest flow: "
                "the lift must be above 0 K."
            )
        maximal_mass_flow_in_kg_per_second = maximal_heat_power_in_watt / (
            PumpedWater.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * minimal_lift_at_maximal_heat_in_kelvin
        )
        if not math.isfinite(maximal_mass_flow_in_kg_per_second) or maximal_mass_flow_in_kg_per_second <= 0.0:
            raise ValueError(
                f"A ControllerLiftPump sized for {maximal_heat_power_in_watt!r} W over {minimal_lift_at_maximal_heat_in_kelvin!r} K "
                f"has no finite largest flow above 0 kg/s, got {maximal_mass_flow_in_kg_per_second!r}: the heat must be finite and above 0."
            )
        return cls(maximal_mass_flow_in_kg_per_second=maximal_mass_flow_in_kg_per_second)

    def operating_point(
        self, *, heat_power_in_watt: float, return_temperature_in_celsius: float, lift_to_supply_set_temperature_in_kelvin: float
    ) -> PumpOperatingPoint:
        """Return the flow that carries a heat over the lift the controller asks for, and the temperature the water leaves at.

        The lift is kept between the rises the heat gives at the largest and the smallest flow, ``Q / (c m_max)`` and
        ``Q / (c m_min)``; then ``m = Q / (c lift)`` and the supply is ``T_ret + lift``. A clamped lift gives the
        bound's flow to within rounding. The pump clamps the lift rather than the flow because that is the variable
        this law holds; it also keeps the boiler's results bit for bit. For example, 1500 W over a 25 K lift from
        50 °C run 0.0144 kg/s and supply 75 °C.

        Args:
            heat_power_in_watt: The heat the device puts into the water, in W.
            return_temperature_in_celsius: The temperature the water enters the device at, in °C.
            lift_to_supply_set_temperature_in_kelvin: The lift to the supply set temperature the controller asks for,
                in K, above 0.

        Returns:
            The flow, the supply temperature and whether the lift, and so the flow, is clamped.
        """
        specific_heat = PumpedWater.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN
        lift_in_kelvin = max(lift_to_supply_set_temperature_in_kelvin, heat_power_in_watt / (specific_heat * self.maximal_mass_flow_in_kg_per_second))
        smallest = self.minimal_mass_flow_in_kg_per_second
        if smallest > 0.0:
            lift_in_kelvin = min(lift_in_kelvin, heat_power_in_watt / (specific_heat * smallest))
        return PumpOperatingPoint(
            mass_flow_in_kg_per_second=heat_power_in_watt / (specific_heat * lift_in_kelvin),
            supply_temperature_in_celsius=return_temperature_in_celsius + lift_in_kelvin,
            flow_is_clamped=lift_in_kelvin != lift_to_supply_set_temperature_in_kelvin,
        )


@dataclass(frozen=True, kw_only=True)
class TargetSupplyTemperaturePump(Subcomponent):
    """A pump that brings the device's own return up to a supply set temperature, within a smallest and a largest flow.

    ``m = Q / (c (T_set - T_ret))``, supply ``T_set``. Unlike :class:`ControllerLiftPump`, the lift is computed from
    the return the device sees in the pass, not from a temperature the controller read. No recorded system uses it
    yet; bead hisim-hydronics-fxix.29 tries it in the boiler, the electric heater and district heating. For example,
    ``TargetSupplyTemperaturePump(maximal_mass_flow_in_kg_per_second=0.5)`` runs 0.1 kg/s for 8360 W from 40 °C to
    60 °C.

    Attributes:
        maximal_mass_flow_in_kg_per_second: The largest flow the pump runs, in kg/s, finite and not below the
            smallest: the flow it runs when the heat cannot reach the set temperature.
        minimal_mass_flow_in_kg_per_second: The smallest flow the pump runs, in kg/s, 0 or more.
    """

    maximal_mass_flow_in_kg_per_second: float
    minimal_mass_flow_in_kg_per_second: float = 0.0

    def __post_init__(self) -> None:
        """Refuse an infinite largest flow and bounds the pump cannot keep, so a bad pump fails when its device is built.

        Raises:
            ValueError: If the largest flow is infinite, or the bounds fail :meth:`PumpedWater.check_mass_flow_bounds`.
        """
        if math.isinf(self.maximal_mass_flow_in_kg_per_second):
            raise ValueError(
                f"A TargetSupplyTemperaturePump needs a finite largest flow, got {self.maximal_mass_flow_in_kg_per_second!r}: "
                "it runs that flow when the heat cannot reach the set temperature."
            )
        PumpedWater.check_mass_flow_bounds(
            minimal_mass_flow_in_kg_per_second=self.minimal_mass_flow_in_kg_per_second,
            maximal_mass_flow_in_kg_per_second=self.maximal_mass_flow_in_kg_per_second,
            pump_name=type(self).__name__,
        )

    def operating_point(
        self, *, heat_power_in_watt: float, return_temperature_in_celsius: float, supply_set_temperature_in_celsius: float
    ) -> PumpOperatingPoint:
        """Return the flow that brings a return up to a supply set temperature with a heat, and the temperature it leaves at.

        Outside the bounds the flow is the nearer bound and the supply ``T_ret + Q / (c m)``
        (:meth:`PumpedWater.operating_point_at_nearer_bound`). Without heat the pump runs its smallest flow and supplies
        the return. If the heat cannot reach the set temperature (the return at or above it while the device heats, or
        below it while the device cools), the pump runs its largest flow, clamped. For example, 8360 W from 40 °C to
        60 °C run 0.1 kg/s.

        Args:
            heat_power_in_watt: The heat the device puts into the water, in W; negative for heat it takes out.
            return_temperature_in_celsius: The temperature the water enters the device at, in °C.
            supply_set_temperature_in_celsius: The temperature the supply should reach, in °C.

        Returns:
            The flow, the supply temperature and whether the flow is clamped.
        """
        smallest = self.minimal_mass_flow_in_kg_per_second
        if heat_power_in_watt == 0.0:
            return PumpOperatingPoint(
                mass_flow_in_kg_per_second=smallest, supply_temperature_in_celsius=return_temperature_in_celsius, flow_is_clamped=smallest > 0.0
            )
        lift_in_kelvin = supply_set_temperature_in_celsius - return_temperature_in_celsius
        if heat_power_in_watt * lift_in_kelvin > 0.0:
            mass_flow_in_kg_per_second = heat_power_in_watt / (PumpedWater.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * lift_in_kelvin)
        else:
            mass_flow_in_kg_per_second = math.inf  # the heat cannot reach the set temperature at any flow
        if smallest <= mass_flow_in_kg_per_second <= self.maximal_mass_flow_in_kg_per_second:
            return PumpOperatingPoint(
                mass_flow_in_kg_per_second=mass_flow_in_kg_per_second, supply_temperature_in_celsius=supply_set_temperature_in_celsius, flow_is_clamped=False
            )
        return PumpedWater.operating_point_at_nearer_bound(
            mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
            heat_power_in_watt=heat_power_in_watt,
            return_temperature_in_celsius=return_temperature_in_celsius,
            minimal_mass_flow_in_kg_per_second=smallest,
            maximal_mass_flow_in_kg_per_second=self.maximal_mass_flow_in_kg_per_second,
        )
