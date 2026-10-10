"""Hydronics: the water arithmetic that every hydronic circuit and every fully mixed node shares.

A **circuit** is one closed water loop between two components: a mass flow ``m >= 0`` (kg/s) that one of them
pumps, a supply temperature (the water leaving the supply owner, °C) and a return temperature (the water coming
back to it, °C). A **node** is a component that holds water and integrates its temperature over a step, such as a
hot-water tank; it is fully mixed, so its water has one uniform temperature at every moment. Components that
exchange heat through water publish only these port quantities, and the heat is derived from them by the
functions here, so both ends of a circuit book the same number.

The module lives in ``hisim/`` rather than ``hisim/components/``: :class:`hisim.energy_port.HydronicPort` derives
a circuit's energy with :func:`circuit_heat_kwh`, and ``hisim/energy_port.py`` is imported by
``hisim/component.py``, the base of every component, which must not import the component package built on it.
The module imports nothing from HiSim.

Every function refuses what it cannot compute with a :class:`HydronicsError` subclass, which is a ``ValueError``:
a negative mass flow, a negative loss coefficient, a heat capacity or a timestep that is not positive, a value that
is not finite (NaN or infinite), a valve whose warm temperature is not above its cold one. A result that overflows
to an infinity or a NaN from finite arguments (a heat ``m c dT dt`` beyond the float range, say) is refused with
:class:`NonFiniteValueError` naming the quantity, never returned. Nothing is clipped.

Names keep this module's short unit suffixes: ``_w`` (W), ``_c`` (°C), ``_j`` (J), ``_kwh`` (kWh), ``_s`` (s),
``_kg_per_s`` (kg/s), ``_w_per_k`` (W/K), ``_j_per_k`` (J/K).

Circuits
--------

A circuit's heat flow is ``m c (T_sup - T_ret)`` (:func:`circuit_power_w`) and its heat over a step of ``dt``
seconds ``m c (T_sup - T_ret) dt`` (:func:`circuit_heat_j`, :func:`circuit_heat_kwh`). It is positive when the
supply owner heats the receiving side and negative for a cooling circuit with ``T_sup < T_ret``; the direction is
the circuit's topology, so a mass flow is never negative and nothing is mirrored.

A generator that holds a lift ``dT`` supplies ``T_ret + dT`` up to a supply limit; above the limit the supply stops
at the limit, or at the return if that is hotter (:func:`throttled_supply_temperature_c`). A hot-water circuit's
limit is the lower of the generator's maximum supply temperature and its controller's set temperature
(:func:`capped_hot_water_supply_temperature_c`).

The mixed node
--------------

A fully mixed node of heat capacity ``C`` (J/K) with inflows ``m_i`` at constant temperatures ``T_i`` and a loss
``UA`` (W/K) to ``T_amb`` obeys ``C dT/dt = sum_i m_i c (T_i - T) - UA (T - T_amb)``: each inflow mixes in at once,
and the same mass leaves at the node's temperature. With ``G = sum_i m_i c + UA``,
``T_inf = (sum_i m_i c T_i + UA T_amb) / G``, ``k = G / C`` and ``a = k dt``:

* ``T_end = T_inf + (T0 - T_inf) e^(-a)``
* ``T_mean = T_inf + (T0 - T_inf) (1 - e^(-a)) / a``

:meth:`MixedNode.step` evaluates both through ``phi(a) = (1 - e^(-a)) / a`` (:func:`relaxation_mean_factor`) as
``T_mean = T_inf + (T0 - T_inf) phi`` and ``T_end = T0 + (T_inf - T0) a phi``; the second form keeps the change
``T_end - T0`` free of cancellation when ``a`` is small. ``phi`` is ``-expm1(-a) / a`` for
``a >= MixedNode.SMALL_A_THRESHOLD`` (1e-4) and the Taylor polynomial ``1 - a/2 + a^2/6 - a^3/24`` below it, so
``a = 0`` (no flow and no loss: ``G = 0``) gives ``phi = 1`` and ``T_mean = T_end = T0`` without a division. The
Taylor remainder below the threshold is at most ``a^4/120 < 8.4e-19``, under a fortieth of an ulp of ``phi ~ 1``.

The heat each inflow brings is ``m_i c (T_i - T_mean) dt`` and the loss ``UA (T_mean - T_amb) dt``; their
difference is ``C (T_end - T0)`` exactly, and ``T_mean`` and ``T_end`` never leave the range of ``T0``, the inflow
temperatures and ``T_amb``. A node therefore publishes ``T_mean`` as the return of the circuits it receives: the
heat a circuit brings, derived from its flow, its supply and that return, is then the heat the node integrated.

The tap valve
-------------

:func:`mixing_valve_draw` is a thermostatic mixing valve's arithmetic for a hot-water demand ``m_d`` at ``T_warm``
from a tank at ``T`` refilled with mains water at ``T_cold``. Because the hot water it lets out depends on the
tank's step mean, a tank solves the valve on its own step mean with :func:`solve_bracketed_fixed_point`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, ClassVar, Iterable, Sequence, Tuple, TypeVar


class UnitConversion:

    """The factors between the energy and volume units the hydronic components convert between.

    A namespace of constants, so that no component writes ``3.6e3`` or ``1000.0`` into a formula. For example, a
    heat flow of 2000 W over a 900 s step is ``2000 * 900 / UnitConversion.JOULES_PER_WATT_HOUR = 500`` Wh.
    """

    #: Joules in one watt hour, J/Wh: a power in W times a step in s over this is an energy in Wh.
    JOULES_PER_WATT_HOUR: ClassVar[float] = 3.6e3

    #: Joules in one kilowatt hour, J/kWh.
    JOULES_PER_KILOWATT_HOUR: ClassVar[float] = 3.6e6

    #: Watt hours in one kilowatt hour, Wh/kWh.
    WATT_HOURS_PER_KILOWATT_HOUR: ClassVar[float] = 1000.0

    #: Litres in one cubic metre, l/m³.
    LITERS_PER_CUBIC_METER: ClassVar[float] = 1000.0


class Water:

    """The properties of liquid water that every hydronic circuit and node computes with.

    One place for both values, so that a generator, a tank and the heat distribution system turn flows into heat
    and volumes into masses with the same numbers. ``PhysicsConfig``'s water entry in
    ``hisim/components/configuration.py`` reads them from here. For example, a 250 l tank holds
    ``250 * Water.DENSITY_KG_PER_LITER = 248`` kg of water.
    """

    #: Specific heat capacity of water, J/(kg K).
    SPECIFIC_HEAT_J_PER_KG_K: ClassVar[float] = 4180.0

    #: Density of water at 40 °C, kg/m³, the mean temperature of a domestic hot-water or heating storage (source:
    #: https://www.internetchemie.info/chemie-lexikon/daten/w/wasser-dichtetabelle.php).
    DENSITY_KG_PER_M3: ClassVar[float] = 992.0

    #: The same density in kg/l, the unit storage volumes are configured in.
    DENSITY_KG_PER_LITER: ClassVar[float] = DENSITY_KG_PER_M3 / UnitConversion.LITERS_PER_CUBIC_METER


class HydronicsError(ValueError):

    """A value the hydronic arithmetic cannot compute with.

    The base of every refusal of this module. It is a ``ValueError``, so a caller that validates its input by
    catching ``ValueError`` catches these too.
    """


class NonFiniteValueError(HydronicsError):

    """A temperature, flow, coefficient or duration is NaN or infinite, or a result overflowed the float range.

    The message names the argument or the computed quantity, so the value that broke the step can be found.
    """


class NegativeMassFlowError(HydronicsError):

    """A mass flow is negative.

    A circuit's direction is its topology (from the supply owner to the receiver), never the sign of its flow, so
    a negative flow is always an error of the caller.
    """


class NegativeHeatLossCoefficientError(HydronicsError):

    """A node's loss coefficient ``UA`` is negative.

    A negative loss coefficient would make the node gain heat from a colder room, which no insulation does.
    """


class NonPositiveHeatCapacityError(HydronicsError):

    """A node's heat capacity is zero or negative.

    The node step divides by the heat capacity, so a node without water cannot be integrated.
    """


class NonPositiveTimestepError(HydronicsError):

    """A step duration is zero or negative.

    Every heat of this module is a heat flow times the step duration, which must be positive.
    """


class ValveTemperatureOrderError(HydronicsError):

    """A mixing valve's warm temperature is not above its cold one.

    The valve blends tank water with mains water to reach the warm temperature, which is only possible when the
    warm temperature lies above the mains temperature.
    """


class NegativeDemandError(HydronicsError):

    """A hot-water demand is negative.

    A demand is the warm water the household asks for, which is never negative.
    """


class SetTemperatureAboveMaximumError(HydronicsError):

    """A hot-water controller's set temperature lies above its generator's maximum supply temperature.

    The generator's supply is capped at its maximum, so a tank charged towards a set temperature above it never
    reaches the set temperature and the charge never ends; the configuration of the pair is refused instead.
    """


class FixedPointNotFoundError(HydronicsError):

    """A bracketed fixed-point solve did not converge within its evaluation budget.

    The solve stops with this error rather than returning a guess, so a tank never books heat from an
    unconverged step mean.
    """


def _finite(name: str, value: float) -> float:
    """Return ``value`` as a float, refused when it is NaN or infinite.

    Every argument of this module passes through here, so a ``Decimal`` or a NumPy scalar becomes a plain float
    before any arithmetic.

    Raises:
        NonFiniteValueError: If ``value`` is NaN or infinite; the message names it ``name``.
    """
    number = float(value)
    if not math.isfinite(number):
        raise NonFiniteValueError(f"{name} must be finite, got {value!r}.")
    return number


def _mass_flow(name: str, value: float) -> float:
    """Return a finite mass flow in kg/s as a float, refused when it is negative.

    Raises:
        NonFiniteValueError: If ``value`` is NaN or infinite.
        NegativeMassFlowError: If ``value`` is negative; the message names it ``name``.
    """
    number = _finite(name, value)
    if number < 0.0:
        raise NegativeMassFlowError(f"{name} must be >= 0 kg/s, got {number!r}.")
    return number


def _finite_result(name: str, value: float) -> float:
    """Return a computed ``value`` unchanged, refused when the arithmetic overflowed to an infinity or a NaN.

    The arguments of every computation are checked to be finite first, so a non-finite result can only come from
    an overflow, which the message says.

    Raises:
        NonFiniteValueError: If ``value`` is NaN or infinite; the message names the quantity ``name``.
    """
    if not math.isfinite(value):
        raise NonFiniteValueError(
            f"{name} is not finite ({value!r}): the arguments are finite but the result overflows the float range."
        )
    return value


def _finite_sum(name: str, values: Sequence[float]) -> float:
    """Return the exactly rounded sum of ``values`` (:func:`math.fsum`), refused when it is not finite.

    ``math.fsum`` rounds once, at the end, so the sum does not depend on the order of the values or on the Python
    version, whose plain ``sum`` changed its rounding in 3.12.

    Raises:
        NonFiniteValueError: If the sum, or a partial sum on the way, overflows the float range.
    """
    try:
        total = math.fsum(values)
    except (OverflowError, ValueError) as error:
        raise NonFiniteValueError(
            f"{name} is not finite: the arguments are finite but the sum overflows the float range ({error})."
        ) from error
    return _finite_result(name, total)


def _timestep(name: str, value: float) -> float:
    """Return a finite step duration in s as a float, refused when it is not positive.

    Raises:
        NonFiniteValueError: If ``value`` is NaN or infinite.
        NonPositiveTimestepError: If ``value`` is zero or negative; the message names it ``name``.
    """
    number = _finite(name, value)
    if number <= 0.0:
        raise NonPositiveTimestepError(f"{name} must be > 0 s, got {number!r}.")
    return number


def relaxation_mean_factor(a: float) -> float:
    """Return ``phi(a) = (1 - e^(-a)) / a``, the share of a node's start deviation left in its step mean.

    ``a = k dt`` is the node's dimensionless relaxation exponent over the step. ``phi`` is 1 at ``a = 0`` (a node
    that exchanges nothing keeps its start temperature as its mean) and falls towards ``1 / a`` for a large ``a``.
    For example, ``phi(1) = 0.632``. Below ``MixedNode.SMALL_A_THRESHOLD`` the Taylor polynomial
    ``1 - a/2 + a^2/6 - a^3/24`` is used (remainder at most ``a^4/120``), otherwise ``-expm1(-a) / a``; neither
    divides by a vanishing ``a``.

    Args:
        a: The relaxation exponent, dimensionless, 0 or more.

    Returns:
        The factor ``phi``, dimensionless, in (0, 1].

    Raises:
        NonFiniteValueError: If ``a`` is NaN or infinite.
        HydronicsError: If ``a`` is negative.
    """
    a = _finite("a", a)
    if a < 0.0:
        raise HydronicsError(f"The relaxation exponent a = k dt must be >= 0, got {a!r}.")
    if a < MixedNode.SMALL_A_THRESHOLD:
        return 1.0 - a / 2.0 * (1.0 - a / 3.0 * (1.0 - a / 4.0))
    return -math.expm1(-a) / a


def circuit_power_w(*, mass_flow_kg_per_s: float, t_supply_c: float, t_return_c: float) -> float:
    """Return the heat flow a circuit's water carries, ``m c (T_sup - T_ret)``, in W.

    This is the thermal power a component books for a circuit it owns: the heat its water carries, nothing else.
    It is negative for a cooling circuit, whose supply is colder than its return. For example, 0.4 kg/s leaving at
    35.0 °C with a return of 30.04 °C carries ``0.4 * 4180 * 4.96 = 8293.12`` W. :func:`circuit_heat_j` is this
    power times the step, evaluated in the same order, so the power a component books and the heat an energy
    balance derives from the same three values agree to the last bit.

    Args:
        mass_flow_kg_per_s: The circuit's mass flow, in kg/s, at least 0.
        t_supply_c: The supply temperature, the water leaving the supply owner, in °C.
        t_return_c: The return temperature, the water coming back to the supply owner, in °C.

    Returns:
        The heat flow, in W.

    Raises:
        NonFiniteValueError: If any argument is NaN or infinite, or the power overflows the float range.
        NegativeMassFlowError: If the mass flow is negative.
    """
    mass_flow = _mass_flow("mass_flow_kg_per_s", mass_flow_kg_per_s)
    t_supply = _finite("t_supply_c", t_supply_c)
    t_return = _finite("t_return_c", t_return_c)
    lift = _finite_result("The circuit's lift t_supply_c - t_return_c", t_supply - t_return)
    power_w = mass_flow * Water.SPECIFIC_HEAT_J_PER_KG_K * lift
    return _finite_result("The circuit power m c (T_sup - T_ret)", power_w)


def circuit_heat_j(*, mass_flow_kg_per_s: float, t_supply_c: float, t_return_c: float, dt_s: float) -> float:
    """Return the heat a circuit's water carries over one step, ``m c (T_sup - T_ret) dt``, in J.

    It is :func:`circuit_power_w` times the step duration, negative for a cooling circuit. For example, 0.1 kg/s
    over 10 K for one hour carries ``4180 * 0.1 * 10 * 3600 = 15.048`` MJ.

    Args:
        mass_flow_kg_per_s: The circuit's mass flow, in kg/s, at least 0.
        t_supply_c: The supply temperature, in °C.
        t_return_c: The return temperature, in °C.
        dt_s: The step duration, in s.

    Returns:
        The heat, in J.

    Raises:
        NonFiniteValueError: If any argument is NaN or infinite, or the heat overflows the float range.
        NegativeMassFlowError: If the mass flow is negative.
        NonPositiveTimestepError: If ``dt_s`` is not positive.
    """
    dt = _timestep("dt_s", dt_s)
    power_w = circuit_power_w(mass_flow_kg_per_s=mass_flow_kg_per_s, t_supply_c=t_supply_c, t_return_c=t_return_c)
    return _finite_result("The circuit heat m c (T_sup - T_ret) dt", power_w * dt)


def circuit_heat_kwh(*, mass_flow_kg_per_s: float, t_supply_c: float, t_return_c: float, dt_s: float) -> float:
    """Return the heat a circuit's water carries over one step, in kWh, the unit of the energy balance.

    It is :func:`circuit_heat_j` divided by ``UnitConversion.JOULES_PER_KILOWATT_HOUR``. For example, 0.1 kg/s
    over 10 K for one hour carries 4.18 kWh.

    Args:
        mass_flow_kg_per_s: The circuit's mass flow, in kg/s, at least 0.
        t_supply_c: The supply temperature, in °C.
        t_return_c: The return temperature, in °C.
        dt_s: The step duration, in s.

    Returns:
        The heat, in kWh.

    Raises:
        NonFiniteValueError: If any argument is NaN or infinite, or the heat overflows the float range.
        NegativeMassFlowError: If the mass flow is negative.
        NonPositiveTimestepError: If ``dt_s`` is not positive.
    """
    heat_j = circuit_heat_j(
        mass_flow_kg_per_s=mass_flow_kg_per_s, t_supply_c=t_supply_c, t_return_c=t_return_c, dt_s=dt_s
    )
    return heat_j / UnitConversion.JOULES_PER_KILOWATT_HOUR


@dataclass(frozen=True)
class CircuitStep:

    """What the owner of a circuit publishes for one step: its mass flow, its supply and the heat its water carries.

    A value object, so a flow and a temperature cannot be swapped by position. ``power_w`` is
    :func:`circuit_power_w` of the flow, the supply and the return the owner read; an idle circuit has no flow, and
    its supply is its return.
    """

    #: The circuit's mass flow, in kg/s.
    mass_flow_kg_per_s: float
    #: The circuit's supply temperature, in °C.
    t_supply_c: float
    #: The heat flow the circuit's water carries, in W.
    power_w: float

    @staticmethod
    def idle(*, t_return_c: float) -> "CircuitStep":
        """Return the step of a circuit that moves no water: no flow, its supply at its return, no heat.

        Args:
            t_return_c: The circuit's return temperature, in °C.

        Returns:
            The idle step.
        """
        return CircuitStep(mass_flow_kg_per_s=0.0, t_supply_c=t_return_c, power_w=0.0)

    def at_part_load(self, *, part_load_ratio: float, t_return_c: float) -> "CircuitStep":
        """Return this full-load step of a circuit when its generator runs only a fraction of the step.

        A generator that runs a fraction of a step at full load publishes the averaged flow, its full-load flow times
        the ratio, at its full-load supply temperature, and the heat that flow carries from the return,
        ``m c (T_sup - T_ret)`` (:func:`circuit_power_w`). A ratio of 1 returns this step unchanged. For example, a
        step of 0.0144 kg/s at 75 °C over a 50 °C return at a ratio of 0.5 gives 0.0072 kg/s and 752.4 W.

        Args:
            part_load_ratio: The fraction of the step the generator runs, from 0 to 1.
            t_return_c: The circuit's return temperature, in °C.

        Returns:
            The step at that ratio.

        Raises:
            ValueError: If the ratio lies outside [0, 1].
        """
        if not 0.0 <= part_load_ratio <= 1.0:
            raise ValueError(f"A part-load ratio is a fraction from 0 to 1, not {part_load_ratio}.")
        if part_load_ratio == 1.0:
            return self
        mass_flow_kg_per_s = part_load_ratio * self.mass_flow_kg_per_s
        return CircuitStep(
            mass_flow_kg_per_s=mass_flow_kg_per_s,
            t_supply_c=self.t_supply_c,
            power_w=circuit_power_w(mass_flow_kg_per_s=mass_flow_kg_per_s, t_supply_c=self.t_supply_c, t_return_c=t_return_c),
        )


def throttled_supply_temperature_c(*, unthrottled_supply_c: float, return_c: float, supply_limit_c: float) -> float:
    """Return the supply temperature of a generator that holds its lift, throttled at a supply limit, in °C.

    A generator that holds a lift supplies its return plus that lift, the unthrottled supply. When the unthrottled
    supply exceeds the limit, the supply stops at the limit, or at the return if the return is already hotter, so
    a throttled generator never cools its circuit; the flow stays as the generator pumps it, so the circuit then
    carries ``m c (T_sup - T_ret)``. For example, with an 80 °C limit a 72 °C return and a 10 K lift give 80 °C, a
    85 °C return gives 85 °C, and a 60 °C return gives 70 °C.

    Args:
        unthrottled_supply_c: The supply the generator's own law gives, in °C.
        return_c: The circuit's return temperature, in °C.
        supply_limit_c: The highest supply temperature the generator delivers, in °C.

    Returns:
        The supply temperature the circuit carries, in °C.

    Raises:
        NonFiniteValueError: If any argument is NaN or infinite.
    """
    unthrottled = _finite("unthrottled_supply_c", unthrottled_supply_c)
    return_temperature = _finite("return_c", return_c)
    limit = _finite("supply_limit_c", supply_limit_c)
    if unthrottled <= limit:
        return unthrottled
    return max(limit, return_temperature)


def capped_hot_water_supply_temperature_c(
    *, unthrottled_supply_c: float, return_c: float, maximal_supply_c: float, set_supply_c: float
) -> float:
    """Return a generator's hot-water supply temperature, capped at its maximum and its controller's set point, in °C.

    The cap is the lower of the generator's maximum supply temperature and the supply temperature its hot-water
    controller aims at, which must not lie above the maximum, and the supply is throttled at it as
    :func:`throttled_supply_temperature_c` does:
    ``max(min(T_set, T_max), T_ret)`` when the unthrottled supply exceeds ``min(T_set, T_max)``, the unthrottled
    supply otherwise. For example, with a 70 °C set temperature and an 80 °C maximum, a 65 °C return and a 10 K
    lift give 70 °C.

    Why the set temperature caps the supply: a charge then ends at the controller's target inside the step instead
    of a whole step past it, so the fuel or electricity of a charge does not depend on the step length.

    Args:
        unthrottled_supply_c: The supply the generator's own law gives, in °C.
        return_c: The circuit's return temperature, the tank's step mean, in °C.
        maximal_supply_c: The generator's maximum supply temperature, in °C.
        set_supply_c: The supply temperature the hot-water controller aims at, in °C.

    Returns:
        The supply temperature the hot-water circuit carries, in °C.

    Raises:
        NonFiniteValueError: If any argument is NaN or infinite.
        SetTemperatureAboveMaximumError: If ``set_supply_c`` lies above ``maximal_supply_c``, so that a charge
            towards it never ends.
    """
    maximal_supply = _finite("maximal_supply_c", maximal_supply_c)
    set_supply = _finite("set_supply_c", set_supply_c)
    if set_supply > maximal_supply:
        raise SetTemperatureAboveMaximumError(
            f"The hot-water set temperature {set_supply!r} °C lies above the generator's maximum supply temperature "
            f"{maximal_supply!r} °C, so a charge towards it never ends; lower the controller's set temperature or "
            "raise the generator's maximum."
        )
    limit = min(maximal_supply, set_supply)
    return throttled_supply_temperature_c(
        unthrottled_supply_c=unthrottled_supply_c, return_c=return_c, supply_limit_c=limit
    )


@dataclass(frozen=True)
class Inflow:

    """One water stream entering a node at a constant temperature for the step.

    The supply of a charging circuit, the return of a distribution circuit, or the cold refill of a tap. Both
    fields are stored as the ``float`` of what was passed, as every function of this module takes its arguments:
    a ``Decimal`` or a NumPy scalar becomes a plain float, so the step arithmetic never meets another type.
    """

    #: The stream's mass flow, in kg/s, at least 0.
    mass_flow_kg_per_s: float
    #: The stream's temperature, in °C.
    temperature_c: float

    def __post_init__(self) -> None:
        """Refuse a negative or non-finite mass flow and a non-finite temperature, and store both as floats.

        Raises:
            NegativeMassFlowError: If the mass flow is negative.
            NonFiniteValueError: If the mass flow or the temperature is NaN or infinite.
        """
        object.__setattr__(
            self, "mass_flow_kg_per_s", _mass_flow("Inflow.mass_flow_kg_per_s", self.mass_flow_kg_per_s)
        )
        object.__setattr__(self, "temperature_c", _finite("Inflow.temperature_c", self.temperature_c))


@dataclass(frozen=True)
class NodeStep:

    """The result of one step of a fully mixed node.

    ``t_end_c`` is the node's temperature at the end of the step, its start temperature for the next step;
    ``t_mean_c`` is the mean of its temperature over the step, which it publishes as the return of the circuits it
    receives. ``heat_in_j_per_inflow[i]`` is ``m_i c (T_i - T_mean) dt``, in the order of the inflows, and
    ``loss_j`` is ``UA (T_mean - T_amb) dt``; ``sum(heat_in_j_per_inflow) - loss_j`` equals ``C (t_end_c - T0)``.
    """

    #: The node's temperature at the end of the step, in °C.
    t_end_c: float
    #: The mean of the node's temperature over the step, in °C.
    t_mean_c: float
    #: The heat each inflow brought over the step, in J, in the order of the inflows.
    heat_in_j_per_inflow: Tuple[float, ...]
    #: The heat the node lost to its surroundings over the step, in J.
    loss_j: float


@dataclass(frozen=True)
class TemperatureRange:

    """The range of the temperatures a node mixes over one step, in °C.

    A fully mixed node relaxes towards a weighted mean of its inflow and ambient temperatures, so its step mean and
    its end temperature never leave the range of those and its start temperature. The range brackets a node's
    fixed-point solves and bounds what a node publishes. For example, a tank at 50 °C charged at 70 °C, refilled at
    10 °C and losing heat to a 20 °C room mixes temperatures from 10 to 70 °C.
    """

    #: The lowest temperature the node mixes, in °C.
    low_c: float
    #: The highest temperature the node mixes, in °C.
    high_c: float

    @staticmethod
    def of_node(
        *, t0_c: float, inflows: Iterable[Inflow], fixed_temperatures_c: Iterable[float]
    ) -> "TemperatureRange":
        """Return the range of a node's start temperature, its flowing inflows and fixed temperatures, in °C.

        An inflow without flow brings no water, so its temperature is left out. The fixed temperatures are those
        the node mixes whatever the flows are, such as its ambient temperature and a tap's mains refill.

        Args:
            t0_c: The node's start temperature, in °C.
            inflows: The streams entering the node.
            fixed_temperatures_c: Further temperatures the node mixes, in °C.

        Returns:
            The range from the lowest to the highest of these temperatures.
        """
        temperatures_c = [t0_c, *fixed_temperatures_c] + [
            inflow.temperature_c for inflow in inflows if inflow.mass_flow_kg_per_s > 0.0
        ]
        return TemperatureRange(low_c=min(temperatures_c), high_c=max(temperatures_c))

    def clamped_c(self, temperature_c: float) -> float:
        """Return ``temperature_c`` moved into the range, in °C.

        For example, 75 °C in the range 10 to 70 °C becomes 70 °C; 50 °C stays.

        Args:
            temperature_c: The temperature to bound, in °C.

        Returns:
            The temperature, at least ``low_c`` and at most ``high_c``.
        """
        return min(max(temperature_c, self.low_c), self.high_c)


class MixedNode:

    """A fully mixed water node, integrated over one step in closed form without sub-stepping.

    A namespace for the node step and its numerical threshold. The step solves
    ``C dT/dt = sum_i m_i c (T_i - T) - UA (T - T_amb)`` exactly for constant inflow temperatures, so its result does
    not depend on the step length and its heat balance closes to float precision.
    """

    #: Below this relaxation exponent ``a = k dt`` the relaxation mean factor is the Taylor polynomial, at or above
    #: it ``-expm1(-a)/a``; at it, the polynomial's remainder ``a^4/120`` is under a fortieth of an ulp of 1.
    SMALL_A_THRESHOLD: ClassVar[float] = 1e-4

    @staticmethod
    def step(
        *,
        t0_c: float,
        inflows: Iterable[Inflow],
        ua_w_per_k: float,
        t_amb_c: float,
        dt_s: float,
        heat_capacity_j_per_k: float,
    ) -> NodeStep:
        """Return the end temperature, step mean, inflow heats and loss of a fully mixed node over one step.

        Every inflow keeps its temperature over the step, and as much water leaves the node as enters it, at the
        node's temperature. For example, 248 kg of water at 50 °C charged with 0.2 kg/s at 70 °C for 900 s, with
        no loss, ends at 60.3 °C with a step mean of 55.8 °C.

        Args:
            t0_c: The node's temperature at the start of the step, in °C.
            inflows: Every stream entering the node; read once, so any iterable will do.
            ua_w_per_k: The loss coefficient to ``t_amb_c``, in W/K.
            t_amb_c: The temperature the node loses heat to, in °C.
            dt_s: The step duration, in s.
            heat_capacity_j_per_k: The node's heat capacity ``C = M c``, in J/K.

        Returns:
            The end temperature, the step mean, the heat of each inflow and the loss.

        Raises:
            NonFiniteValueError: If any value is NaN or infinite, or a result overflows the float range.
            NegativeHeatLossCoefficientError: If ``ua_w_per_k`` is negative.
            NonPositiveTimestepError: If ``dt_s`` is not positive.
            NonPositiveHeatCapacityError: If ``heat_capacity_j_per_k`` is not positive.
            HydronicsError: If an inflow is not an :class:`Inflow`.
        """
        t0 = _finite("t0_c", t0_c)
        ua = _finite("ua_w_per_k", ua_w_per_k)
        if ua < 0.0:
            raise NegativeHeatLossCoefficientError(f"ua_w_per_k must be >= 0 W/K, got {ua!r}.")
        t_amb = _finite("t_amb_c", t_amb_c)
        dt = _timestep("dt_s", dt_s)
        capacity = _finite("heat_capacity_j_per_k", heat_capacity_j_per_k)
        if capacity <= 0.0:
            raise NonPositiveHeatCapacityError(f"heat_capacity_j_per_k must be > 0 J/K, got {capacity!r}.")
        inflows = tuple(inflows)
        for inflow in inflows:
            if not isinstance(inflow, Inflow):
                raise HydronicsError(f"A node's inflows must be Inflow instances, got {inflow!r}.")

        conductances = [
            _finite_result(
                f"The conductance m c of inflow {i}", inflow.mass_flow_kg_per_s * Water.SPECIFIC_HEAT_J_PER_KG_K
            )
            for i, inflow in enumerate(inflows)
        ]
        total_conductance = _finite_sum("The node's total conductance sum_i m_i c + UA", conductances + [ua])
        if total_conductance > 0.0:
            weighted = [g * inflow.temperature_c for g, inflow in zip(conductances, inflows)] + [ua * t_amb]
            numerator = _finite_sum("The node's equilibrium numerator sum_i m_i c T_i + UA T_amb", weighted)
            t_inf = _finite_result("The node's equilibrium temperature T_inf", numerator / total_conductance)
            a = _finite_result("The node's relaxation exponent a = k dt", total_conductance * dt / capacity)
        else:
            t_inf = t0
            a = 0.0
        phi = relaxation_mean_factor(a)
        t_mean = _finite_result("The node's step mean T_mean", t_inf + (t0 - t_inf) * phi)
        t_end = _finite_result("The node's end temperature T_end", t0 + (t_inf - t0) * (a * phi))
        heat_in = tuple(
            _finite_result(f"The heat of inflow {i}", g * (inflow.temperature_c - t_mean) * dt)
            for i, (g, inflow) in enumerate(zip(conductances, inflows))
        )
        loss = _finite_result("The node's loss UA (T_mean - T_amb) dt", ua * (t_mean - t_amb) * dt)
        return NodeStep(t_end_c=t_end, t_mean_c=t_mean, heat_in_j_per_inflow=heat_in, loss_j=loss)


@dataclass(frozen=True)
class ValveDraw:

    """What a thermostatic tap valve lets out of a tank for one hot-water demand.

    ``hot_water_kg_per_s`` is the tank water the valve lets out, which the same mass of mains water refills;
    ``unmet_fraction`` is the share of the demand's heat the valve cannot deliver because the tank is too cold. A
    value object, so the flow and the fraction cannot be swapped by position.
    """

    #: The tank water the valve lets out, in kg/s, between 0 and the demand.
    hot_water_kg_per_s: float
    #: The share of the demand's heat left unmet, dimensionless, between 0 and 1.
    unmet_fraction: float


def mixing_valve_draw(*, t_tank_c: float, t_warm_c: float, t_cold_c: float, demand_kg_per_s: float) -> ValveDraw:
    """Return the hot water a thermostatic tap valve draws from a tank, and the share of the demand left unmet.

    The household asks for ``demand_kg_per_s`` of warm water at ``t_warm_c``; the valve blends tank water with
    mains water at ``t_cold_c``, which also refills the tank with the mass drawn.

    * ``T > T_warm``: ``m_hot = m_d (T_warm - T_cold) / (T - T_cold)``, nothing unmet; the heat drawn
      ``m_hot c (T - T_cold)`` equals the demand's ``m_d c (T_warm - T_cold)``.
    * ``T_cold < T <= T_warm``: all of ``m_d`` passes unmixed; the unmet fraction is
      ``(T_warm - T) / (T_warm - T_cold)``, so the two branches meet at ``T = T_warm``.
    * ``T <= T_cold``: nothing is drawn and the unmet fraction is 1.

    For example, 0.05 kg/s at 40 °C from a 55 °C tank refilled at 10 °C lets out 0.0333 kg/s of tank water. The
    unmet fraction is a property of the temperatures; with no demand it multiplies a zero heat.

    Args:
        t_tank_c: The tank temperature the valve sees, in °C.
        t_warm_c: The temperature the household asks for, in °C.
        t_cold_c: The mains water temperature, in °C, below ``t_warm_c``.
        demand_kg_per_s: The warm water the household asks for, in kg/s, at least 0.

    Returns:
        The hot water let out and the unmet fraction.

    Raises:
        NonFiniteValueError: If any argument is NaN or infinite, or a result overflows the float range.
        ValveTemperatureOrderError: If ``t_warm_c <= t_cold_c``.
        NegativeDemandError: If ``demand_kg_per_s`` is negative.
    """
    t_tank = _finite("t_tank_c", t_tank_c)
    t_warm = _finite("t_warm_c", t_warm_c)
    t_cold = _finite("t_cold_c", t_cold_c)
    demand = _finite("demand_kg_per_s", demand_kg_per_s)
    if t_warm <= t_cold:
        raise ValveTemperatureOrderError(
            f"A mixing valve's warm temperature must be above its cold one, got t_warm_c={t_warm!r}, "
            f"t_cold_c={t_cold!r}."
        )
    if demand < 0.0:
        raise NegativeDemandError(f"demand_kg_per_s must be >= 0, got {demand!r}.")
    span = _finite_result("The valve's span t_warm_c - t_cold_c", t_warm - t_cold)
    if t_tank > t_warm:
        lift = _finite_result("The tank's lift over the mains t_tank_c - t_cold_c", t_tank - t_cold)
        hot_water = _finite_result("The valve's hot-water draw m_hot", demand * span / lift)
        return ValveDraw(hot_water_kg_per_s=hot_water, unmet_fraction=0.0)
    if t_tank > t_cold:
        return ValveDraw(hot_water_kg_per_s=demand, unmet_fraction=(t_warm - t_tank) / span)
    return ValveDraw(hot_water_kg_per_s=0.0, unmet_fraction=1.0)


def hot_water_demand_power_w(*, demand_kg_per_s: float, t_warm_c: float, t_cold_c: float) -> float:
    """Return the heat flow a hot-water demand asks for, ``m_d c (T_warm - T_cold)``, in W.

    It is the heat that warms the demanded water from the mains temperature to the tap temperature, the heat
    :func:`mixing_valve_draw` delivers in full while the tank is above the tap temperature. For example, 0.05 kg/s
    at 40 °C from 10 °C mains water ask for ``0.05 * 4180 * 30 = 6270`` W.

    Args:
        demand_kg_per_s: The warm water the household asks for, in kg/s.
        t_warm_c: The temperature the household asks for, in °C.
        t_cold_c: The mains water temperature, in °C.

    Returns:
        The heat flow, in W.
    """
    return demand_kg_per_s * Water.SPECIFIC_HEAT_J_PER_KG_K * (t_warm_c - t_cold_c)


SolveResultT = TypeVar("SolveResultT")


class IllinoisSide:

    """Which end of the bracket the Illinois method moved last: a namespace of the two sides and of none.

    The Illinois variant of regula falsi halves the residual kept at one end of the bracket when the other end
    moved twice in a row, which keeps a convex residual from stalling the method at one end.
    """

    #: No end has moved yet.
    NONE: ClassVar[int] = 0
    #: The low end moved last.
    LOW: ClassVar[int] = -1
    #: The high end moved last.
    HIGH: ClassVar[int] = 1


def solve_bracketed_fixed_point(
    *,
    evaluate: Callable[[float], SolveResultT],
    image_c: Callable[[SolveResultT], float],
    low_c: float,
    high_c: float,
    tolerance_k: float,
    maximum_evaluations: int,
) -> SolveResultT:
    """Return the evaluation at the fixed point ``x = g(x)`` of a temperature map inside a bracket.

    ``evaluate(x)`` computes a result for an assumed temperature ``x``, and ``image_c(result)`` is ``g(x)``, the
    temperature that result implies. The bracket must hold the fixed point: ``g`` maps it into itself, so
    ``g(low) - low >= 0 >= g(high) - high``. The method is the Illinois variant of regula falsi on the residual
    ``g(x) - x``; it needs no start value from an earlier iteration and returns the evaluation whose residual is at
    most ``tolerance_k``, or whose bracket has shrunk below it. For example, a tank whose step mean depends on the
    hot water its tap valve lets out, which depends on that step mean, solves its step mean this way within a few
    evaluations.

    Args:
        evaluate: The computation at an assumed temperature in °C.
        image_c: The temperature an evaluation implies, in °C.
        low_c: The low end of the bracket, in °C.
        high_c: The high end of the bracket, in °C, at least ``low_c``.
        tolerance_k: The largest residual accepted, in K.
        maximum_evaluations: The most evaluations inside the bracket before the solve fails.

    Returns:
        The evaluation at the fixed point.

    Raises:
        FixedPointNotFoundError: If the solve has not converged within ``maximum_evaluations`` evaluations.
    """
    low_result, high_result = evaluate(low_c), evaluate(high_c)
    low_residual_k = image_c(low_result) - low_c
    high_residual_k = image_c(high_result) - high_c
    if low_residual_k <= tolerance_k:
        return low_result
    if high_residual_k >= -tolerance_k:
        return high_result
    moved_last = IllinoisSide.NONE
    for _ in range(maximum_evaluations):
        assumed_c = (low_c * high_residual_k - high_c * low_residual_k) / (high_residual_k - low_residual_k)
        candidate = evaluate(assumed_c)
        residual_k = image_c(candidate) - assumed_c
        if abs(residual_k) <= tolerance_k or high_c - low_c <= tolerance_k:
            return candidate
        if residual_k > 0.0:
            low_c, low_residual_k = assumed_c, residual_k
            if moved_last == IllinoisSide.LOW:
                high_residual_k /= 2.0
            moved_last = IllinoisSide.LOW
        else:
            high_c, high_residual_k = assumed_c, residual_k
            if moved_last == IllinoisSide.HIGH:
                low_residual_k /= 2.0
            moved_last = IllinoisSide.HIGH
    raise FixedPointNotFoundError(
        f"The fixed point did not converge within {maximum_evaluations} evaluations (bracket {low_c} to {high_c} °C)."
    )
