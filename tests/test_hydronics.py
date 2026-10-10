"""Property tests of the hydronics library: circuit heat, supply limits, the mixed node, the tap valve and the solve.

``hypothesis`` is not a declared test dependency, so the properties are checked over seeded random sweeps: every
sweep draws from its own :class:`random.Random` with a fixed seed, so a failure reproduces exactly.

The checks are platform independent. A reference the tests compute in floats sums with :func:`math.fsum`, exactly
rounded on every Python, never with :func:`sum`, whose float rounding changed in Python 3.12. A property the
mathematics states exactly (a bound, a sign) is checked up to ``ULPS_OF_FLOAT_NOISE`` ulps of the temperatures'
scale, the rounding a handful of float operations can leave: a value that close to a bound counts as on it.
"""

import dataclasses
import math
import random
from decimal import Decimal, localcontext
from typing import Callable, Iterator, List, Tuple

import numpy as np
import pytest

from hisim import hydronics
from hisim import loadtypes as lt
from hisim.components import simple_water_storage
from hisim.components.configuration import PhysicsConfig
from hisim.hydronics import Inflow, MixedNode, NodeStep, Water
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base


class Sweep:

    """The settings of the seeded random sweeps and of the float comparisons, a namespace."""

    #: The specific heat of water the library computes with, in J/(kg K).
    C_WATER = Water.SPECIFIC_HEAT_J_PER_KG_K
    #: The step lengths every sweep draws cases for, in s.
    STEP_LENGTHS_S = (60.0, 900.0, 3600.0)
    #: The cases drawn per step length.
    CASES_PER_STEP_LENGTH = 2000
    #: How many ulps of a temperature scale count as float noise: the closed form takes a few rounded operations
    #: from its arguments to ``T_mean`` and ``T_end``, each off by at most half an ulp of a value bounded by the scale.
    ULPS_OF_FLOAT_NOISE = 4


def float_noise(scale: float) -> float:
    """Return ``ULPS_OF_FLOAT_NOISE`` ulps of ``scale``: the distance below which two temperatures are one value."""
    return Sweep.ULPS_OF_FLOAT_NOISE * math.ulp(abs(scale))


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
        self.heat_capacity_j_per_k = rng.uniform(5.0, 2000.0) * Sweep.C_WATER

    def step(self) -> NodeStep:
        """The library's step of this case."""
        return MixedNode.step(
            t0_c=self.t0_c,
            inflows=self.inflows,
            ua_w_per_k=self.ua_w_per_k,
            t_amb_c=self.t_amb_c,
            dt_s=self.dt_s,
            heat_capacity_j_per_k=self.heat_capacity_j_per_k,
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
        conductances = [inflow.mass_flow_kg_per_s * Sweep.C_WATER for inflow in self.inflows]
        conductance = math.fsum(conductances + [self.ua_w_per_k])
        if conductance == 0.0:
            return self.t0_c
        weighted = [g * inflow.temperature_c for g, inflow in zip(conductances, self.inflows)]
        return math.fsum(weighted + [self.ua_w_per_k * self.t_amb_c]) / conductance


def node_cases(seed: int) -> List[NodeCase]:
    """``Sweep.CASES_PER_STEP_LENGTH`` cases at each of 60, 900 and 3600 s, drawn from ``seed``."""
    rng = random.Random(seed)
    return [NodeCase(rng, dt_s) for dt_s in Sweep.STEP_LENGTHS_S for _ in range(Sweep.CASES_PER_STEP_LENGTH)]


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
    """A ``PhysicsConfig`` water entry with its own specific heat would let two components heat water differently."""
    water = PhysicsConfig.get_properties_for_energy_carrier(lt.LoadTypes.WATER)
    assert water.specific_heat_capacity_in_joule_per_kg_per_kelvin == Water.SPECIFIC_HEAT_J_PER_KG_K


def test_water_density_is_the_storages_value_at_40_degrees() -> None:
    """A storage or ``PhysicsConfig`` with its own water density would give two masses for one volume.

    The library's density is 0.992 kg/l, water at 40 °C; the space-heating buffer, the hot-water tank and
    ``PhysicsConfig`` (which the heat distribution system turns its pipe volume into a mass with) read it.
    """
    assert Water.DENSITY_KG_PER_M3 == 992.0
    assert Water.DENSITY_KG_PER_LITER == 0.992
    buffer = simple_water_storage.SimpleHotWaterStorage(
        my_simulation_parameters=SimulationParameters.one_day_only(2021, 900),
        config=dataclasses.replace(
            simple_water_storage.SimpleHotWaterStorageConfig.preset_buffer("Buffer"),
            volume_heating_water_storage_in_liter=500.0,
        ),
    )
    assert buffer.density_water_at_40_degree_celsius_in_kg_per_liter == Water.DENSITY_KG_PER_LITER
    water = PhysicsConfig.get_properties_for_energy_carrier(lt.LoadTypes.WATER)
    assert water.density_in_kg_per_m3 == Water.DENSITY_KG_PER_M3


# --- the small-a threshold -----------------------------------------------------------------------------------


def threshold_probe_points() -> List[float]:
    """Log-spaced ``a`` from 1e-15 to 100, and the floats around the threshold on both sides."""
    points = [10.0 ** (exponent / 4.0) for exponent in range(-60, 9)]
    below = above = MixedNode.SMALL_A_THRESHOLD
    for _ in range(4):
        below = math.nextafter(below, 0.0)
        above = math.nextafter(above, math.inf)
        points += [below, above]
    points += [MixedNode.SMALL_A_THRESHOLD, MixedNode.SMALL_A_THRESHOLD * 0.5, MixedNode.SMALL_A_THRESHOLD * 2.0, 5e-324, 1e-300]
    return sorted(points)


@pytest.mark.parametrize("a", threshold_probe_points())
def test_relaxation_factor_matches_a_fifty_digit_reference(a: float) -> None:
    """``phi(a)`` is within two ulps of ``(1 - e^(-a))/a`` evaluated in 50-digit decimal arithmetic.

    This is the proof of ``MixedNode.SMALL_A_THRESHOLD``: below it the Taylor polynomial (remainder ``a^4/120``), above it
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
    assert MixedNode.SMALL_A_THRESHOLD**4 / 120.0 < 2.0**-52 / 40.0


# --- the mixed node: closed form -----------------------------------------------------------------------------


def reference_step(case: NodeCase) -> Tuple[Decimal, Decimal]:
    """Return ``(T_end, T_mean)`` of the node step in 50-digit decimal arithmetic, from the closed-form formulas."""
    with localcontext() as context:
        context.prec = 50
        c = Decimal(Sweep.C_WATER)
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
    """A closed form evaluated with a cancellation or a wrong factor would miss the 50-digit reference by more than 1e-12 K."""
    for case in node_cases(seed=11)[::10]:
        result = case.step()
        t_end, t_mean = reference_step(case)
        assert abs(Decimal(result.t_end_c) - t_end) < Decimal("1e-12")
        assert abs(Decimal(result.t_mean_c) - t_mean) < Decimal("1e-12")


def rk4_reference(case: NodeCase, substeps: int) -> Tuple[float, float]:
    """``(T_end, T_mean)`` by integrating the node's ODE with classical Runge-Kutta, independent of the closed form."""
    conductances = [(i.mass_flow_kg_per_s * Sweep.C_WATER, i.temperature_c) for i in case.inflows]

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


# --- the mixed node: properties -------------------------------------------------------------------------------


def test_exact_closure() -> None:
    """A node whose inflow heats and loss did not add up to ``C (T_end - T0)`` would create or lose energy."""
    worst = 0.0
    for case in node_cases(seed=1):
        result = case.step()
        stored = case.heat_capacity_j_per_k * (result.t_end_c - case.t0_c)
        recomputed_inflows = [
            i.mass_flow_kg_per_s * Sweep.C_WATER * (i.temperature_c - result.t_mean_c) * case.dt_s for i in case.inflows
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
                t0_c=temperature,
                inflows=case.inflows,
                ua_w_per_k=case.ua_w_per_k,
                t_amb_c=case.t_amb_c,
                dt_s=case.dt_s,
                heat_capacity_j_per_k=case.heat_capacity_j_per_k,
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
        result = MixedNode.step(
            t0_c=t0,
            inflows=inflows,
            ua_w_per_k=0.0,
            t_amb_c=rng.uniform(-10.0, 30.0),
            dt_s=rng.choice(Sweep.STEP_LENGTHS_S),
            heat_capacity_j_per_k=1e6,
        )
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
            t0_c=case.t0_c,
            inflows=case.inflows,
            ua_w_per_k=case.ua_w_per_k,
            t_amb_c=case.t_amb_c,
            dt_s=half,
            heat_capacity_j_per_k=case.heat_capacity_j_per_k,
        )
        second = MixedNode.step(
            t0_c=first.t_end_c,
            inflows=case.inflows,
            ua_w_per_k=case.ua_w_per_k,
            t_amb_c=case.t_amb_c,
            dt_s=half,
            heat_capacity_j_per_k=case.heat_capacity_j_per_k,
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
    """A cooling inflow booked with a positive heat would make a cooling circuit heat its node."""
    result = MixedNode.step(
        t0_c=45.0,
        inflows=[Inflow(0.3, 7.0)],
        ua_w_per_k=0.0,
        t_amb_c=20.0,
        dt_s=900.0,
        heat_capacity_j_per_k=300.0 * Sweep.C_WATER,
    )
    assert result.t_end_c < result.t_mean_c < 45.0
    assert result.heat_in_j_per_inflow[0] < 0.0


# --- circuit heat ---------------------------------------------------------------------------------------------


class Circuit:

    """Keyword arguments of one valid circuit, the base the circuit tests vary, a namespace."""

    #: 0.1 kg/s from 50 to 60 °C for 900 s.
    GOOD = {"mass_flow_kg_per_s": 0.1, "t_supply_c": 60.0, "t_return_c": 50.0, "dt_s": 900.0}


def test_circuit_heat_is_m_c_delta_t_dt() -> None:
    """0.1 kg/s, 10 K, one hour is 15.048 MJ or 4.18 kWh; another specific heat or unit factor would fail."""
    hour = {**Circuit.GOOD, "dt_s": 3600.0}
    assert hydronics.circuit_heat_j(**hour) == pytest.approx(15.048e6, rel=1e-15)
    assert hydronics.circuit_heat_kwh(**hour) == pytest.approx(4.18, rel=1e-15)


@pytest.mark.parametrize(
    ("mass_flow_kg_per_s", "t_supply_c", "t_return_c", "dt_s"),
    [(0.4, 35.0, 30.04, 900.0), (0.333, 7.0, 12.3, 60.0), (0.0, 55.0, 45.0, 3600.0), (1.7, 40.1, 40.1, 1.0)],
)
def test_circuit_heat_is_the_circuit_power_times_the_step_to_the_last_bit(
    mass_flow_kg_per_s: float, t_supply_c: float, t_return_c: float, dt_s: float
) -> None:
    """A booked power and a derived heat evaluated in another order would differ in the last bits and unbalance."""
    power_w = hydronics.circuit_power_w(
        mass_flow_kg_per_s=mass_flow_kg_per_s, t_supply_c=t_supply_c, t_return_c=t_return_c
    )
    assert power_w == mass_flow_kg_per_s * Sweep.C_WATER * (t_supply_c - t_return_c)
    heat_j = hydronics.circuit_heat_j(
        mass_flow_kg_per_s=mass_flow_kg_per_s, t_supply_c=t_supply_c, t_return_c=t_return_c, dt_s=dt_s
    )
    assert heat_j == power_w * dt_s


def test_circuit_power_is_m_c_delta_t_and_refuses_what_circuit_heat_refuses() -> None:
    """0.4 kg/s over 4.96 K carries 8293.12 W; a power that accepted a negative flow or a NaN would hide an error."""
    assert hydronics.circuit_power_w(mass_flow_kg_per_s=0.4, t_supply_c=35.0, t_return_c=30.04) == pytest.approx(
        8293.12, rel=1e-12
    )
    with pytest.raises(hydronics.NegativeMassFlowError):
        hydronics.circuit_power_w(mass_flow_kg_per_s=-0.1, t_supply_c=35.0, t_return_c=30.0)
    with pytest.raises(hydronics.NonFiniteValueError):
        hydronics.circuit_power_w(mass_flow_kg_per_s=0.1, t_supply_c=math.nan, t_return_c=30.0)
    with pytest.raises(hydronics.NonFiniteValueError, match="circuit power"):
        hydronics.circuit_power_w(mass_flow_kg_per_s=1e306, t_supply_c=1e3, t_return_c=-1e3)


def test_circuit_heat_kwh_sign_rule() -> None:
    """Heating is positive, cooling negative, no flow or no lift zero; a mirrored or clipped heat would fail."""

    def heat_kwh(**changes: float) -> float:
        """Return the circuit heat in kWh of the good circuit with these changes."""
        return hydronics.circuit_heat_kwh(**dict(Circuit.GOOD, **changes))

    assert heat_kwh() > 0.0
    assert heat_kwh(t_supply_c=7.0, t_return_c=12.0) < 0.0
    assert heat_kwh(t_supply_c=7.0, t_return_c=12.0) == -heat_kwh(t_supply_c=12.0, t_return_c=7.0)
    assert heat_kwh(mass_flow_kg_per_s=0.0) == 0.0
    assert heat_kwh(t_supply_c=50.0) == 0.0


def test_node_heat_equals_circuit_heat_with_the_step_mean_as_return() -> None:
    """A node that booked its inflow's heat against another temperature than its step mean would unbalance it."""
    result = MixedNode.step(
        t0_c=40.0,
        inflows=[Inflow(0.2, 60.0)],
        ua_w_per_k=2.0,
        t_amb_c=20.0,
        dt_s=900.0,
        heat_capacity_j_per_k=300.0 * Sweep.C_WATER,
    )
    circuit = hydronics.circuit_heat_j(mass_flow_kg_per_s=0.2, t_supply_c=60.0, t_return_c=result.t_mean_c, dt_s=900.0)
    assert result.heat_in_j_per_inflow[0] == pytest.approx(circuit, rel=1e-15)


def test_an_idle_circuit_moves_no_water_and_supplies_its_return() -> None:
    """An idle circuit with a flow or a supply off its return would carry heat nobody booked."""
    idle = hydronics.CircuitStep.idle(t_return_c=47.5)
    assert idle == hydronics.CircuitStep(mass_flow_kg_per_s=0.0, t_supply_c=47.5, power_w=0.0)


def test_a_circuit_at_part_load_carries_the_averaged_flow_at_its_full_load_supply() -> None:
    """The documented example, and a ratio of 1 or outside [0, 1].

    A part-loaded step that kept the full flow, or booked the full-load heat for the averaged flow, would book heat its
    water does not carry; a ratio outside [0, 1] is a controller error that must stop the step.
    """
    full_load = hydronics.CircuitStep(mass_flow_kg_per_s=0.0144, t_supply_c=75.0, power_w=1504.8)
    part = full_load.at_part_load(part_load_ratio=0.5, t_return_c=50.0)
    assert part.mass_flow_kg_per_s == pytest.approx(0.0072, rel=1e-12)
    assert part.t_supply_c == 75.0
    assert part.power_w == pytest.approx(752.4, rel=1e-12)
    assert full_load.at_part_load(part_load_ratio=1.0, t_return_c=50.0) is full_load
    for ratio in (-0.1, 1.5):
        with pytest.raises(ValueError, match="part-load ratio"):
            full_load.at_part_load(part_load_ratio=ratio, t_return_c=50.0)


# --- supply limits --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("unthrottled_supply_c", "return_c", "supply_limit_c", "expected_c"),
    [
        (70.0, 60.0, 80.0, 70.0),  # below the limit: the generator's own supply
        (80.0, 70.0, 80.0, 80.0),  # exactly at the limit: not throttled
        (82.0, 72.0, 80.0, 80.0),  # above it: the limit
        (95.0, 85.0, 80.0, 85.0),  # the return already above the limit: the return, never colder
    ],
)
def test_the_supply_is_throttled_at_its_limit_and_never_below_the_return(
    unthrottled_supply_c: float, return_c: float, supply_limit_c: float, expected_c: float
) -> None:
    """A throttle that passed the limit or cooled the circuit below its return would book wrong heat."""
    assert (
        hydronics.throttled_supply_temperature_c(
            unthrottled_supply_c=unthrottled_supply_c, return_c=return_c, supply_limit_c=supply_limit_c
        )
        == expected_c
    )


@pytest.mark.parametrize(
    ("unthrottled_supply_c", "return_c", "maximal_supply_c", "set_supply_c", "expected_c"),
    [
        (65.0, 55.0, 80.0, 70.0, 65.0),  # below both: the generator's own supply
        (75.0, 65.0, 80.0, 70.0, 70.0),  # the set temperature caps
        (85.0, 75.0, 80.0, 80.0, 80.0),  # the set temperature at the maximum: the maximum caps
        (78.0, 72.0, 80.0, 70.0, 72.0),  # the return above the set temperature: the return
        (70.0, 60.0, 80.0, 70.0, 70.0),  # exactly at the set temperature: not throttled
    ],
)
def test_the_hot_water_supply_is_capped_at_the_lower_of_maximum_and_set_temperature(
    unthrottled_supply_c: float, return_c: float, maximal_supply_c: float, set_supply_c: float, expected_c: float
) -> None:
    """A hot-water cap that ignored the set temperature would charge a whole step past the controller's target."""
    assert (
        hydronics.capped_hot_water_supply_temperature_c(
            unthrottled_supply_c=unthrottled_supply_c,
            return_c=return_c,
            maximal_supply_c=maximal_supply_c,
            set_supply_c=set_supply_c,
        )
        == expected_c
    )


def test_a_set_temperature_above_the_maximum_supply_is_refused() -> None:
    """A charge towards a set temperature the capped supply cannot reach would never end; the pair is refused."""
    with pytest.raises(hydronics.SetTemperatureAboveMaximumError, match="never ends"):
        hydronics.capped_hot_water_supply_temperature_c(
            unthrottled_supply_c=75.0, return_c=65.0, maximal_supply_c=75.0, set_supply_c=75.5
        )


@pytest.mark.parametrize("field", ["unthrottled_supply_c", "return_c", "maximal_supply_c", "set_supply_c"])
def test_the_hot_water_cap_refuses_a_value_that_is_not_finite(field: str) -> None:
    """A NaN set temperature would make every comparison false and pass the generator's supply uncapped."""
    arguments = {"unthrottled_supply_c": 75.0, "return_c": 65.0, "maximal_supply_c": 80.0, "set_supply_c": 70.0}
    arguments[field] = math.nan
    with pytest.raises(hydronics.NonFiniteValueError, match=field):
        hydronics.capped_hot_water_supply_temperature_c(**arguments)


# --- the tap valve --------------------------------------------------------------------------------------------


class Valve:

    """The tap and mains temperatures of the valve tests, in °C, a namespace."""

    #: The temperature the household asks for, in °C.
    T_WARM_C = 40.0
    #: The mains water temperature, in °C.
    T_COLD_C = 10.0

    @staticmethod
    def draw(t_tank_c: float, demand_kg_per_s: float) -> hydronics.ValveDraw:
        """Return the valve's draw from a tank at ``t_tank_c`` for this demand, between the test's temperatures."""
        return hydronics.mixing_valve_draw(
            t_tank_c=t_tank_c, t_warm_c=Valve.T_WARM_C, t_cold_c=Valve.T_COLD_C, demand_kg_per_s=demand_kg_per_s
        )

    @staticmethod
    def demand_heat_w(demand_kg_per_s: float) -> float:
        """Return the heat flow the household asks for, ``m_d c (T_warm - T_cold)``, in W."""
        return hydronics.hot_water_demand_power_w(
            demand_kg_per_s=demand_kg_per_s, t_warm_c=Valve.T_WARM_C, t_cold_c=Valve.T_COLD_C
        )


def test_the_demand_heat_is_m_c_times_the_span() -> None:
    """0.05 kg/s from 10 to 40 °C ask for 6270 W; another span or specific heat would mis-book the unmet heat."""
    assert Valve.demand_heat_w(0.05) == pytest.approx(0.05 * 4180.0 * 30.0, rel=1e-15)


def test_valve_draws_the_demanded_heat_above_t_warm() -> None:
    """A hot tank that let out more or less than the demand's heat would over- or under-deliver hot water."""
    rng = random.Random(6)
    for _ in range(2000):
        tank, demand = rng.uniform(Valve.T_WARM_C + 1e-9, 95.0), rng.uniform(0.0, 0.5)
        draw = Valve.draw(tank, demand)
        assert draw.unmet_fraction == 0.0
        assert 0.0 <= draw.hot_water_kg_per_s <= demand
        assert draw.hot_water_kg_per_s * Sweep.C_WATER * (tank - Valve.T_COLD_C) == pytest.approx(
            Valve.demand_heat_w(demand), rel=1e-14, abs=1e-12
        )


def test_valve_is_continuous_at_t_warm() -> None:
    """A jump at the tap temperature would make the tank's fixed point flip between two draws."""
    demand = 0.12
    assert Valve.draw(Valve.T_WARM_C, demand) == hydronics.ValveDraw(hot_water_kg_per_s=demand, unmet_fraction=0.0)
    above = Valve.draw(math.nextafter(Valve.T_WARM_C, math.inf), demand)
    below = Valve.draw(math.nextafter(Valve.T_WARM_C, 0.0), demand)
    assert above.hot_water_kg_per_s == pytest.approx(demand, rel=1e-14)
    assert above.unmet_fraction == 0.0
    assert below.hot_water_kg_per_s == demand
    assert below.unmet_fraction == pytest.approx(0.0, abs=1e-14)


def test_valve_between_cold_and_warm_passes_everything_and_reports_the_shortfall() -> None:
    """A lukewarm tank whose delivered and unmet heat did not add up to the demand would lose or invent heat."""
    rng = random.Random(7)
    for _ in range(2000):
        tank, demand = rng.uniform(math.nextafter(Valve.T_COLD_C, math.inf), Valve.T_WARM_C), rng.uniform(0.0, 0.5)
        draw = Valve.draw(tank, demand)
        assert draw.hot_water_kg_per_s == demand
        assert 0.0 <= draw.unmet_fraction < 1.0
        delivered = draw.hot_water_kg_per_s * Sweep.C_WATER * (tank - Valve.T_COLD_C)
        assert delivered == pytest.approx(Valve.demand_heat_w(demand) * (1.0 - draw.unmet_fraction), rel=1e-12, abs=1e-9)


def test_valve_at_or_below_t_cold_draws_nothing() -> None:
    """A tank at or below the mains temperature that let water out would book heat it does not have."""
    nothing = hydronics.ValveDraw(hot_water_kg_per_s=0.0, unmet_fraction=1.0)
    assert Valve.draw(Valve.T_COLD_C, 0.1) == nothing
    assert Valve.draw(Valve.T_COLD_C - 5.0, 0.1) == nothing


def test_valve_unmet_fraction_is_bounded_and_monotone() -> None:
    """An unmet share outside [0, 1], or rising as the tank warms, would book a negative or excessive shortfall."""
    temperatures = [Valve.T_COLD_C - 5.0 + 0.01 * j for j in range(6000)]
    unmet = [Valve.draw(t, 0.1).unmet_fraction for t in temperatures]
    assert all(0.0 <= u <= 1.0 for u in unmet)
    assert all(later <= earlier for earlier, later in zip(unmet, unmet[1:]))


# --- the range of a node and the bracketed solve ----------------------------------------------------------------


def test_a_node_s_range_spans_its_start_its_flowing_inflows_and_its_fixed_temperatures() -> None:
    """An inflow without flow brings no water; a range that counted it would widen the tap solve's bracket."""
    mixed_range = hydronics.TemperatureRange.of_node(
        t0_c=50.0, inflows=[Inflow(0.2, 70.0), Inflow(0.0, 95.0)], fixed_temperatures_c=(10.0, 20.0)
    )
    assert mixed_range == hydronics.TemperatureRange(low_c=10.0, high_c=70.0)
    assert mixed_range.clamped_c(75.0) == 70.0
    assert mixed_range.clamped_c(5.0) == 10.0
    assert mixed_range.clamped_c(42.0) == 42.0


def test_the_bracketed_solve_finds_the_fixed_point_of_a_contraction() -> None:
    """A solve that stopped early or left the bracket would book the tap's heat at a wrong step mean."""

    def image_c(assumed_c: float) -> float:
        """Return a contraction's image of ``assumed_c``, with its fixed point at 40 °C."""
        return 0.5 * assumed_c + 20.0

    fixed_point_c = hydronics.solve_bracketed_fixed_point(
        evaluate=lambda assumed_c: assumed_c,
        image_c=image_c,
        low_c=10.0,
        high_c=70.0,
        tolerance_k=1e-12,
        maximum_evaluations=200,
    )
    assert fixed_point_c == pytest.approx(40.0, abs=1e-10)


def test_the_bracketed_solve_fails_instead_of_returning_a_guess() -> None:
    """A solve out of evaluations must stop the run; a returned guess would book heat from an unconverged mean."""
    with pytest.raises(hydronics.FixedPointNotFoundError, match="did not converge within 1 evaluations"):
        hydronics.solve_bracketed_fixed_point(
            evaluate=lambda assumed_c: assumed_c,
            image_c=lambda assumed_c: 60.0 - 0.5 * assumed_c - 0.002 * assumed_c**2,
            low_c=10.0,
            high_c=60.0,
            tolerance_k=1e-15,
            maximum_evaluations=1,
        )


# --- refusals ------------------------------------------------------------------------------------------------


class Refusals:

    """The arguments of one valid node step, the base the refusal tests vary, a namespace."""

    #: A 300 kg node at 40 °C charged at 0.2 kg/s and 60 °C for 900 s, losing 2 W/K to 20 °C.
    GOOD_STEP = {
        "t0_c": 40.0,
        "inflows": (Inflow(0.2, 60.0),),
        "ua_w_per_k": 2.0,
        "t_amb_c": 20.0,
        "dt_s": 900.0,
        "heat_capacity_j_per_k": 300.0 * Sweep.C_WATER,
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
    """An invalid argument that a node step accepted would integrate nonsense; each is refused by name."""
    arguments = dict(Refusals.GOOD_STEP, **{field: value})
    with pytest.raises(error):
        MixedNode.step(**arguments)  # type: ignore[arg-type]  # the dict mixes the argument types on purpose


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
    """An overflow returned as inf or NaN would poison every later step; it is refused naming the quantity."""
    with pytest.raises(hydronics.NonFiniteValueError, match=quantity):
        MixedNode.step(**dict(Refusals.GOOD_STEP, **changes))  # type: ignore[arg-type]  # mixed argument types


@pytest.mark.parametrize(
    ("changes", "quantity"),
    [
        ({"mass_flow_kg_per_s": 1e300, "t_supply_c": 100.0, "t_return_c": 0.0, "dt_s": 1e10}, "circuit heat"),
        ({"t_supply_c": 1.5e308, "t_return_c": -1.5e308, "dt_s": 1.0}, "lift"),
    ],
)
@pytest.mark.parametrize("function", [hydronics.circuit_heat_j, hydronics.circuit_heat_kwh])
def test_circuit_heat_refuses_a_result_that_overflows(
    function: Callable[..., float], changes: dict, quantity: str
) -> None:
    """A circuit heat beyond the float range returned as inf would poison the balance; it is refused by name."""
    with pytest.raises(hydronics.NonFiniteValueError, match=quantity):
        function(**dict(Circuit.GOOD, **changes))


@pytest.mark.parametrize(
    ("arguments", "quantity"),
    [
        ({"t_tank_c": 0.0, "t_warm_c": 1.5e308, "t_cold_c": -1.5e308, "demand_kg_per_s": 0.1}, "span"),
        ({"t_tank_c": 1e308, "t_warm_c": -5e307, "t_cold_c": -1e308, "demand_kg_per_s": 0.1}, "lift over the mains"),
    ],
)
def test_valve_refuses_a_result_that_overflows(arguments: dict, quantity: str) -> None:
    """A valve whose temperature differences overflow would answer a rounded-away fraction; it is refused."""
    with pytest.raises(hydronics.NonFiniteValueError, match=quantity):
        hydronics.mixing_valve_draw(**arguments)


def step_with(inflows: object) -> NodeStep:
    """Return the node step of ``Refusals.GOOD_STEP`` with these inflows."""
    return MixedNode.step(**{**Refusals.GOOD_STEP, "inflows": inflows})  # type: ignore[arg-type]  # mixed types


def test_an_inflow_stores_floats() -> None:
    """An inflow that kept a ``Decimal`` or NumPy scalar would let another type into the step arithmetic."""
    inflow = Inflow(Decimal("0.2"), np.float32(60.0))  # type: ignore[arg-type]  # the conversion is tested
    assert isinstance(inflow.mass_flow_kg_per_s, float) and inflow.mass_flow_kg_per_s == 0.2
    assert isinstance(inflow.temperature_c, float) and inflow.temperature_c == 60.0
    assert step_with((inflow,)) == step_with((Inflow(0.2, 60.0),))


def test_a_node_step_reads_its_inflows_once() -> None:
    """A step that read a one-shot iterable twice would see no inflows the second time and book none."""
    inflows = (Inflow(0.2, 60.0), Inflow(0.05, 30.0))

    def one_shot() -> Iterator[Inflow]:
        """Yield the two inflows once."""
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
    """An inflow with a negative or non-finite flow, or a non-finite temperature, would poison the node step."""
    with pytest.raises(error):
        Inflow(mass_flow, temperature)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"mass_flow_kg_per_s": -0.1}, hydronics.NegativeMassFlowError),
        ({"dt_s": 0.0}, hydronics.NonPositiveTimestepError),
        ({"mass_flow_kg_per_s": math.nan}, hydronics.NonFiniteValueError),
        ({"t_supply_c": math.nan}, hydronics.NonFiniteValueError),
        ({"t_return_c": math.inf}, hydronics.NonFiniteValueError),
        ({"dt_s": math.nan}, hydronics.NonFiniteValueError),
    ],
)
@pytest.mark.parametrize("function", [hydronics.circuit_heat_j, hydronics.circuit_heat_kwh])
def test_circuit_heat_refusals(function: Callable[..., float], changes: dict, error: type) -> None:
    """A circuit heat that accepted a negative flow, an empty step or a non-finite value would hide an error."""
    with pytest.raises(error):
        function(**dict(Circuit.GOOD, **changes))


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"t_warm_c": 40.0, "t_cold_c": 40.0}, hydronics.ValveTemperatureOrderError),
        ({"t_warm_c": 10.0, "t_cold_c": 40.0}, hydronics.ValveTemperatureOrderError),
        ({"demand_kg_per_s": -0.1}, hydronics.NegativeDemandError),
        ({"t_tank_c": math.nan}, hydronics.NonFiniteValueError),
        ({"t_warm_c": math.nan}, hydronics.NonFiniteValueError),
        ({"t_cold_c": math.nan}, hydronics.NonFiniteValueError),
        ({"demand_kg_per_s": math.inf}, hydronics.NonFiniteValueError),
    ],
)
def test_valve_refusals(changes: dict, error: type) -> None:
    """A valve that accepted ``T_warm <= T_cold``, a negative demand or a non-finite value would draw nonsense."""
    arguments = dict({"t_tank_c": 50.0, "t_warm_c": 40.0, "t_cold_c": 10.0, "demand_kg_per_s": 0.1}, **changes)
    with pytest.raises(error):
        hydronics.mixing_valve_draw(**arguments)


@pytest.mark.parametrize("a", [-1e-12, -1.0, math.nan, math.inf])
def test_relaxation_factor_refusals(a: float) -> None:
    """A negative or non-finite exponent has no relaxation factor; returning one would hide a broken node."""
    with pytest.raises(hydronics.HydronicsError):
        hydronics.relaxation_mean_factor(a)
