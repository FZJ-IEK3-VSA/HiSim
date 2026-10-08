"""Property tests of the hydronics library (hydronic coupling spec §3-§4 and §6, stage A, hisim-fxix.2).

``hypothesis`` is not a declared test dependency, so the properties are checked over seeded random sweeps: every
sweep draws from its own :class:`random.Random` with a fixed seed, so a failure reproduces exactly.

The checks are platform independent. A reference the tests compute in floats sums with :func:`math.fsum`, exactly
rounded on every Python, never with :func:`sum`, whose float rounding changed in Python 3.12. A property the
mathematics states exactly (a bound, a sign) is checked up to ``ULPS_OF_FLOAT_NOISE`` ulps of the temperatures'
scale, the rounding a handful of float operations can leave: a value that close to a bound counts as on it.
"""

import inspect
import math
import random
import re
from decimal import Decimal, localcontext
from typing import Callable, Iterator, List, Sequence, Tuple

import numpy as np
import pytest

from hisim import hydronics
from hisim import loadtypes as lt
from hisim.components import simple_water_storage
from hisim.components.configuration import PhysicsConfig
from hisim.hydronics import (
    ACCELERATION_AFTER_ITERATIONS,
    SMALL_A_THRESHOLD,
    UNDER_RELAXATION_WEIGHT,
    WATER_SPECIFIC_HEAT_J_PER_KG_K,
    Inflow,
    MixedNode,
    NodeStep,
)

pytestmark = pytest.mark.base

C_WATER = WATER_SPECIFIC_HEAT_J_PER_KG_K
STEP_LENGTHS_S = (60.0, 900.0, 3600.0)
CASES_PER_STEP_LENGTH = 2000

#: How many ulps of a temperature scale count as float noise: the closed form takes a few rounded operations from
#: its arguments to ``T_mean`` and ``T_end``, each off by at most half an ulp of a value bounded by the scale.
ULPS_OF_FLOAT_NOISE = 4


def float_noise(scale: float) -> float:
    """``ULPS_OF_FLOAT_NOISE`` ulps of ``scale``: the distance below which two temperatures are the same value."""
    return ULPS_OF_FLOAT_NOISE * math.ulp(abs(scale))


class NodeCase:

    """One random node step: start temperature, inflows, loss, ambient, step length and heat capacity."""

    def __init__(self, rng: random.Random, dt_s: float) -> None:
        """Draw a case; about a fifth of the flows and losses are exactly zero."""
        self.t0_c = rng.uniform(5.0, 90.0)
        self.inflows = [
            Inflow(0.0 if rng.random() < 0.2 else rng.uniform(0.0, 0.5), rng.uniform(5.0, 90.0))
            for _ in range(rng.randint(0, 4))
        ]
        self.ua_w_per_k = 0.0 if rng.random() < 0.2 else rng.uniform(0.0, 10.0)
        self.t_amb_c = rng.uniform(-10.0, 30.0)
        self.dt_s = dt_s
        self.heat_capacity_j_per_k = rng.uniform(5.0, 2000.0) * C_WATER

    def step(self) -> NodeStep:
        """The library's step of this case."""
        return MixedNode.step(
            self.t0_c, self.inflows, self.ua_w_per_k, self.t_amb_c, self.dt_s, self.heat_capacity_j_per_k
        )

    def temperatures(self) -> List[float]:
        """Every temperature the node can relax towards, and its start."""
        return [self.t0_c, self.t_amb_c] + [inflow.temperature_c for inflow in self.inflows]

    def scale(self) -> float:
        """The largest magnitude among the case's temperatures: every temperature of the step is bounded by it."""
        return max(abs(t) for t in self.temperatures())

    def equilibrium_c(self) -> float:
        """``T_inf``, or the start temperature for a node with neither flow nor loss.

        Evaluated as :meth:`MixedNode.step` evaluates it, each sum exactly rounded with :func:`math.fsum`, so the
        reference is the library's equilibrium to the bit on every Python version.
        """
        conductances = [inflow.mass_flow_kg_per_s * C_WATER for inflow in self.inflows]
        conductance = math.fsum(conductances + [self.ua_w_per_k])
        if conductance == 0.0:
            return self.t0_c
        weighted = [g * inflow.temperature_c for g, inflow in zip(conductances, self.inflows)]
        return math.fsum(weighted + [self.ua_w_per_k * self.t_amb_c]) / conductance


def node_cases(seed: int) -> List[NodeCase]:
    """``CASES_PER_STEP_LENGTH`` cases at each of 60, 900 and 3600 s, drawn from ``seed``."""
    rng = random.Random(seed)
    return [NodeCase(rng, dt_s) for dt_s in STEP_LENGTHS_S for _ in range(CASES_PER_STEP_LENGTH)]


def phi_reference(a: float) -> Decimal:
    """``(1 - e^(-a)) / a`` to 50 significant digits for the float ``a`` (``a > 0``).

    The working precision grows with ``1/a`` so that ``1 - e^(-a)`` keeps 50 digits even for a subnormal ``a``.
    """
    exact = Decimal(a)
    with localcontext() as context:
        context.prec = 60 + max(0, -exact.adjusted())
        return (1 - (-exact).exp()) / exact


# --- constants -----------------------------------------------------------------------------------------------


def test_water_specific_heat_is_the_one_physics_config_states() -> None:
    """``PhysicsConfig``'s water c, which every storage reads today, is the library's constant (spec §2.2)."""
    water = PhysicsConfig.get_properties_for_energy_carrier(lt.LoadTypes.WATER)
    assert water.specific_heat_capacity_in_joule_per_kg_per_kelvin == WATER_SPECIFIC_HEAT_J_PER_KG_K


def test_water_density_is_the_storages_value_at_40_degrees() -> None:
    """The library's density, 0.992 kg/l at 40 °C, is every storage's and ``PhysicsConfig``'s.

    The space-heating buffer states it as a literal, the hot-water tank reads the library, and ``PhysicsConfig``
    (which the heat distribution system turns its pipe volume into a mass with) reads the library too.
    """
    source = inspect.getsource(simple_water_storage)
    stated = set(re.findall(r"density_water_at_40_degree_celsius_in_kg_per_liter = ([0-9.]+)", source))
    assert stated == {"0.992"}
    assert hydronics.WATER_DENSITY_KG_PER_M3 == float(stated.pop()) * 1000.0
    assert simple_water_storage.SimpleDHWStorage.WATER_DENSITY_IN_KG_PER_LITER * 1000.0 == (
        hydronics.WATER_DENSITY_KG_PER_M3
    )
    water = PhysicsConfig.get_properties_for_energy_carrier(lt.LoadTypes.WATER)
    assert water.density_in_kg_per_m3 == hydronics.WATER_DENSITY_KG_PER_M3


# --- the small-a threshold -----------------------------------------------------------------------------------


def threshold_probe_points() -> List[float]:
    """Log-spaced ``a`` from 1e-15 to 100, and the floats around the threshold on both sides."""
    points = [10.0 ** (exponent / 4.0) for exponent in range(-60, 9)]
    below = above = SMALL_A_THRESHOLD
    for _ in range(4):
        below = math.nextafter(below, 0.0)
        above = math.nextafter(above, math.inf)
        points += [below, above]
    points += [SMALL_A_THRESHOLD, SMALL_A_THRESHOLD * 0.5, SMALL_A_THRESHOLD * 2.0, 5e-324, 1e-300]
    return sorted(points)


@pytest.mark.parametrize("a", threshold_probe_points())
def test_relaxation_factor_matches_a_fifty_digit_reference(a: float) -> None:
    """``phi(a)`` is within two ulps of ``(1 - e^(-a))/a`` evaluated in 50-digit decimal arithmetic.

    This is the proof of ``SMALL_A_THRESHOLD``: below it the Taylor polynomial (remainder ``a^4/120``), above it
    ``-expm1(-a)/a``; both sides of the switch and the switch itself are probed.
    """
    computed = hydronics.relaxation_mean_factor(a)
    reference = phi_reference(a)
    error = abs(Decimal(computed) - reference) / reference
    assert error <= Decimal(2 * 2.0**-52), f"a={a!r}: relative error {error:.3e}"


def test_relaxation_factor_is_one_at_zero_without_division() -> None:
    """``a = 0`` (no flow, no loss) is the limit 1, not a ZeroDivisionError."""
    assert hydronics.relaxation_mean_factor(0.0) == 1.0


def test_taylor_remainder_bound_at_the_threshold_is_below_an_ulp() -> None:
    """The documented bound: the first dropped term, ``a^4/120``, is under a fortieth of an ulp of 1."""
    assert SMALL_A_THRESHOLD**4 / 120.0 < 2.0**-52 / 40.0


# --- the mixed node: closed form -----------------------------------------------------------------------------


def reference_step(case: NodeCase) -> Tuple[Decimal, Decimal]:
    """``(T_end, T_mean)`` of §4.1 in 50-digit decimal arithmetic, straight from the spec's formulas."""
    with localcontext() as context:
        context.prec = 50
        c = Decimal(C_WATER)
        conductance = sum((Decimal(i.mass_flow_kg_per_s) * c for i in case.inflows), Decimal(0)) + Decimal(
            case.ua_w_per_k
        )
        t0 = Decimal(case.t0_c)
        if conductance == 0:
            return t0, t0
        weighted = sum(
            (Decimal(i.mass_flow_kg_per_s) * c * Decimal(i.temperature_c) for i in case.inflows), Decimal(0)
        )
        t_inf = (weighted + Decimal(case.ua_w_per_k) * Decimal(case.t_amb_c)) / conductance
        a = conductance * Decimal(case.dt_s) / Decimal(case.heat_capacity_j_per_k)
        decay = (-a).exp()
        return t_inf + (t0 - t_inf) * decay, t_inf + (t0 - t_inf) * (1 - decay) / a


def test_step_matches_the_closed_form_in_fifty_digits() -> None:
    """``T_end`` and ``T_mean`` agree with the spec's formulas evaluated exactly, to 1e-12 K."""
    for case in node_cases(seed=11)[::10]:
        result = case.step()
        t_end, t_mean = reference_step(case)
        assert abs(Decimal(result.t_end_c) - t_end) < Decimal("1e-12")
        assert abs(Decimal(result.t_mean_c) - t_mean) < Decimal("1e-12")


def rk4_reference(case: NodeCase, substeps: int) -> Tuple[float, float]:
    """``(T_end, T_mean)`` by integrating the node's ODE with classical Runge-Kutta, independent of the closed form."""
    conductances = [(i.mass_flow_kg_per_s * C_WATER, i.temperature_c) for i in case.inflows]

    def derivative(temperature: float) -> float:
        gain = math.fsum(g * (t_in - temperature) for g, t_in in conductances)
        return (gain - case.ua_w_per_k * (temperature - case.t_amb_c)) / case.heat_capacity_j_per_k

    h = case.dt_s / substeps
    temperature = case.t0_c
    integral = 0.0
    for _ in range(substeps):
        # The state is (T, integral of T); RK4 on both, the integral's derivative being T itself.
        k1 = derivative(temperature)
        t2 = temperature + h / 2 * k1
        k2 = derivative(t2)
        t3 = temperature + h / 2 * k2
        k3 = derivative(t3)
        t4 = temperature + h * k3
        k4 = derivative(t4)
        integral += h / 6 * (temperature + 2 * t2 + 2 * t3 + t4)
        temperature += h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
    return temperature, integral / case.dt_s


def test_step_solves_the_node_equation() -> None:
    """An independent numerical integration of ``C dT/dt = sum m c (T_i - T) - UA (T - T_amb)`` agrees."""
    for case in node_cases(seed=12)[::200]:
        result = case.step()
        t_end, t_mean = rk4_reference(case, substeps=4000)
        assert result.t_end_c == pytest.approx(t_end, abs=1e-6)
        assert result.t_mean_c == pytest.approx(t_mean, abs=1e-6)


# --- the mixed node: properties of §4.2 ----------------------------------------------------------------------


def test_exact_closure() -> None:
    """``C (T_end - T0) = sum m_i c (T_i - T_mean) dt - UA (T_mean - T_amb) dt`` to 1e-9 relative (§4.2)."""
    worst = 0.0
    for case in node_cases(seed=1):
        result = case.step()
        stored = case.heat_capacity_j_per_k * (result.t_end_c - case.t0_c)
        recomputed_inflows = [
            i.mass_flow_kg_per_s * C_WATER * (i.temperature_c - result.t_mean_c) * case.dt_s for i in case.inflows
        ]
        recomputed_loss = case.ua_w_per_k * (result.t_mean_c - case.t_amb_c) * case.dt_s
        assert result.heat_in_j_per_inflow == pytest.approx(recomputed_inflows, rel=1e-15, abs=0)
        assert result.loss_j == pytest.approx(recomputed_loss, rel=1e-15, abs=0)
        residual = math.fsum(result.heat_in_j_per_inflow) - result.loss_j - stored
        scale = math.fsum(abs(q) for q in result.heat_in_j_per_inflow) + abs(result.loss_j) + abs(stored)
        if scale == 0.0:
            assert residual == 0.0
            continue
        worst = max(worst, abs(residual) / scale)
    assert worst <= 1e-9, f"worst relative residual {worst:.3e}"


def test_no_overshoot() -> None:
    """``T_mean`` and ``T_end`` stay within ``[min, max]`` of ``T0``, the inflow temperatures and ``T_amb``.

    Exactly so in the mathematics; in floats ``T_inf`` is a rounded weighted mean, which can land an ulp outside
    the range when every temperature it weighs is the same, so the bound holds up to float noise.
    """
    for case in node_cases(seed=2):
        result = case.step()
        low, high = min(case.temperatures()), max(case.temperatures())
        noise = float_noise(case.scale())
        assert low - noise <= result.t_mean_c <= high + noise
        assert low - noise <= result.t_end_c <= high + noise


def test_monotone_relaxation_toward_equilibrium() -> None:
    """Over repeated steps with constant inflows ``T`` approaches ``T_inf`` monotonically and never passes it.

    Within each step the mean lies between the start and the end temperature. A temperature within float noise
    of ``T_inf`` is at the equilibrium: it has no side to stay on, so the sign check skips a step that starts or
    ends there. A step with ``a`` so large that ``1 - e^(-a)`` rounds to 1 lands on ``T_inf`` up to rounding.
    """
    for case in node_cases(seed=3)[::20]:
        t_inf = case.equilibrium_c()
        noise = float_noise(case.scale())
        temperature = case.t0_c
        distance = abs(temperature - t_inf)
        for _ in range(30):
            result = MixedNode.step(
                temperature, case.inflows, case.ua_w_per_k, case.t_amb_c, case.dt_s, case.heat_capacity_j_per_k
            )
            if abs(temperature - t_inf) > noise and abs(result.t_end_c - t_inf) > noise:
                assert (result.t_end_c - t_inf) * (temperature - t_inf) > 0.0, "passed the equilibrium"
            new_distance = abs(result.t_end_c - t_inf)
            assert new_distance <= distance + noise
            assert min(temperature, result.t_end_c) - noise <= result.t_mean_c
            assert result.t_mean_c <= max(temperature, result.t_end_c) + noise
            temperature, distance = result.t_end_c, new_distance


def test_zero_flow_and_zero_loss_leave_the_node_unchanged() -> None:
    """``m = 0`` and ``UA = 0``: the ``a -> 0`` limit gives ``T_mean = T_end = T0`` exactly."""
    rng = random.Random(4)
    for _ in range(500):
        t0 = rng.uniform(-20.0, 95.0)
        inflows = [Inflow(0.0, rng.uniform(5.0, 90.0)) for _ in range(rng.randint(0, 3))]
        result = MixedNode.step(t0, inflows, 0.0, rng.uniform(-10.0, 30.0), rng.choice(STEP_LENGTHS_S), 1e6)
        assert result.t_end_c == t0
        assert result.t_mean_c == t0
        assert result.loss_j == 0.0
        assert all(q == 0.0 for q in result.heat_in_j_per_inflow)


def test_two_half_steps_equal_one_step() -> None:
    """One step of ``dt`` equals two steps of ``dt/2`` with the same inflows, to float precision.

    The end temperature agrees, the step mean is the mean of the two half-step means, and the inflow heats
    and the loss add up.
    """
    for case in node_cases(seed=5)[::4]:
        whole = case.step()
        half = case.dt_s / 2.0
        first = MixedNode.step(
            case.t0_c, case.inflows, case.ua_w_per_k, case.t_amb_c, half, case.heat_capacity_j_per_k
        )
        second = MixedNode.step(
            first.t_end_c, case.inflows, case.ua_w_per_k, case.t_amb_c, half, case.heat_capacity_j_per_k
        )
        scale = case.scale()
        assert whole.t_end_c == pytest.approx(second.t_end_c, rel=0, abs=8 * 2.0**-52 * scale)
        assert whole.t_mean_c == pytest.approx(
            (first.t_mean_c + second.t_mean_c) / 2.0, rel=0, abs=8 * 2.0**-52 * scale
        )
        joules = case.heat_capacity_j_per_k * scale + 1.0
        for q_whole, q_first, q_second in zip(
            whole.heat_in_j_per_inflow, first.heat_in_j_per_inflow, second.heat_in_j_per_inflow
        ):
            assert q_whole == pytest.approx(q_first + q_second, rel=1e-12, abs=1e-12 * joules)
        assert whole.loss_j == pytest.approx(first.loss_j + second.loss_j, rel=1e-12, abs=1e-12 * joules)


def test_cooling_inflow_brings_negative_heat() -> None:
    """A flow colder than the node cools it with the same equation, and its heat is negative (§4.2)."""
    result = MixedNode.step(45.0, [Inflow(0.3, 7.0)], 0.0, 20.0, 900.0, 300.0 * C_WATER)
    assert result.t_end_c < result.t_mean_c < 45.0
    assert result.heat_in_j_per_inflow[0] < 0.0


# --- circuit heat and kWh (§3.4, §3.5) -----------------------------------------------------------------------


def test_circuit_heat_is_m_c_delta_t_dt() -> None:
    """0.1 kg/s, 10 K, one hour: 4180 J/(kg K) * 0.1 * 10 * 3600 = 15.048 MJ = 4.18 kWh."""
    assert hydronics.circuit_heat_j(0.1, 60.0, 50.0, 3600.0) == pytest.approx(15.048e6, rel=1e-15)
    assert hydronics.kilowatt_hours(0.1, 60.0, 50.0, 3600.0) == pytest.approx(4.18, rel=1e-15)


@pytest.mark.parametrize(
    ("mass_flow", "t_supply", "t_return", "dt"),
    [(0.4, 35.0, 30.04, 900.0), (0.333, 7.0, 12.3, 60.0), (0.0, 55.0, 45.0, 3600.0), (1.7, 40.1, 40.1, 1.0)],
)
def test_circuit_heat_is_the_circuit_power_times_the_step_to_the_last_bit(
    mass_flow: float, t_supply: float, t_return: float, dt: float
) -> None:
    """What a component books as power and what the balance derives as heat from the same values agree exactly."""
    power = hydronics.circuit_power_w(mass_flow, t_supply, t_return)
    assert power == mass_flow * C_WATER * (t_supply - t_return)
    assert hydronics.circuit_heat_j(mass_flow, t_supply, t_return, dt) == power * dt


def test_circuit_power_is_m_c_delta_t_and_refuses_what_circuit_heat_refuses() -> None:
    """0.4 kg/s over 4.96 K carries 8293.12 W; a negative flow and a non-finite temperature are refused."""
    assert hydronics.circuit_power_w(0.4, 35.0, 30.04) == pytest.approx(8293.12, rel=1e-12)
    with pytest.raises(hydronics.NegativeMassFlowError):
        hydronics.circuit_power_w(-0.1, 35.0, 30.0)
    with pytest.raises(hydronics.NonFiniteValueError):
        hydronics.circuit_power_w(0.1, math.nan, 30.0)
    with pytest.raises(hydronics.NonFiniteValueError, match="circuit power"):
        hydronics.circuit_power_w(1e306, 1e3, -1e3)


def test_kilowatt_hours_sign_rule() -> None:
    """Heating (``T_sup > T_ret``) is positive, cooling (``T_sup < T_ret``) negative, no flow or no lift zero."""
    assert hydronics.kilowatt_hours(0.2, 55.0, 45.0, 900.0) > 0.0
    assert hydronics.kilowatt_hours(0.2, 7.0, 12.0, 900.0) < 0.0
    assert hydronics.kilowatt_hours(0.2, 7.0, 12.0, 900.0) == -hydronics.kilowatt_hours(0.2, 12.0, 7.0, 900.0)
    assert hydronics.kilowatt_hours(0.0, 55.0, 45.0, 900.0) == 0.0
    assert hydronics.kilowatt_hours(0.2, 45.0, 45.0, 900.0) == 0.0


def test_node_heat_equals_circuit_heat_with_the_step_mean_as_return() -> None:
    """The heat a node takes from a charging circuit is the circuit heat with the node's ``T_mean`` as return."""
    result = MixedNode.step(40.0, [Inflow(0.2, 60.0)], 2.0, 20.0, 900.0, 300.0 * C_WATER)
    circuit = hydronics.circuit_heat_j(0.2, 60.0, result.t_mean_c, 900.0)
    assert result.heat_in_j_per_inflow[0] == pytest.approx(circuit, rel=1e-15)


# --- the tap valve (§4.3) ------------------------------------------------------------------------------------

T_WARM = 40.0
T_COLD = 10.0


def demand_heat(demand: float) -> float:
    """The heat the household asks for: ``m_d c (T_warm - T_cold)`` per second."""
    return demand * C_WATER * (T_WARM - T_COLD)


def test_valve_draws_the_demanded_heat_above_t_warm() -> None:
    """Above ``T_warm`` the drawn heat ``m_hot c (T - T_cold)`` equals the demand's heat, nothing unmet."""
    rng = random.Random(6)
    for _ in range(2000):
        tank, demand = rng.uniform(T_WARM + 1e-9, 95.0), rng.uniform(0.0, 0.5)
        m_hot, unmet = hydronics.mixing_valve_draw(tank, T_WARM, T_COLD, demand)
        assert unmet == 0.0
        assert 0.0 <= m_hot <= demand
        assert m_hot * C_WATER * (tank - T_COLD) == pytest.approx(demand_heat(demand), rel=1e-14, abs=1e-12)


def test_valve_is_continuous_at_t_warm() -> None:
    """At ``T_warm`` both branches give the whole demand and nothing unmet; just above and below agree with it."""
    demand = 0.12
    assert hydronics.mixing_valve_draw(T_WARM, T_WARM, T_COLD, demand) == (demand, 0.0)
    above = hydronics.mixing_valve_draw(math.nextafter(T_WARM, math.inf), T_WARM, T_COLD, demand)
    below = hydronics.mixing_valve_draw(math.nextafter(T_WARM, 0.0), T_WARM, T_COLD, demand)
    assert above[0] == pytest.approx(demand, rel=1e-14)
    assert above[1] == 0.0
    assert below[0] == demand
    assert below[1] == pytest.approx(0.0, abs=1e-14)


def test_valve_between_cold_and_warm_passes_everything_and_reports_the_shortfall() -> None:
    """Between ``T_cold`` and ``T_warm`` all of ``m_d`` passes; delivered heat is the demand times (1 - unmet)."""
    rng = random.Random(7)
    for _ in range(2000):
        tank, demand = rng.uniform(math.nextafter(T_COLD, math.inf), T_WARM), rng.uniform(0.0, 0.5)
        m_hot, unmet = hydronics.mixing_valve_draw(tank, T_WARM, T_COLD, demand)
        assert m_hot == demand
        assert 0.0 <= unmet < 1.0
        delivered = m_hot * C_WATER * (tank - T_COLD)
        assert delivered == pytest.approx(demand_heat(demand) * (1.0 - unmet), rel=1e-12, abs=1e-9)


def test_valve_at_or_below_t_cold_draws_nothing() -> None:
    """At or below ``T_cold`` nothing is drawn and the whole demand is unmet."""
    assert hydronics.mixing_valve_draw(T_COLD, T_WARM, T_COLD, 0.1) == (0.0, 1.0)
    assert hydronics.mixing_valve_draw(T_COLD - 5.0, T_WARM, T_COLD, 0.1) == (0.0, 1.0)


def test_valve_unmet_fraction_is_bounded_and_monotone() -> None:
    """The unmet fraction lies in [0, 1] and does not rise as the tank gets warmer."""
    temperatures = [T_COLD - 5.0 + 0.01 * j for j in range(6000)]
    unmet = [hydronics.mixing_valve_draw(t, T_WARM, T_COLD, 0.1)[1] for t in temperatures]
    assert all(0.0 <= u <= 1.0 for u in unmet)
    assert all(later <= earlier for earlier, later in zip(unmet, unmet[1:]))


# --- node acceleration (§6) ----------------------------------------------------------------------------------


def plain_history(
    fixed_point_map: Callable[[float], float], start: float, count: int
) -> Tuple[List[float], List[float]]:
    """``count`` plain iterates of ``fixed_point_map`` from ``start``: published values and what they produced."""
    published, computed = [], []
    value = start
    for _ in range(count):
        published.append(value)
        value = fixed_point_map(value)
        computed.append(value)
    return published, computed


def test_acceleration_waits_six_iterations() -> None:
    """Up to six iterates the node publishes its plain step mean."""
    for count in range(1, ACCELERATION_AFTER_ITERATIONS + 1):
        published, computed = plain_history(lambda x: 0.5 * x + 20.0, 10.0, count)
        assert hydronics.accelerated_node_mean(published, computed) == computed[-1]


@pytest.mark.parametrize("theta", [0.05, 0.57, 0.95])
def test_secant_lands_on_the_fixed_point_of_a_contraction(theta: float) -> None:
    """For a linear contraction ``F(x) = theta x + b`` (§6's model) the secant step is the fixed point."""
    fixed_point = 55.0

    def contraction(x: float) -> float:
        return theta * x + (1.0 - theta) * fixed_point

    published, computed = plain_history(contraction, 20.0, ACCELERATION_AFTER_ITERATIONS + 1)
    accelerated = hydronics.accelerated_node_mean(published, computed)
    assert accelerated == pytest.approx(fixed_point, abs=1e-9)
    assert abs(accelerated - fixed_point) < abs(computed[-1] - fixed_point) or computed[-1] == fixed_point


def test_oscillation_is_under_relaxed() -> None:
    """A residual that changes sign is under-relaxed with weight 1/2, which damps ``F(x) = -0.9 x + b``."""
    fixed_point = 50.0

    def oscillating(x: float) -> float:
        return -0.9 * x + 1.9 * fixed_point

    published, computed = plain_history(oscillating, 40.0, ACCELERATION_AFTER_ITERATIONS + 1)
    accelerated = hydronics.accelerated_node_mean(published, computed)
    residual = computed[-1] - published[-1]
    assert accelerated == published[-1] + UNDER_RELAXATION_WEIGHT * residual
    assert abs(accelerated - fixed_point) <= 0.05 * abs(published[-1] - fixed_point) + 1e-12


def test_a_non_contracting_estimate_falls_back_to_the_plain_iterate() -> None:
    """When the secant's contraction estimate is outside [0, 1) the plain step mean is published."""
    published, computed = plain_history(lambda x: 1.2 * x - 5.0, 30.0, ACCELERATION_AFTER_ITERATIONS + 1)
    assert hydronics.accelerated_node_mean(published, computed) == computed[-1]


def test_acceleration_never_moves_a_converged_fixed_point() -> None:
    """A zero residual returns the published value, whatever the history before it."""
    published = [30.0, 40.0, 45.0, 47.0, 48.0, 48.5, 49.0, 50.0]
    computed = [40.0, 45.0, 47.0, 48.0, 48.5, 49.0, 50.0, 50.0]
    assert hydronics.accelerated_node_mean(published, computed) == 50.0


def iterate_loop(accelerate: bool, tolerance: float = 1e-4, limit: int = 100) -> Tuple[int, float]:
    """A node fed by a generator that holds its lift, iterated like the simulator: count and converged mean.

    A 135 kg tank, a generator at 0.3 kg/s with a 15 K lift (so ``m dt / M = 2`` at 900 s) and a distribution
    return of 0.1 kg/s at 30 °C. Each iteration the generator answers the published mean with
    ``T_sup = T_mean + 15``, the node steps, and the node publishes its mean, accelerated or not. Converged when
    the published mean changes by no more than the simulator's 1e-4.
    """
    capacity = 135.0 * C_WATER
    published: List[float] = []
    computed: List[float] = []
    current = 45.0
    for iteration in range(1, limit + 1):
        inflows = [Inflow(0.3, current + 15.0), Inflow(0.1, 30.0)]
        mean = MixedNode.step(45.0, inflows, 1.5, 20.0, 900.0, capacity).t_mean_c
        published.append(current)
        computed.append(mean)
        following = hydronics.accelerated_node_mean(published, computed) if accelerate else mean
        if abs(following - current) <= tolerance:
            return iteration, following
        current = following
    raise AssertionError("the loop did not converge")


def test_acceleration_brings_a_lift_holding_loop_under_the_force_convergence_limit() -> None:
    """The §6 target: a loop that needs more than 10 plain iterations converges within 10, to the same answer."""
    plain_count, plain_mean = iterate_loop(accelerate=False)
    fast_count, fast_mean = iterate_loop(accelerate=True)
    assert plain_count > 10
    assert fast_count <= 10
    assert fast_mean == pytest.approx(plain_mean, abs=1e-3)


# --- refusals ------------------------------------------------------------------------------------------------

GOOD_STEP = {
    "t0_c": 40.0,
    "inflows": (Inflow(0.2, 60.0),),
    "ua_w_per_k": 2.0,
    "t_amb_c": 20.0,
    "dt_s": 900.0,
    "heat_capacity_j_per_k": 300.0 * C_WATER,
}


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("ua_w_per_k", -0.1, hydronics.NegativeHeatLossCoefficientError),
        ("heat_capacity_j_per_k", 0.0, hydronics.NonPositiveHeatCapacityError),
        ("dt_s", 0.0, hydronics.NonPositiveTimestepError),
        ("t0_c", math.nan, hydronics.NonFiniteValueError),
        ("ua_w_per_k", math.nan, hydronics.NonFiniteValueError),
        ("t_amb_c", math.nan, hydronics.NonFiniteValueError),
        ("dt_s", math.nan, hydronics.NonFiniteValueError),
        ("heat_capacity_j_per_k", math.nan, hydronics.NonFiniteValueError),
        ("t0_c", math.inf, hydronics.NonFiniteValueError),
        ("inflows", ((0.2, 60.0),), hydronics.HydronicsError),
    ],
)
def test_node_step_refusals(field: str, value: object, error: type) -> None:
    """Every invalid argument of a node step is refused with its named error, a ValueError."""
    arguments = dict(GOOD_STEP, **{field: value})
    with pytest.raises(error):
        MixedNode.step(**arguments)  # type: ignore[arg-type]
    assert issubclass(error, ValueError)


@pytest.mark.parametrize(
    ("changes", "quantity"),
    [
        ({"inflows": (Inflow(1e306, 50.0),)}, "conductance m c of inflow 0"),
        ({"inflows": (Inflow(1e304, 50.0),) * 5}, "total conductance"),
        ({"inflows": (Inflow(1e301, 4000.0),) * 2}, "equilibrium numerator"),
        ({"inflows": (Inflow(1e300, 60.0),), "dt_s": 1e10, "heat_capacity_j_per_k": 1.0}, "relaxation exponent"),
        (
            {"t0_c": 1e300, "inflows": (Inflow(1e3, -1e300),), "ua_w_per_k": 0.0, "dt_s": 1e10,
             "heat_capacity_j_per_k": 1e300},
            "heat of inflow 0",
        ),
    ],
)
def test_node_step_refuses_a_result_that_overflows(changes: dict, quantity: str) -> None:
    """Finite arguments whose arithmetic overflows are refused, naming the quantity, never returned as inf/NaN."""
    with pytest.raises(hydronics.NonFiniteValueError, match=quantity):
        MixedNode.step(**dict(GOOD_STEP, **changes))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("arguments", "quantity"),
    [((1e300, 100.0, 0.0, 1e10), "circuit heat"), ((0.1, 1.5e308, -1.5e308, 1.0), "lift")],
)
@pytest.mark.parametrize("function", [hydronics.circuit_heat_j, hydronics.kilowatt_hours])
def test_circuit_heat_refuses_a_result_that_overflows(
    function: Callable[..., float], arguments: Tuple[float, ...], quantity: str
) -> None:
    """A circuit heat beyond the float range is refused with the quantity named, in J and in kWh."""
    with pytest.raises(hydronics.NonFiniteValueError, match=quantity):
        function(*arguments)


@pytest.mark.parametrize(
    ("arguments", "quantity"),
    [((0.0, 1.5e308, -1.5e308, 0.1), "span"), ((1e308, -5e307, -1e308, 0.1), "lift over the mains")],
)
def test_valve_refuses_a_result_that_overflows(arguments: Tuple[float, float, float, float], quantity: str) -> None:
    """A valve whose temperature differences overflow is refused rather than answering a rounded-away fraction."""
    with pytest.raises(hydronics.NonFiniteValueError, match=quantity):
        hydronics.mixing_valve_draw(*arguments)


def test_acceleration_refuses_a_residual_that_overflows() -> None:
    """Finite iterates whose residual overflows are refused, naming the residual."""
    published = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, -1e308, 1e308]
    computed = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 1e308, 1e308]
    with pytest.raises(hydronics.NonFiniteValueError, match="previous residual"):
        hydronics.accelerated_node_mean(published, computed)


def test_acceleration_reads_only_the_last_two_pairs() -> None:
    """The node owns its history; only the length and the last two pairs are read, so only they are validated."""
    published, computed = plain_history(lambda x: 0.5 * x + 20.0, 10.0, ACCELERATION_AFTER_ITERATIONS + 1)
    expected = hydronics.accelerated_node_mean(published, computed)
    published[0] = computed[0] = math.nan
    assert hydronics.accelerated_node_mean(published, computed) == expected


def step_with(inflows: object) -> NodeStep:
    """The node step of ``GOOD_STEP`` with these inflows."""
    return MixedNode.step(
        GOOD_STEP["t0_c"],  # type: ignore[arg-type]
        inflows,  # type: ignore[arg-type]
        GOOD_STEP["ua_w_per_k"],  # type: ignore[arg-type]
        GOOD_STEP["t_amb_c"],  # type: ignore[arg-type]
        GOOD_STEP["dt_s"],  # type: ignore[arg-type]
        GOOD_STEP["heat_capacity_j_per_k"],  # type: ignore[arg-type]
    )


def test_an_inflow_stores_floats() -> None:
    """An inflow keeps the float of what it was given, so a ``Decimal`` or NumPy scalar cannot reach the step."""
    inflow = Inflow(Decimal("0.2"), np.float32(60.0))  # type: ignore[arg-type]
    assert isinstance(inflow.mass_flow_kg_per_s, float) and inflow.mass_flow_kg_per_s == 0.2
    assert isinstance(inflow.temperature_c, float) and inflow.temperature_c == 60.0
    assert step_with((inflow,)) == step_with((Inflow(0.2, 60.0),))


def test_a_node_step_reads_its_inflows_once() -> None:
    """A one-shot iterable of inflows gives the same step as a tuple: the step materializes it once."""
    inflows = (Inflow(0.2, 60.0), Inflow(0.05, 30.0))

    def one_shot() -> Iterator[Inflow]:
        yield from inflows

    by_generator = step_with(one_shot())
    assert by_generator == step_with(inflows)
    assert len(by_generator.heat_in_j_per_inflow) == 2


@pytest.mark.parametrize(
    ("mass_flow", "temperature", "error"),
    [
        (-1e-9, 50.0, hydronics.NegativeMassFlowError),
        (math.nan, 50.0, hydronics.NonFiniteValueError),
        (math.inf, 50.0, hydronics.NonFiniteValueError),
        (0.1, math.nan, hydronics.NonFiniteValueError),
        (0.1, -math.inf, hydronics.NonFiniteValueError),
    ],
)
def test_inflow_refusals(mass_flow: float, temperature: float, error: type) -> None:
    """An inflow with a negative or non-finite flow, or a non-finite temperature, cannot be built."""
    with pytest.raises(error):
        Inflow(mass_flow, temperature)


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ((-0.1, 60.0, 50.0, 900.0), hydronics.NegativeMassFlowError),
        ((0.1, 60.0, 50.0, 0.0), hydronics.NonPositiveTimestepError),
        ((math.nan, 60.0, 50.0, 900.0), hydronics.NonFiniteValueError),
        ((0.1, math.nan, 50.0, 900.0), hydronics.NonFiniteValueError),
        ((0.1, 60.0, math.inf, 900.0), hydronics.NonFiniteValueError),
        ((0.1, 60.0, 50.0, math.nan), hydronics.NonFiniteValueError),
    ],
)
@pytest.mark.parametrize("function", [hydronics.circuit_heat_j, hydronics.kilowatt_hours])
def test_circuit_heat_refusals(function: Callable[..., float], arguments: Tuple[float, ...], error: type) -> None:
    """Circuit heat refuses a negative flow, a step that is not positive and any non-finite value."""
    with pytest.raises(error):
        function(*arguments)


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ((50.0, 40.0, 40.0, 0.1), hydronics.ValveTemperatureOrderError),
        ((50.0, 10.0, 40.0, 0.1), hydronics.ValveTemperatureOrderError),
        ((50.0, 40.0, 10.0, -0.1), hydronics.NegativeDemandError),
        ((math.nan, 40.0, 10.0, 0.1), hydronics.NonFiniteValueError),
        ((50.0, math.nan, 10.0, 0.1), hydronics.NonFiniteValueError),
        ((50.0, 40.0, math.nan, 0.1), hydronics.NonFiniteValueError),
        ((50.0, 40.0, 10.0, math.inf), hydronics.NonFiniteValueError),
    ],
)
def test_valve_refusals(arguments: Tuple[float, float, float, float], error: type) -> None:
    """The valve refuses ``T_warm <= T_cold``, a negative demand and any non-finite value."""
    with pytest.raises(error):
        hydronics.mixing_valve_draw(*arguments)


@pytest.mark.parametrize(
    ("published", "computed", "error"),
    [
        ([], [], hydronics.AccelerationHistoryError),
        ([1.0, 2.0], [2.0], hydronics.AccelerationHistoryError),
        ([1.0, math.nan], [2.0, 3.0], hydronics.NonFiniteValueError),
        ([1.0, 2.0], [2.0, math.inf], hydronics.NonFiniteValueError),
    ],
)
def test_acceleration_refusals(published: Sequence[float], computed: Sequence[float], error: type) -> None:
    """An empty or unpaired history, or a non-finite iterate, is refused."""
    with pytest.raises(error):
        hydronics.accelerated_node_mean(published, computed)


@pytest.mark.parametrize("a", [-1e-12, -1.0, math.nan, math.inf])
def test_relaxation_factor_refusals(a: float) -> None:
    """A negative or non-finite exponent is refused."""
    with pytest.raises(hydronics.HydronicsError):
        hydronics.relaxation_mean_factor(a)
