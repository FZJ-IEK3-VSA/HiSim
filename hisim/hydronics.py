"""Hydronics: the water arithmetic every hydronic circuit and every fully mixed node shares (hisim-fxix.2).

This module is stage A of the hydronic coupling spec (``roadmap/hydronic_coupling_spec.md``, PR #878, §9.5 A):
a pure numerical library. Its one user so far is the hplib heat pump
(``hisim/components/more_advanced_heat_pump_hplib.py``), which books the thermal power of its circuits with
:func:`circuit_power_w`. Later stages (C and D) replace the storages' own mixing, booking and loss code in
``hisim/components/simple_water_storage.py`` with the functions here: the mass mixing of
``calculate_mean_water_temperature_in_water_storage`` becomes :meth:`MixedNode.step`, the inflow heat booked
against the start temperature becomes ``NodeStep.heat_in_j_per_inflow`` (booked against the step mean), the
outlet at the start temperature becomes ``NodeStep.t_mean_c``, and the loss subtracted after the step from
``calculate_heat_loss_and_temperature_loss`` becomes ``NodeStep.loss_j``, taken inside the same integration.
The stratification path's ``dt/3600`` mixing factor stays in the storage (spec §4.5, D5).

It lives in ``hisim/`` rather than ``hisim/components/`` although the spec names ``hisim/components/hydronics.py``
(§4.1): :class:`hisim.energy_port.HydronicPort` delegates its energy to :func:`kilowatt_hours`, and
``hisim/energy_port.py`` is imported by ``hisim/component.py``, the base of every component. The core layer must
not import from the component package it is the base of, so the library sits beside ``energy_port.py`` and
``units.py``; it imports nothing from HiSim.

Every function refuses what it cannot compute with a :class:`HydronicsError` subclass, a ``ValueError``: a
negative mass flow, a negative loss coefficient, a heat capacity or a timestep that is not positive, a value that
is not finite (NaN or infinite), a valve whose warm temperature is not above its cold one. A result that overflows
to an infinity or a NaN from finite arguments (a heat ``m c dT dt`` beyond the float range, say) is refused with
:class:`NonFiniteValueError` naming the quantity, never returned. Nothing is clipped.

Circuits (§3)
-------------

A circuit is one closed loop between two components: a mass flow ``m >= 0`` (kg/s), a supply temperature and a
return temperature (°C). Its heat over a step of ``dt`` seconds is derived, never sent:

``Q = m c (T_sup - T_ret) dt``  (:func:`circuit_heat_j`, §3.5)

and its heat flow is ``m c (T_sup - T_ret)`` (:func:`circuit_power_w`), positive when the supply owner heats the
receiving side, negative for a cooling circuit with ``T_sup < T_ret`` (§3.4); nothing is mirrored.
:func:`kilowatt_hours` is the same number in kWh, the unit of the energy balance.

The mixed node (§4.1, §4.2)
---------------------------

A fully mixed node of heat capacity ``C`` (J/K) with inflows ``m_i`` at constant temperatures ``T_i`` and a loss
``UA`` (W/K) to ``T_amb`` obeys ``C dT/dt = sum_i m_i c (T_i - T) - UA (T - T_amb)``. With
``G = sum_i m_i c + UA``, ``T_inf = (sum_i m_i c T_i + UA T_amb) / G``, ``k = G / C`` and ``a = k dt``:

* ``T_end = T_inf + (T0 - T_inf) e^(-a)``
* ``T_mean = T_inf + (T0 - T_inf) (1 - e^(-a)) / a``

:meth:`MixedNode.step` evaluates both through ``phi(a) = (1 - e^(-a)) / a`` (:func:`relaxation_mean_factor`)
as ``T_mean = T_inf + (T0 - T_inf) phi`` and ``T_end = T0 + (T_inf - T0) a phi``; the second form keeps the
change ``T_end - T0`` free of cancellation when ``a`` is small. ``phi`` is ``-expm1(-a) / a`` for
``a >= SMALL_A_THRESHOLD`` (1e-4) and the Taylor polynomial ``1 - a/2 + a^2/6 - a^3/24`` below it, so ``a = 0``
(no flow and no loss: ``G = 0``) gives ``phi = 1`` and ``T_mean = T_end = T0`` without a division. The Taylor
remainder below the threshold is at most ``a^4/120 < 8.4e-19``, under a fortieth of an ulp of ``phi ~ 1``;
``tests/test_hydronics.py`` checks ``phi`` against a 50-digit :mod:`decimal` reference on both sides of the
threshold.

The heat each inflow brings is ``m_i c (T_i - T_mean) dt`` and the loss ``UA (T_mean - T_amb) dt``; their
difference is ``C (T_end - T0)`` identically (exact closure, §4.2), and ``T_mean`` and ``T_end`` never leave the
range of ``T0``, the inflow temperatures and ``T_amb`` (no overshoot).

The tap valve (§4.3)
--------------------

:func:`mixing_valve_draw` is the thermostatic valve's arithmetic for a demand ``m_d`` at ``T_warm`` from a tank
at ``T`` refilled at ``T_cold``. The local scalar iteration that solves it on the tank's own step mean belongs to
the tank (stage C); this function is the map it iterates.

Node acceleration (§6)
----------------------

:func:`accelerated_node_mean` is the node-side acceleration of §6: after more than
``ACCELERATION_AFTER_ITERATIONS`` (6) iterations on one step, a node publishes a secant (Aitken) extrapolation of
its own fixed point instead of its raw step mean, and an under-relaxed value when its residual changes sign. It
only changes how fast the fixed point is reached, never which one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar, Iterable, Sequence, Tuple

#: Specific heat capacity of water, J/(kg K): the value every storage and the heat distribution system use today,
#: ``PhysicsConfig.get_properties_for_energy_carrier(LoadTypes.WATER)`` in ``hisim/components/configuration.py``
#: (spec §2.2), which reads this constant: the library is its one source. ``tests/test_hydronics.py`` pins the
#: two to each other, which proves that import.
WATER_SPECIFIC_HEAT_J_PER_KG_K: float = 4180.0

#: Density of water, kg/m³: 0.992 kg/l at 40 °C, the value ``SimpleHotWaterStorage`` and ``SimpleDHWStorage``
#: turn their volume into a mass with (``density_water_at_40_degree_celsius_in_kg_per_liter`` in
#: ``hisim/components/simple_water_storage.py``, source
#: https://www.internetchemie.info/chemie-lexikon/daten/w/wasser-dichtetabelle.php). Nothing reads this constant
#: yet. ``PhysicsConfig``'s water entry states 1000 kg/m³, which no storage uses; hydronic stage C aligns it with
#: this value when the storages move onto this library.
WATER_DENSITY_KG_PER_M3: float = 992.0

#: Joules in one kilowatt hour.
JOULES_PER_KILOWATT_HOUR: float = 3.6e6

#: Below this ``a = k dt`` the relaxation mean factor is the Taylor polynomial, at or above it ``-expm1(-a)/a``.
SMALL_A_THRESHOLD: float = 1e-4

#: A node accelerates its published mean only after this many iterations on one step (§6: "more than six").
ACCELERATION_AFTER_ITERATIONS: int = 6

#: The weight of the new value when a node under-relaxes an oscillating iteration (§6).
UNDER_RELAXATION_WEIGHT: float = 0.5


class HydronicsError(ValueError):

    """A value the hydronic arithmetic cannot compute with."""


class NonFiniteValueError(HydronicsError):

    """A temperature, flow, coefficient or duration is NaN or infinite."""


class NegativeMassFlowError(HydronicsError):

    """A mass flow is negative; a circuit's direction is its topology, never the flow's sign (§3.4)."""


class NegativeHeatLossCoefficientError(HydronicsError):

    """A node's loss coefficient ``UA`` is negative."""


class NonPositiveHeatCapacityError(HydronicsError):

    """A node's heat capacity is zero or negative: a node without water cannot be integrated."""


class NonPositiveTimestepError(HydronicsError):

    """A step duration is zero or negative."""


class ValveTemperatureOrderError(HydronicsError):

    """A mixing valve's warm temperature is not above its cold one."""


class NegativeDemandError(HydronicsError):

    """A hot-water demand is negative."""


class AccelerationHistoryError(HydronicsError):

    """A node's iteration history is empty or its published and computed values do not pair up."""


def _finite(name: str, value: float) -> float:
    """``value`` as a float, refused when it is NaN or infinite."""
    number = float(value)
    if not math.isfinite(number):
        raise NonFiniteValueError(f"{name} must be finite, got {value!r}.")
    return number


def _mass_flow(name: str, value: float) -> float:
    """A finite mass flow, refused when it is negative."""
    number = _finite(name, value)
    if number < 0.0:
        raise NegativeMassFlowError(f"{name} must be >= 0 kg/s, got {number!r}.")
    return number


def _finite_result(name: str, value: float) -> float:
    """A computed ``value``, refused when the arithmetic overflowed to an infinity or a NaN."""
    if not math.isfinite(value):
        raise NonFiniteValueError(
            f"{name} is not finite ({value!r}): the arguments are finite but the result overflows the float range."
        )
    return value


def _finite_sum(name: str, values: Sequence[float]) -> float:
    """The exactly rounded ``math.fsum`` of ``values``, refused when it is not finite or overflows on the way."""
    try:
        total = math.fsum(values)
    except (OverflowError, ValueError) as error:
        raise NonFiniteValueError(
            f"{name} is not finite: the arguments are finite but the sum overflows the float range ({error})."
        ) from error
    return _finite_result(name, total)


def _timestep(name: str, value: float) -> float:
    """A finite step duration, refused when it is not positive."""
    number = _finite(name, value)
    if number <= 0.0:
        raise NonPositiveTimestepError(f"{name} must be > 0 s, got {number!r}.")
    return number


def relaxation_mean_factor(a: float) -> float:
    """``phi(a) = (1 - e^(-a)) / a``, the step mean's share of the start deviation, with ``phi(0) = 1``.

    For ``a < SMALL_A_THRESHOLD`` the Taylor polynomial ``1 - a/2 + a^2/6 - a^3/24`` (remainder at most
    ``a^4/120``), otherwise ``-expm1(-a) / a``; neither divides by a vanishing ``a``.

    Raises:
        NonFiniteValueError: If ``a`` is NaN or infinite.
        HydronicsError: If ``a`` is negative.
    """
    a = _finite("a", a)
    if a < 0.0:
        raise HydronicsError(f"The relaxation exponent a = k dt must be >= 0, got {a!r}.")
    if a < SMALL_A_THRESHOLD:
        return 1.0 - a / 2.0 * (1.0 - a / 3.0 * (1.0 - a / 4.0))
    return -math.expm1(-a) / a


def circuit_power_w(mass_flow_kg_per_s: float, t_supply_c: float, t_return_c: float) -> float:
    """The heat flow of a circuit, ``m c (T_sup - T_ret)`` in W (§3.5); negative when cooling (§3.4).

    This is the thermal power a component books for a circuit it owns: the heat its water carries, nothing
    else. For example, 0.4 kg/s leaving at 35.0 °C with a return of 30.04 °C carries
    ``0.4 * 4180 * 4.96 = 8293.12`` W. :func:`circuit_heat_j` is this power times the step, evaluated in the same
    order, so the power a component books and the heat the energy balance derives from the same three values
    agree to the last bit.

    Args:
        mass_flow_kg_per_s: The circuit's mass flow, kg/s, at least 0.
        t_supply_c: The supply temperature, °C: the water leaving the supply owner.
        t_return_c: The return temperature, °C: the water coming back to it.

    Returns:
        The heat flow in W.

    Raises:
        NonFiniteValueError: If any argument is NaN or infinite, or the power overflows the float range.
        NegativeMassFlowError: If the mass flow is negative.
    """
    mass_flow = _mass_flow("mass_flow_kg_per_s", mass_flow_kg_per_s)
    t_supply = _finite("t_supply_c", t_supply_c)
    t_return = _finite("t_return_c", t_return_c)
    lift = _finite_result("The circuit's lift t_supply_c - t_return_c", t_supply - t_return)
    power = mass_flow * WATER_SPECIFIC_HEAT_J_PER_KG_K * lift
    return _finite_result("The circuit power m c (T_sup - T_ret)", power)


def circuit_heat_j(mass_flow_kg_per_s: float, t_supply_c: float, t_return_c: float, dt_s: float) -> float:
    """The heat of a circuit over one step, ``m c (T_sup - T_ret) dt`` in J (§3.5); negative when cooling (§3.4).

    It is :func:`circuit_power_w` times ``dt_s``.

    Raises:
        NonFiniteValueError: If any argument is NaN or infinite, or the heat overflows the float range.
        NegativeMassFlowError: If the mass flow is negative.
        NonPositiveTimestepError: If ``dt_s`` is not positive.
    """
    dt = _timestep("dt_s", dt_s)
    heat = circuit_power_w(mass_flow_kg_per_s, t_supply_c, t_return_c) * dt
    return _finite_result("The circuit heat m c (T_sup - T_ret) dt", heat)


def kilowatt_hours(
    mass_flow_kg_per_s: float, t_supply_c: float, t_return_c: float, seconds_per_timestep: float
) -> float:
    """The heat of a circuit over one step in kWh: :func:`circuit_heat_j` divided by 3.6e6 (§3.5).

    Raises:
        NonFiniteValueError: If any argument is NaN or infinite, or the heat overflows the float range.
        NegativeMassFlowError: If the mass flow is negative.
        NonPositiveTimestepError: If ``seconds_per_timestep`` is not positive.
    """
    return circuit_heat_j(mass_flow_kg_per_s, t_supply_c, t_return_c, seconds_per_timestep) / JOULES_PER_KILOWATT_HOUR


@dataclass(frozen=True)
class Inflow:

    """One water stream entering a node at a constant temperature for the step.

    The supply of a charging circuit, the return of a distribution circuit, or the cold refill of a tap. Both
    fields are stored as the ``float`` of what was passed, as every function of this module takes its arguments:
    a ``Decimal`` or a NumPy scalar becomes a plain float, so the step arithmetic never meets another type.
    """

    mass_flow_kg_per_s: float
    temperature_c: float

    def __post_init__(self) -> None:
        """Refuse a negative or non-finite mass flow and a non-finite temperature; store both as floats."""
        object.__setattr__(
            self, "mass_flow_kg_per_s", _mass_flow("Inflow.mass_flow_kg_per_s", self.mass_flow_kg_per_s)
        )
        object.__setattr__(self, "temperature_c", _finite("Inflow.temperature_c", self.temperature_c))


@dataclass(frozen=True)
class NodeStep:

    """The result of one node step (§4.1).

    ``t_end_c`` is the node's state for the next step, ``t_mean_c`` the step mean it publishes to every circuit
    (§3.2). ``heat_in_j_per_inflow[i]`` is ``m_i c (T_i - T_mean) dt``, in the order of the inflows, and
    ``loss_j`` is ``UA (T_mean - T_amb) dt``; ``sum(heat_in_j_per_inflow) - loss_j`` equals
    ``C (t_end_c - T0)``.
    """

    t_end_c: float
    t_mean_c: float
    heat_in_j_per_inflow: Tuple[float, ...]
    loss_j: float


class MixedNode:

    """The fully mixed node of §4.1: one closed form per step, no sub-stepping."""

    #: The equation the step solves exactly, for error messages and the docs.
    EQUATION: ClassVar[str] = "C dT/dt = sum_i m_i c (T_i - T) - UA (T - T_amb)"

    @staticmethod
    def step(
        t0_c: float,
        inflows: Iterable[Inflow],
        ua_w_per_k: float,
        t_amb_c: float,
        dt_s: float,
        heat_capacity_j_per_k: float,
    ) -> NodeStep:
        """Integrate the node over one step with constant inflow temperatures (§4.1).

        Args:
            t0_c: The node's temperature at the start of the step, °C.
            inflows: Every stream entering the node; as much water leaves as enters. Read once, so any iterable
                will do.
            ua_w_per_k: The loss coefficient to ``t_amb_c``, W/K.
            t_amb_c: The temperature the node loses heat to, °C.
            dt_s: The step duration, s.
            heat_capacity_j_per_k: The node's heat capacity ``C = M c``, J/K.

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
                f"The conductance m c of inflow {i}", inflow.mass_flow_kg_per_s * WATER_SPECIFIC_HEAT_J_PER_KG_K
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


def mixing_valve_draw(
    t_tank_c: float, t_warm_c: float, t_cold_c: float, demand_kg_per_s: float
) -> Tuple[float, float]:
    """The hot-water mass a thermostatic tap valve draws from a tank, and the share of the demand left unmet (§4.3).

    The household asks for ``demand_kg_per_s`` at ``t_warm_c``; the valve blends tank water with mains water at
    ``t_cold_c``, which also refills the tank with the mass drawn.

    * ``T > T_warm``: ``m_hot = m_d (T_warm - T_cold) / (T - T_cold)``, nothing unmet; the heat drawn
      ``m_hot c (T - T_cold)`` equals the demand's ``m_d c (T_warm - T_cold)``.
    * ``T_cold < T <= T_warm``: all of ``m_d`` passes unmixed; the unmet fraction is
      ``(T_warm - T) / (T_warm - T_cold)``, so the two branches meet at ``T = T_warm``.
    * ``T <= T_cold``: nothing is drawn and the unmet fraction is 1.

    The unmet fraction is a property of the temperatures; with no demand it multiplies a zero heat.

    Returns:
        ``(m_hot_kg_per_s, unmet_fraction)`` with ``0 <= m_hot <= m_d`` and ``0 <= unmet_fraction <= 1``.

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
        return _finite_result("The valve's hot-water draw m_hot", demand * span / lift), 0.0
    if t_tank > t_cold:
        return demand, (t_warm - t_tank) / span
    return 0.0, 1.0


def accelerated_node_mean(published_c: Sequence[float], computed_c: Sequence[float]) -> float:
    """The step mean a node publishes next, accelerated after six iterations on the step (§6).

    The node's fixed-point map ``F`` takes the step mean it published (which the other end of its circuits
    reacted to) to the step mean it computes from what came back. ``published_c[j]`` is the mean the node had
    published when it computed ``computed_c[j] = F(published_c[j])``, oldest first, for every iteration on the
    current step. The node owns this history: it appends one pair per iteration, resets it at the start of a step,
    and is the one that put every value in it. This function reads only the length and the last two pairs, so it
    validates only those; it neither copies nor scans the rest. The residual is
    ``r_j = computed_c[j] - published_c[j]``.

    * Up to ``ACCELERATION_AFTER_ITERATIONS`` iterates: the plain iteration, ``computed_c[-1]``.
    * When the last two residuals have opposite signs (an oscillation, which a contraction with ``theta`` in
      (0, 1) does not produce but the heat pump's rounding staircase of §5.2 can): the under-relaxed
      ``x + w r`` with ``w = UNDER_RELAXATION_WEIGHT``.
    * Otherwise the secant step on the residual, ``x - r (x - x_prev) / (r - r_prev)``, which is Aitken's
      delta-squared extrapolation when the node has published its plain iterates: with ``x = F(x_prev)`` the two
      are algebraically identical, ``x - r r_prev / (r - r_prev) = x_prev - (x - x_prev)^2 / (F(x) - 2x + x_prev)``.
      It is taken only when the
      secant's estimate of the contraction factor ``theta = 1 + (r - r_prev) / (x - x_prev)`` lies in [0, 1),
      the range §6 derives for a node fed by circuits that hold their lift; outside it, and when two
      iterates coincide, the plain iteration is returned.

    A zero residual returns the published value in every branch, so the fixed point is never moved. The secant
    step is not bounded: the node that uses it (stage C) clamps what it publishes to its physical range.

    Raises:
        AccelerationHistoryError: If the history is empty or the two sequences differ in length.
        NonFiniteValueError: If one of the last two iterates is NaN or infinite, or the extrapolation overflows.
    """
    if len(published_c) != len(computed_c):
        raise AccelerationHistoryError(
            f"Every computed mean pairs with the mean published before it: got {len(published_c)} published "
            f"and {len(computed_c)} computed."
        )
    if not published_c:
        raise AccelerationHistoryError("A node's iteration history is empty.")
    count = len(published_c)
    last = count - 1
    computed_last = _finite(f"computed_c[{last}]", computed_c[last])
    x = _finite(f"published_c[{last}]", published_c[last])
    if count <= ACCELERATION_AFTER_ITERATIONS:
        return computed_last
    x_prev = _finite(f"published_c[{last - 1}]", published_c[last - 1])
    computed_prev = _finite(f"computed_c[{last - 1}]", computed_c[last - 1])
    r_prev = _finite_result("The previous residual", computed_prev - x_prev)
    r = _finite_result("The residual", computed_last - x)
    if r * r_prev < 0.0:
        return _finite_result("The under-relaxed mean", x + UNDER_RELAXATION_WEIGHT * r)
    if x == x_prev or r == r_prev:
        return computed_last
    slope = _finite_result("The secant slope", (r - r_prev) / (x - x_prev))
    theta = 1.0 + slope
    if not 0.0 <= theta < 1.0:
        return computed_last
    return _finite_result("The secant extrapolation", x - r / slope)
