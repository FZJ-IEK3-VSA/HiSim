"""Hydronics: the water arithmetic that every hydronic circuit and every fully mixed node shares.

A **circuit** is one closed water loop between two components: a mass flow ``m >= 0`` (kg/s) that one of them
pumps, a supply temperature (the water leaving the supply owner, °C) and a return temperature (the water coming
back to it, °C). A **node** is a component that holds water and integrates its temperature over a step, such as a
hot-water tank; it is fully mixed, so its water has one uniform temperature at every moment. Components that
exchange heat through water publish only these port quantities, and the heat is derived from them by the
functions here, so both ends of a circuit book the same number.

The module lives in ``hisim/`` rather than ``hisim/components/``: :class:`hisim.energy_port.HydronicPort` derives
a circuit's energy from :func:`circuit_power_w`, and ``hisim/energy_port.py`` is imported by
``hisim/component.py``, the base of every component, which must not import the component package built on it.
The module imports nothing from HiSim.

The functions refuse, with a :class:`HydronicsError` subclass (a ``ValueError``), the values that would give a
wrong but finite result: a negative mass flow, a negative relaxation exponent, a hot-water set temperature above the
generator's maximum supply. A node's constants (its loss coefficient, its heat capacity and the step length) are
checked once, when the component that owns the node is built (:meth:`MixedNode.check_constants`), not on every
step. Nothing is clipped.

The functions do not check that their values are finite. They run on every pass of every time step, and a NaN or
an infinity passes through their arithmetic into the outputs that publish the result; the simulator's check of
every result column after the run (:mod:`hisim.postprocessing.finite_results`) then names the column and its first
bad step.

Names keep this module's short unit suffixes: ``_w`` (W), ``_c`` (°C), ``_j`` (J), ``_s`` (s),
``_kg_per_s`` (kg/s), ``_w_per_k`` (W/K), ``_j_per_k`` (J/K).

Circuits
--------

A circuit's heat flow is ``m c (T_sup - T_ret)`` (:func:`circuit_power_w`), and its heat over a step of ``dt``
seconds is that heat flow times ``dt``. It is positive when the supply owner heats the receiving side and negative
for a cooling circuit with ``T_sup < T_ret``; the direction is the circuit's topology, so a mass flow is never
negative and nothing is mirrored.

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

:meth:`MixedNode.step` evaluates both through ``phi(a) = (1 - e^(-a)) / a``
(:meth:`MixedNode.relaxation_mean_factor`) as ``T_mean = T_inf + (T0 - T_inf) phi`` and
``T_end = T0 + (T_inf - T0) a phi``; the second form keeps the change ``T_end - T0`` free of cancellation when ``a``
is small. ``phi`` is ``-expm1(-a) / a`` for ``a >= MixedNode.SMALL_A_THRESHOLD`` (1e-4) and the Taylor polynomial
``1 - a/2 + a^2/6 - a^3/24`` below it, so ``a = 0`` (no flow and no loss: ``G = 0``) gives ``phi = 1`` and
``T_mean = T_end = T0`` without a division. The Taylor remainder below the threshold is at most
``a^4/120 < 8.4e-19``, under a fortieth of an ulp of ``phi ~ 1``.

The heat each inflow brings is ``m_i c (T_i - T_mean) dt`` and the loss ``UA (T_mean - T_amb) dt``; their
difference is ``C (T_end - T0)`` exactly, and ``T_mean`` and ``T_end`` never leave the range of ``T0``, the inflow
temperatures and ``T_amb``. A mixed node therefore publishes ``T_mean`` as the return of the circuits it receives:
the heat a circuit brings, derived from its flow, its supply and that return, is then the heat the node integrated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar, Iterable, NamedTuple, Tuple


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


class SetTemperatureAboveMaximumError(HydronicsError):

    """A hot-water controller's set temperature lies above its generator's maximum supply temperature.

    The generator's supply is capped at its maximum, so a tank charged towards a set temperature above it never
    reaches the set temperature and the charge never ends; the configuration of the pair is refused instead.
    """


def circuit_power_w(*, mass_flow_kg_per_s: float, t_supply_c: float, t_return_c: float) -> float:
    """Return the heat flow a circuit's water carries, ``m c (T_sup - T_ret)``, in W.

    This is the thermal power a component books for a circuit it owns: the heat its water carries, nothing else.
    It is negative for a cooling circuit, whose supply is colder than its return. For example, 0.4 kg/s leaving at
    35.0 °C with a return of 30.04 °C carries ``0.4 * 4180 * 4.96 = 8293.12`` W. A circuit's heat over a step is this
    power times the step duration, evaluated in that order, so the power a component books and the heat an energy
    balance derives from the same three values agree to the last bit.

    Args:
        mass_flow_kg_per_s: The circuit's mass flow, in kg/s, at least 0.
        t_supply_c: The supply temperature, the water leaving the supply owner, in °C.
        t_return_c: The return temperature, the water coming back to the supply owner, in °C.

    Returns:
        The heat flow, in W.

    Raises:
        NegativeMassFlowError: If the mass flow is negative.
    """
    if mass_flow_kg_per_s < 0.0:
        raise NegativeMassFlowError(f"mass_flow_kg_per_s must be >= 0 kg/s, got {mass_flow_kg_per_s!r}.")
    return mass_flow_kg_per_s * Water.SPECIFIC_HEAT_J_PER_KG_K * (t_supply_c - t_return_c)


@dataclass(frozen=True)
class FluidStream:

    """The stream of water the owner of a circuit sends into it over one step: its mass flow, its supply temperature
    and the heat flow it carries.

    A stream, as in process engineering or a Modelica fluid port, is a flow with its state; HiSim's circuits have no
    transport delay, so the stream holds for the whole step and reaches the receiver within it. A value object, so a
    flow and a temperature cannot be swapped by position. ``power_w`` is :func:`circuit_power_w` of the flow, the supply
    and the return the owner read (:meth:`carrying`); an idle circuit has no flow, and its supply is its return
    (:meth:`idle`).
    """

    #: The circuit's mass flow, in kg/s.
    mass_flow_kg_per_s: float
    #: The circuit's supply temperature, in °C.
    t_supply_c: float
    #: The heat flow the circuit's water carries, in W.
    power_w: float

    @staticmethod
    def idle(*, t_return_c: float) -> "FluidStream":
        """Return the stream of a circuit that moves no water: no flow, its supply at its return, no heat.

        Args:
            t_return_c: The circuit's return temperature, in °C.

        Returns:
            The idle stream.
        """
        return FluidStream(mass_flow_kg_per_s=0.0, t_supply_c=t_return_c, power_w=0.0)

    @staticmethod
    def carrying(*, mass_flow_kg_per_s: float, t_supply_c: float, t_return_c: float) -> "FluidStream":
        """Return the stream of a circuit that moves a flow at a supply temperature, with the heat its water carries.

        ``power_w`` is :func:`circuit_power_w` of the flow, the supply and the return, so an owner that builds its stream
        here books exactly the heat the receiver derives from the same three values. For example, 0.025 kg/s supplied
        at 60 °C with a 40 °C return carry 2090 W.

        Args:
            mass_flow_kg_per_s: The circuit's mass flow, in kg/s, at least 0.
            t_supply_c: The supply temperature, the water leaving the owner, in °C.
            t_return_c: The return temperature the owner read, in °C.

        Returns:
            The stream with that flow, that supply and the heat the flow carries.

        Raises:
            NegativeMassFlowError: If the mass flow is negative.
        """
        return FluidStream(
            mass_flow_kg_per_s=mass_flow_kg_per_s,
            t_supply_c=t_supply_c,
            power_w=circuit_power_w(mass_flow_kg_per_s=mass_flow_kg_per_s, t_supply_c=t_supply_c, t_return_c=t_return_c),
        )

    def at_part_load(self, *, part_load_ratio: float, t_return_c: float) -> "FluidStream":
        """Return this full-load stream of a circuit when its generator runs only a fraction of the step.

        A generator that runs a fraction of a step at full load publishes the averaged flow, its full-load flow times
        the ratio, at its full-load supply temperature, and the heat that flow carries (:meth:`carrying`). A ratio of 1
        returns this stream unchanged, and a ratio of 0 returns the idle stream (:meth:`idle`): no flow, the supply at the
        return. For example, a stream of 0.0144 kg/s at 75 °C on a 50 °C return gives 0.0036 kg/s carrying 376.2 W at a
        ratio of 0.25.

        The heat is computed from the averaged flow rather than as the ratio times the full-load heat, so the heat a
        circuit publishes is the heat a receiver derives from its published flow, supply and return, to the last bit.
        The supply drops to the return at a ratio of 0 because a full-load supply published with no flow would read as
        a running generator in the outlet temperature column.

        Args:
            part_load_ratio: The fraction of the step the generator runs, from 0 to 1.
            t_return_c: The circuit's return temperature, in °C, the one this full-load stream was built with;
                the supply of the idle stream at a ratio of 0.

        Returns:
            The stream at that ratio.

        Raises:
            ValueError: If the ratio lies outside [0, 1].
        """
        if not 0.0 <= part_load_ratio <= 1.0:
            raise ValueError(f"A part-load ratio is a fraction from 0 to 1, not {part_load_ratio}.")
        if part_load_ratio == 1.0:
            return self
        if part_load_ratio == 0.0:
            return FluidStream.idle(t_return_c=t_return_c)
        return FluidStream.carrying(
            mass_flow_kg_per_s=part_load_ratio * self.mass_flow_kg_per_s, t_supply_c=self.t_supply_c, t_return_c=t_return_c
        )


def throttled_supply_temperature_c(*, unthrottled_supply_c: float, return_c: float, supply_limit_c: float) -> float:
    """Return the supply temperature of a generator that holds its lift, throttled at a supply limit, in °C.

    A generator that holds a lift supplies its return plus that lift, the unthrottled supply. When the unthrottled
    supply exceeds the limit, the supply stops at the limit, or at the return if the return is already hotter, so
    a throttled generator never cools its circuit; the flow stays as the generator pumps it, so the circuit then
    carries ``m c (T_sup - T_ret)``. For example, with an 80 °C limit a 72 °C return and a 10 K lift give 80 °C, a
    85 °C return gives 85 °C, and a 60 °C return gives 70 °C. An unthrottled supply that is NaN is returned as it is,
    so that it reaches the supply output and the check of the results after the run names it.

    Args:
        unthrottled_supply_c: The supply the generator's own law gives, in °C.
        return_c: The circuit's return temperature, in °C.
        supply_limit_c: The highest supply temperature the generator delivers, in °C.

    Returns:
        The supply temperature the circuit carries, in °C.
    """
    if not unthrottled_supply_c > supply_limit_c:
        return unthrottled_supply_c
    return max(supply_limit_c, return_c)


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
        SetTemperatureAboveMaximumError: If ``set_supply_c`` lies above ``maximal_supply_c``, so that a charge
            towards it never ends.
    """
    if set_supply_c > maximal_supply_c:
        raise SetTemperatureAboveMaximumError(
            f"The hot-water set temperature {set_supply_c!r} °C lies above the generator's maximum supply temperature "
            f"{maximal_supply_c!r} °C, so a charge towards it never ends; lower the controller's set temperature or "
            "raise the generator's maximum."
        )
    limit = min(maximal_supply_c, set_supply_c)
    return throttled_supply_temperature_c(
        unthrottled_supply_c=unthrottled_supply_c, return_c=return_c, supply_limit_c=limit
    )


class Inflow(NamedTuple):

    """One water stream entering a node at a constant temperature for the step.

    The supply of a charging circuit, the return of a distribution circuit, or the cold refill of a tap. For
    example, ``Inflow(0.2, 70.0)`` is 0.2 kg/s of water at 70 °C. A tank builds several of these on every pass of
    every step, so the type is a named tuple, which is cheap to build, and it checks nothing itself:
    :meth:`MixedNode.step` refuses a negative mass flow.
    """

    #: The stream's mass flow, in kg/s, at least 0.
    mass_flow_kg_per_s: float
    #: The stream's temperature, in °C.
    temperature_c: float


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


class MixedNode:

    """A fully mixed water node, integrated over one step in closed form without sub-stepping.

    A namespace for the node step, its relaxation mean factor and their numerical threshold. The step solves
    ``C dT/dt = sum_i m_i c (T_i - T) - UA (T - T_amb)`` exactly for constant inflow temperatures, so its result does
    not depend on the step length and its heat balance closes to float precision.
    """

    #: Below this relaxation exponent ``a = k dt`` the relaxation mean factor is the Taylor polynomial, at or above
    #: it ``-expm1(-a)/a``; at it, the polynomial's remainder ``a^4/120`` is under a fortieth of an ulp of 1.
    SMALL_A_THRESHOLD: ClassVar[float] = 1e-4

    @staticmethod
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
            HydronicsError: If ``a`` is negative; a NaN passes through as a NaN factor.
        """
        if a < 0.0:
            raise HydronicsError(f"The relaxation exponent a = k dt must be >= 0, got {a!r}.")
        if a < MixedNode.SMALL_A_THRESHOLD:
            return 1.0 - a / 2.0 * (1.0 - a / 3.0 * (1.0 - a / 4.0))
        return -math.expm1(-a) / a

    @staticmethod
    def check_constants(*, ua_w_per_k: float, dt_s: float, heat_capacity_j_per_k: float) -> None:
        """Refuse a node's loss coefficient, step length or heat capacity that :meth:`step` cannot integrate with.

        The three values are constant over a run, so the component that owns a node calls this once, when it is
        built, and :meth:`step` does not check them on every pass. For example, a 250 l tank with
        ``ua_w_per_k=0.83``, ``dt_s=900.0`` and ``heat_capacity_j_per_k=1.04e6`` passes; a tank of no volume, whose
        heat capacity is 0, is refused. A NaN fails each comparison and is refused as well.

        Args:
            ua_w_per_k: The loss coefficient, in W/K, at least 0.
            dt_s: The step duration, in s, above 0.
            heat_capacity_j_per_k: The node's heat capacity ``C = M c``, in J/K, above 0.

        Raises:
            NegativeHeatLossCoefficientError: If ``ua_w_per_k`` is negative or NaN.
            NonPositiveTimestepError: If ``dt_s`` is not positive or is NaN.
            NonPositiveHeatCapacityError: If ``heat_capacity_j_per_k`` is not positive or is NaN.
        """
        if not ua_w_per_k >= 0.0:
            raise NegativeHeatLossCoefficientError(f"ua_w_per_k must be >= 0 W/K, got {ua_w_per_k!r}.")
        if not dt_s > 0.0:
            raise NonPositiveTimestepError(f"dt_s must be > 0 s, got {dt_s!r}.")
        if not heat_capacity_j_per_k > 0.0:
            raise NonPositiveHeatCapacityError(f"heat_capacity_j_per_k must be > 0 J/K, got {heat_capacity_j_per_k!r}.")

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

        The loss coefficient, the step duration and the heat capacity are not checked here: they are constant over a
        run, and the component that owns the node checks them once with :meth:`check_constants`. Nor are the values
        checked to be finite; a NaN passes through into the result.

        Args:
            t0_c: The node's temperature at the start of the step, in °C.
            inflows: Every stream entering the node; read once, so any iterable will do.
            ua_w_per_k: The loss coefficient to ``t_amb_c``, in W/K, at least 0.
            t_amb_c: The temperature the node loses heat to, in °C.
            dt_s: The step duration, in s, above 0.
            heat_capacity_j_per_k: The node's heat capacity ``C = M c``, in J/K, above 0.

        Returns:
            The end temperature, the step mean, the heat of each inflow and the loss.

        Raises:
            NegativeMassFlowError: If an inflow's mass flow is negative.
        """
        inflows = tuple(inflows)
        conductances = []
        for inflow in inflows:
            if inflow.mass_flow_kg_per_s < 0.0:
                raise NegativeMassFlowError(
                    f"An inflow's mass_flow_kg_per_s must be >= 0 kg/s, got {inflow.mass_flow_kg_per_s!r}."
                )
            conductances.append(inflow.mass_flow_kg_per_s * Water.SPECIFIC_HEAT_J_PER_KG_K)
        total_conductance = math.fsum(conductances + [ua_w_per_k])
        if total_conductance > 0.0:
            weighted = [g * inflow.temperature_c for g, inflow in zip(conductances, inflows)] + [ua_w_per_k * t_amb_c]
            t_inf = math.fsum(weighted) / total_conductance
            a = total_conductance * dt_s / heat_capacity_j_per_k
        else:
            t_inf = t0_c
            a = 0.0
        phi = MixedNode.relaxation_mean_factor(a)
        t_mean = t_inf + (t0_c - t_inf) * phi
        t_end = t0_c + (t_inf - t0_c) * (a * phi)
        heat_in = tuple(g * (inflow.temperature_c - t_mean) * dt_s for g, inflow in zip(conductances, inflows))
        loss = ua_w_per_k * (t_mean - t_amb_c) * dt_s
        return NodeStep(t_end_c=t_end, t_mean_c=t_mean, heat_in_j_per_inflow=heat_in, loss_j=loss)
