"""Tests of the hot-water tank's tap: the thermostatic mixing valve, the range a tank mixes and the bracketed solve.

These are the pure functions :meth:`SimpleDHWStorage.solve_tank_step` solves the tank's step with. ``hypothesis`` is
not a declared test dependency, so the properties are checked over seeded random sweeps: every sweep draws from its
own :class:`random.Random` with a fixed seed, so a failure reproduces exactly.
"""

import math
import random

import pytest

from hisim import hydronics
from hisim.components.simple_water_storage import (
    FixedPointNotFoundError,
    NegativeDemandError,
    SimpleDHWStorage,
    TemperatureRange,
    ValveDraw,
    ValveTemperatureOrderError,
)
from hisim.hydronics import Inflow, Water

pytestmark = pytest.mark.base


# --- the tap valve --------------------------------------------------------------------------------------------


class Valve:

    """The tap and mains temperatures of the valve tests, in °C, a namespace."""

    #: The temperature the household asks for, in °C.
    T_WARM_C = 40.0
    #: The mains water temperature, in °C.
    T_COLD_C = 10.0

    @staticmethod
    def draw(t_tank_c: float, demand_kg_per_s: float) -> ValveDraw:
        """Return the valve's draw from a tank at ``t_tank_c`` for this demand, between the test's temperatures."""
        # Annotated, since mypy's moderate run sees the storage class's attributes as Any.
        draw: ValveDraw = SimpleDHWStorage.mixing_valve_draw(
            t_tank_c=t_tank_c, t_warm_c=Valve.T_WARM_C, t_cold_c=Valve.T_COLD_C, demand_kg_per_s=demand_kg_per_s
        )
        return draw

    @staticmethod
    def demand_heat_w(demand_kg_per_s: float) -> float:
        """Return the heat flow the household asks for, ``m_d c (T_warm - T_cold)``, in W."""
        return hydronics.circuit_power_w(mass_flow_kg_per_s=demand_kg_per_s, t_supply_c=Valve.T_WARM_C, t_return_c=Valve.T_COLD_C)


def test_valve_draws_the_demanded_heat_above_t_warm() -> None:
    """A hot tank that let out more or less than the demand's heat would over- or under-deliver hot water."""
    rng = random.Random(6)
    for _ in range(2000):
        tank, demand = rng.uniform(Valve.T_WARM_C + 1e-9, 95.0), rng.uniform(0.0, 0.5)
        draw = Valve.draw(tank, demand)
        assert draw.unmet_fraction == 0.0
        assert 0.0 <= draw.hot_water_kg_per_s <= demand
        assert draw.hot_water_kg_per_s * Water.SPECIFIC_HEAT_J_PER_KG_K * (tank - Valve.T_COLD_C) == pytest.approx(
            Valve.demand_heat_w(demand), rel=1e-14, abs=1e-12
        )


def test_valve_is_continuous_at_t_warm() -> None:
    """A jump at the tap temperature would make the tank's fixed point flip between two draws."""
    demand = 0.12
    assert Valve.draw(Valve.T_WARM_C, demand) == ValveDraw(hot_water_kg_per_s=demand, unmet_fraction=0.0)
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
        delivered = draw.hot_water_kg_per_s * Water.SPECIFIC_HEAT_J_PER_KG_K * (tank - Valve.T_COLD_C)
        assert delivered == pytest.approx(Valve.demand_heat_w(demand) * (1.0 - draw.unmet_fraction), rel=1e-12, abs=1e-9)


def test_valve_at_or_below_t_cold_draws_nothing() -> None:
    """A tank at or below the mains temperature that let water out would book heat it does not have."""
    nothing = ValveDraw(hot_water_kg_per_s=0.0, unmet_fraction=1.0)
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
    mixed_range = TemperatureRange.of_node(
        t0_c=50.0, inflows=[Inflow(0.2, 70.0), Inflow(0.0, 95.0)], fixed_temperatures_c=(10.0, 20.0)
    )
    assert mixed_range == TemperatureRange(low_c=10.0, high_c=70.0)


def test_the_bracketed_solve_finds_the_fixed_point_of_a_contraction() -> None:
    """A solve that stopped early or left the bracket would book the tap's heat at a wrong step mean."""

    def image_c(assumed_c: float) -> float:
        """Return a contraction's image of ``assumed_c``, with its fixed point at 40 °C."""
        return 0.5 * assumed_c + 20.0

    fixed_point_c = SimpleDHWStorage.solve_bracketed_fixed_point(
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
    with pytest.raises(FixedPointNotFoundError, match="did not converge within 1 evaluations"):
        SimpleDHWStorage.solve_bracketed_fixed_point(
            evaluate=lambda assumed_c: assumed_c,
            image_c=lambda assumed_c: 60.0 - 0.5 * assumed_c - 0.002 * assumed_c**2,
            low_c=10.0,
            high_c=60.0,
            tolerance_k=1e-15,
            maximum_evaluations=1,
        )


# --- refusals ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"t_warm_c": 40.0, "t_cold_c": 40.0}, ValveTemperatureOrderError),
        ({"t_warm_c": 10.0, "t_cold_c": 40.0}, ValveTemperatureOrderError),
        ({"demand_kg_per_s": -0.1}, NegativeDemandError),
    ],
)
def test_valve_refusals(changes: dict, error: type) -> None:
    """A valve that accepted ``T_warm <= T_cold`` or a negative demand would draw a finite wrong amount."""
    arguments = dict({"t_tank_c": 50.0, "t_warm_c": 40.0, "t_cold_c": 10.0, "demand_kg_per_s": 0.1}, **changes)
    with pytest.raises(error):
        SimpleDHWStorage.mixing_valve_draw(**arguments)


@pytest.mark.parametrize("error", [ValveTemperatureOrderError, NegativeDemandError, FixedPointNotFoundError])
def test_the_tap_s_refusals_are_hydronics_errors(error: type) -> None:
    """A caller that catches ``HydronicsError`` for every refusal of the water arithmetic would miss these otherwise."""
    assert issubclass(error, hydronics.HydronicsError)
