"""The circulation pumps' flow laws, their clamping, their supply law and the settings each refuses when it is built.

:mod:`hisim.subcomponents.pumps` holds one class per flow law, each the pump of the devices that pump their own loop
with that law (``system_docs/component_design_principles.md``, section C), and :class:`PumpedWater`, the outlet law
and the flow bounds they share. Each test states one law and checks it on plain numbers, without building a component.
"""

import dataclasses
import math
from typing import ClassVar, Dict

import pytest

from hisim.subcomponents.base import Subcomponent
from hisim.subcomponents.pumps import (
    ConstantFlowPump,
    ControllerLiftPump,
    FixedLiftPump,
    PumpedWater,
    PumpOperatingPoint,
    TargetSupplyTemperaturePump,
)

pytestmark = pytest.mark.base


class Water:
    """The specific heat the pumps compute with, so each expected value below is written out with it."""

    #: The specific heat of water, in J/(kg K).
    SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN: ClassVar[float] = PumpedWater.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN


@pytest.mark.parametrize(
    ("heat_power_in_watt", "mass_flow_in_kg_per_second", "expected_supply_temperature_in_celsius"),
    [
        (1672.0, 0.02, 40.0 + 1672.0 / (0.02 * Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN)),
        (-836.0, 0.02, 40.0 - 836.0 / (0.02 * Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN)),
        (0.0, 0.02, 40.0),
        (1672.0, 0.0, 40.0),
        (1672.0, -0.01, 40.0),
    ],
    ids=["heat put in", "heat taken out", "no heat", "no flow", "negative flow"],
)
def test_the_supply_is_the_return_plus_the_heat_over_the_flow_and_the_return_without_flow(
    heat_power_in_watt: float, mass_flow_in_kg_per_second: float, expected_supply_temperature_in_celsius: float
) -> None:
    """A supply law that divided by a flow of 0 kg/s would publish an infinite or NaN outlet for a standing pump."""
    supply_temperature_in_celsius = PumpedWater.supply_temperature_in_celsius(
        heat_power_in_watt=heat_power_in_watt,
        return_temperature_in_celsius=40.0,
        mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
    )
    assert supply_temperature_in_celsius == expected_supply_temperature_in_celsius


@pytest.mark.parametrize(
    ("minimal_mass_flow_in_kg_per_second", "maximal_mass_flow_in_kg_per_second"),
    [(0.0, math.inf), (0.0056, 0.0444), (0.05, 0.05)],
    ids=["unbounded", "a solar pump's band", "one flow"],
)
def test_bounds_from_zero_up_to_infinity_with_the_smallest_not_above_the_largest_are_accepted(
    minimal_mass_flow_in_kg_per_second: float, maximal_mass_flow_in_kg_per_second: float
) -> None:
    """A check that refused an unbounded pump or a band of one flow would refuse district heating's or a constant solar pump."""
    PumpedWater.check_mass_flow_bounds(
        minimal_mass_flow_in_kg_per_second=minimal_mass_flow_in_kg_per_second,
        maximal_mass_flow_in_kg_per_second=maximal_mass_flow_in_kg_per_second,
        pump_name="FixedLiftPump",
    )


@pytest.mark.parametrize(
    ("minimal_mass_flow_in_kg_per_second", "maximal_mass_flow_in_kg_per_second"),
    [(-0.1, 0.5), (math.nan, 0.5), (math.inf, math.inf), (0.0, 0.0), (0.0, -0.5), (0.0, math.nan), (0.5, 0.1)],
    ids=["negative smallest", "NaN smallest", "infinite smallest", "largest 0", "negative largest", "NaN largest", "smallest above largest"],
)
def test_bounds_a_pump_cannot_keep_are_refused_with_the_pump_named(minimal_mass_flow_in_kg_per_second: float, maximal_mass_flow_in_kg_per_second: float) -> None:
    """A pump that accepted these would run a negative flow, no water at all, or a flow no bound can hold."""
    with pytest.raises(ValueError, match="FixedLiftPump"):
        PumpedWater.check_mass_flow_bounds(
            minimal_mass_flow_in_kg_per_second=minimal_mass_flow_in_kg_per_second,
            maximal_mass_flow_in_kg_per_second=maximal_mass_flow_in_kg_per_second,
            pump_name="FixedLiftPump",
        )


@pytest.mark.parametrize(
    ("mass_flow_in_kg_per_second", "expected_bound_in_kg_per_second"),
    [(0.002, 0.005), (-0.01, 0.005), (0.08, 0.05), (math.inf, 0.05)],
    ids=["below the smallest flow", "negative", "above the largest flow", "infinite"],
)
def test_a_flow_outside_the_bounds_runs_the_nearer_bound_and_the_supply_follows_the_heat_there(
    mass_flow_in_kg_per_second: float, expected_bound_in_kg_per_second: float
) -> None:
    """A clamp that kept the aimed lift at a bound would carry heat its flow cannot carry; one that hid the clamp would mislead a collector."""
    point = PumpedWater.operating_point_at_nearer_bound(
        mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
        heat_power_in_watt=1000.0,
        return_temperature_in_celsius=40.0,
        minimal_mass_flow_in_kg_per_second=0.005,
        maximal_mass_flow_in_kg_per_second=0.05,
    )
    assert point == PumpOperatingPoint(
        mass_flow_in_kg_per_second=expected_bound_in_kg_per_second,
        supply_temperature_in_celsius=40.0 + 1000.0 / (expected_bound_in_kg_per_second * Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN),
        flow_is_clamped=True,
    )


def test_a_flow_below_a_smallest_flow_of_zero_stands_at_the_return() -> None:
    """At a bound of 0 kg/s the water does not move, so the supply is the return and not a division by zero."""
    point = PumpedWater.operating_point_at_nearer_bound(
        mass_flow_in_kg_per_second=-0.01,
        heat_power_in_watt=-500.0,
        return_temperature_in_celsius=40.0,
        minimal_mass_flow_in_kg_per_second=0.0,
        maximal_mass_flow_in_kg_per_second=math.inf,
    )
    assert point == PumpOperatingPoint(mass_flow_in_kg_per_second=0.0, supply_temperature_in_celsius=40.0, flow_is_clamped=True)


def test_a_constant_flow_pump_runs_its_design_flow_and_its_outlet_follows_the_heat() -> None:
    """A constant pump that changed its flow with the heat would not be the design-flow pump of a distribution system."""
    pump = ConstantFlowPump(design_mass_flow_in_kg_per_second=0.25)
    point = pump.operating_point(heat_power_in_watt=5225.0, return_temperature_in_celsius=30.0)
    assert point == PumpOperatingPoint(
        mass_flow_in_kg_per_second=0.25,
        supply_temperature_in_celsius=30.0 + 5225.0 / (0.25 * Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN),
        flow_is_clamped=False,
    )
    assert point.supply_temperature_in_celsius == pytest.approx(35.0, rel=1e-15)
    assert pump.operating_point(heat_power_in_watt=0.0, return_temperature_in_celsius=30.0) == PumpOperatingPoint(
        mass_flow_in_kg_per_second=0.25, supply_temperature_in_celsius=30.0, flow_is_clamped=False
    )


@pytest.mark.parametrize("design_mass_flow_in_kg_per_second", [0.0, -0.3, math.inf, math.nan], ids=["zero", "negative", "infinite", "NaN"])
def test_a_constant_flow_pump_without_a_finite_design_flow_above_zero_is_refused(design_mass_flow_in_kg_per_second: float) -> None:
    """A constant pump at 0 kg/s would move no water, one at infinity or NaN would publish a meaningless flow."""
    with pytest.raises(ValueError, match="ConstantFlowPump"):
        ConstantFlowPump(design_mass_flow_in_kg_per_second=design_mass_flow_in_kg_per_second)


def test_a_fixed_lift_pump_carries_the_heat_over_its_lift_within_its_bounds() -> None:
    """Within its bounds a temperature-sensor pump runs ``Q / (c dT)`` and the water leaves exactly ``dT`` warmer."""
    pump = FixedLiftPump(target_lift_in_kelvin=20.0, minimal_mass_flow_in_kg_per_second=0.005, maximal_mass_flow_in_kg_per_second=0.05)
    point = pump.operating_point(heat_power_in_watt=1672.0, return_temperature_in_celsius=40.0)
    assert point == PumpOperatingPoint(
        mass_flow_in_kg_per_second=1672.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 20.0), supply_temperature_in_celsius=60.0, flow_is_clamped=False
    )
    assert point.mass_flow_in_kg_per_second == pytest.approx(0.02, rel=1e-15)


@pytest.mark.parametrize(
    ("heat_power_in_watt", "expected_mass_flow_in_kg_per_second"),
    [(200.0, 0.005), (8360.0, 0.05)],
    ids=["below the smallest flow", "above the largest flow"],
)
def test_a_fixed_lift_pump_outside_its_bounds_runs_the_nearer_bound_and_says_so(heat_power_in_watt: float, expected_mass_flow_in_kg_per_second: float) -> None:
    """At a bound the flow is the bound itself, the outlet ``T_ret + Q / (c m)``, and the clamp is reported.

    A pump that kept the target lift at a bound would carry heat its flow cannot carry; one that hid the clamp would
    leave a collector, whose heat depends on its outlet, at a heat it no longer gives.
    """
    pump = FixedLiftPump(target_lift_in_kelvin=20.0, minimal_mass_flow_in_kg_per_second=0.005, maximal_mass_flow_in_kg_per_second=0.05)
    point = pump.operating_point(heat_power_in_watt=heat_power_in_watt, return_temperature_in_celsius=40.0)
    assert point.mass_flow_in_kg_per_second == expected_mass_flow_in_kg_per_second
    assert point.supply_temperature_in_celsius == 40.0 + heat_power_in_watt / (expected_mass_flow_in_kg_per_second * Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN)
    assert point.flow_is_clamped


def test_a_fixed_lift_pump_whose_flow_is_exactly_a_bound_is_not_clamped() -> None:
    """A flow at a bound is within the bounds; a pump that reported it clamped would make a collector evaluate its heat twice."""
    flow_in_kg_per_second = 1672.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 20.0)
    at_largest = FixedLiftPump(target_lift_in_kelvin=20.0, maximal_mass_flow_in_kg_per_second=flow_in_kg_per_second)
    at_smallest = FixedLiftPump(target_lift_in_kelvin=20.0, minimal_mass_flow_in_kg_per_second=flow_in_kg_per_second)
    expected = PumpOperatingPoint(mass_flow_in_kg_per_second=flow_in_kg_per_second, supply_temperature_in_celsius=60.0, flow_is_clamped=False)
    assert at_largest.operating_point(heat_power_in_watt=1672.0, return_temperature_in_celsius=40.0) == expected
    assert at_smallest.operating_point(heat_power_in_watt=1672.0, return_temperature_in_celsius=40.0) == expected


def test_a_fixed_lift_pump_without_bounds_never_clamps() -> None:
    """A pump built without bounds runs whatever flow the heat asks, as a district-heating hot-water pump does."""
    pump = FixedLiftPump(target_lift_in_kelvin=100.0)
    point = pump.operating_point(heat_power_in_watt=15000.0, return_temperature_in_celsius=50.0)
    assert point == PumpOperatingPoint(
        mass_flow_in_kg_per_second=15000.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 100.0), supply_temperature_in_celsius=150.0, flow_is_clamped=False
    )


def test_a_fixed_lift_pump_given_heat_taken_out_stands_at_its_smallest_flow_of_zero() -> None:
    """A negative heat asks for a negative flow; the pump runs its smallest flow, 0 kg/s, and the water stays put."""
    pump = FixedLiftPump(target_lift_in_kelvin=5.0)
    assert pump.operating_point(heat_power_in_watt=-500.0, return_temperature_in_celsius=40.0) == PumpOperatingPoint(
        mass_flow_in_kg_per_second=0.0, supply_temperature_in_celsius=40.0, flow_is_clamped=True
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"target_lift_in_kelvin": 0.0},
        {"target_lift_in_kelvin": -5.0},
        {"target_lift_in_kelvin": math.nan},
        {"target_lift_in_kelvin": math.inf},
        {"target_lift_in_kelvin": 5.0, "minimal_mass_flow_in_kg_per_second": -0.1},
        {"target_lift_in_kelvin": 5.0, "maximal_mass_flow_in_kg_per_second": 0.0},
        {"target_lift_in_kelvin": 5.0, "minimal_mass_flow_in_kg_per_second": 0.5, "maximal_mass_flow_in_kg_per_second": 0.1},
    ],
    ids=["lift 0", "negative lift", "NaN lift", "infinite lift", "negative smallest flow", "largest flow 0", "smallest above largest"],
)
def test_a_fixed_lift_pump_with_a_lift_or_bounds_it_cannot_run_is_refused_when_built(fields: Dict[str, float]) -> None:
    """A pump that accepted these would divide by a lift of zero, hold no lift, or run no water."""
    with pytest.raises(ValueError, match="FixedLiftPump"):
        FixedLiftPump(**fields)


def test_a_controller_lift_pump_carries_the_heat_over_the_lift_asked_for() -> None:
    """Unclamped, a controller-lift pump runs ``Q / (c lift)`` and supplies the return plus the lift asked for."""
    pump = ControllerLiftPump()
    point = pump.operating_point(heat_power_in_watt=1500.0, return_temperature_in_celsius=50.0, lift_to_supply_set_temperature_in_kelvin=25.0)
    assert point == PumpOperatingPoint(
        mass_flow_in_kg_per_second=1500.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 25.0), supply_temperature_in_celsius=75.0, flow_is_clamped=False
    )


def test_a_controller_lift_pump_raises_a_small_lift_to_the_rise_at_its_largest_flow() -> None:
    """A lift close to 0 K would ask for an unbounded flow; the pump runs its largest and the supply ends higher.

    The pump clamps the lift, the variable this law holds: the rise is ``Q / (c m_max)``, the flow the heat over that
    rise, which is the largest flow to within rounding. This is the boiler's arithmetic, so its results stay bit for
    bit what they were.
    """
    largest_in_kg_per_second = 10450.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 5.0)
    pump = ControllerLiftPump(maximal_mass_flow_in_kg_per_second=largest_in_kg_per_second)
    point = pump.operating_point(heat_power_in_watt=10450.0, return_temperature_in_celsius=40.0, lift_to_supply_set_temperature_in_kelvin=0.01)
    rise_in_kelvin = 10450.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * largest_in_kg_per_second)
    assert point.supply_temperature_in_celsius == 40.0 + rise_in_kelvin
    assert point.mass_flow_in_kg_per_second == 10450.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * rise_in_kelvin)
    assert point.mass_flow_in_kg_per_second == pytest.approx(largest_in_kg_per_second, rel=1e-15)
    assert point.flow_is_clamped
    unclamped = pump.operating_point(heat_power_in_watt=10450.0, return_temperature_in_celsius=40.0, lift_to_supply_set_temperature_in_kelvin=8.0)
    assert unclamped == PumpOperatingPoint(
        mass_flow_in_kg_per_second=10450.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 8.0), supply_temperature_in_celsius=48.0, flow_is_clamped=False
    )


def test_a_controller_lift_pump_lowers_a_large_lift_to_the_rise_at_its_smallest_flow() -> None:
    """A lift so large that the flow would fall below the smallest flow is lowered to the rise that flow carries."""
    pump = ControllerLiftPump(minimal_mass_flow_in_kg_per_second=0.1)
    point = pump.operating_point(heat_power_in_watt=2090.0, return_temperature_in_celsius=40.0, lift_to_supply_set_temperature_in_kelvin=30.0)
    assert point.supply_temperature_in_celsius == pytest.approx(45.0, rel=1e-15)
    assert point.mass_flow_in_kg_per_second == pytest.approx(0.1, rel=1e-15)
    assert point.flow_is_clamped


@pytest.mark.parametrize(
    "fields",
    [
        {"maximal_mass_flow_in_kg_per_second": math.nan},
        {"maximal_mass_flow_in_kg_per_second": 0.0},
        {"minimal_mass_flow_in_kg_per_second": -0.1},
        {"minimal_mass_flow_in_kg_per_second": 0.5, "maximal_mass_flow_in_kg_per_second": 0.1},
    ],
    ids=["NaN largest flow", "largest flow 0", "negative smallest flow", "smallest above largest"],
)
def test_a_controller_lift_pump_with_bounds_it_cannot_keep_is_refused_when_built(fields: Dict[str, float]) -> None:
    """A pump that accepted these would clamp every lift to NaN, run no water, or run a negative flow."""
    with pytest.raises(ValueError, match="ControllerLiftPump"):
        ControllerLiftPump(**fields)


def test_a_controller_lift_pump_sized_for_a_heat_and_a_lift_carries_that_heat_over_that_lift_at_its_largest_flow() -> None:
    """A sized pump whose largest flow were not ``Q_max / (c lift_min)`` would let a boiler's supply drift from its sizing."""
    pump = ControllerLiftPump.sized_for(maximal_heat_power_in_watt=10800.0, minimal_lift_at_maximal_heat_in_kelvin=5.0)
    assert pump == ControllerLiftPump(maximal_mass_flow_in_kg_per_second=10800.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 5.0))
    assert pump.maximal_mass_flow_in_kg_per_second == pytest.approx(0.517, abs=5e-4)


@pytest.mark.parametrize(
    ("maximal_heat_power_in_watt", "minimal_lift_at_maximal_heat_in_kelvin"),
    [(0.0, 5.0), (-10800.0, 5.0), (math.inf, 5.0), (math.nan, 5.0), (10800.0, 0.0), (10800.0, -5.0), (10800.0, math.nan)],
    ids=["no heat", "negative heat", "infinite heat", "NaN heat", "no lift", "negative lift", "NaN lift"],
)
def test_a_controller_lift_pump_sized_for_a_heat_and_lift_without_a_finite_largest_flow_is_refused(
    maximal_heat_power_in_watt: float, minimal_lift_at_maximal_heat_in_kelvin: float
) -> None:
    """A boiler of 0 W or of infinite power, or a lift of 0 K, would size a pump without a usable largest flow."""
    with pytest.raises(ValueError, match="has no"):
        ControllerLiftPump.sized_for(
            maximal_heat_power_in_watt=maximal_heat_power_in_watt,
            minimal_lift_at_maximal_heat_in_kelvin=minimal_lift_at_maximal_heat_in_kelvin,
        )


def test_a_target_supply_temperature_pump_brings_its_return_up_to_the_set_temperature() -> None:
    """Within its bounds the pump runs ``Q / (c (T_set - T_ret))`` and the water leaves exactly at the set temperature."""
    pump = TargetSupplyTemperaturePump(maximal_mass_flow_in_kg_per_second=0.5)
    point = pump.operating_point(heat_power_in_watt=8360.0, return_temperature_in_celsius=40.0, supply_set_temperature_in_celsius=60.0)
    assert point == PumpOperatingPoint(
        mass_flow_in_kg_per_second=8360.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 20.0), supply_temperature_in_celsius=60.0, flow_is_clamped=False
    )
    assert point.mass_flow_in_kg_per_second == pytest.approx(0.1, rel=1e-15)


@pytest.mark.parametrize(
    ("heat_power_in_watt", "return_temperature_in_celsius", "expected_mass_flow_in_kg_per_second"),
    [(8360.0, 59.0, 0.5), (8360.0, 60.0, 0.5), (8360.0, 65.0, 0.5), (-8360.0, 50.0, 0.5), (100.0, 40.0, 0.05)],
    ids=["small lift", "return at the set temperature", "return above it", "cooling below it", "little heat"],
)
def test_a_target_supply_temperature_pump_that_cannot_reach_the_set_temperature_runs_the_nearer_bound(
    heat_power_in_watt: float, return_temperature_in_celsius: float, expected_mass_flow_in_kg_per_second: float
) -> None:
    """At a bound the flow is exactly that bound, the supply follows the heat, and the result says so."""
    pump = TargetSupplyTemperaturePump(minimal_mass_flow_in_kg_per_second=0.05, maximal_mass_flow_in_kg_per_second=0.5)
    point = pump.operating_point(
        heat_power_in_watt=heat_power_in_watt, return_temperature_in_celsius=return_temperature_in_celsius, supply_set_temperature_in_celsius=60.0
    )
    assert point == PumpOperatingPoint(
        mass_flow_in_kg_per_second=expected_mass_flow_in_kg_per_second,
        supply_temperature_in_celsius=return_temperature_in_celsius
        + heat_power_in_watt / (expected_mass_flow_in_kg_per_second * Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN),
        flow_is_clamped=True,
    )


def test_a_target_supply_temperature_pump_without_heat_runs_its_smallest_flow_at_the_return() -> None:
    """Without heat the water cannot reach the set temperature at any flow; the pump stands at its smallest flow."""
    standing = TargetSupplyTemperaturePump(maximal_mass_flow_in_kg_per_second=0.5)
    running = TargetSupplyTemperaturePump(minimal_mass_flow_in_kg_per_second=0.05, maximal_mass_flow_in_kg_per_second=0.5)
    arguments = {"heat_power_in_watt": 0.0, "return_temperature_in_celsius": 40.0, "supply_set_temperature_in_celsius": 60.0}
    assert standing.operating_point(**arguments) == PumpOperatingPoint(mass_flow_in_kg_per_second=0.0, supply_temperature_in_celsius=40.0, flow_is_clamped=False)
    assert running.operating_point(**arguments) == PumpOperatingPoint(mass_flow_in_kg_per_second=0.05, supply_temperature_in_celsius=40.0, flow_is_clamped=True)


@pytest.mark.parametrize(
    "fields",
    [
        {"maximal_mass_flow_in_kg_per_second": math.inf},
        {"maximal_mass_flow_in_kg_per_second": 0.0},
        {"maximal_mass_flow_in_kg_per_second": math.nan},
        {"maximal_mass_flow_in_kg_per_second": 0.5, "minimal_mass_flow_in_kg_per_second": -0.1},
        {"maximal_mass_flow_in_kg_per_second": 0.1, "minimal_mass_flow_in_kg_per_second": 0.5},
    ],
    ids=["infinite largest flow", "largest flow 0", "NaN largest flow", "negative smallest flow", "smallest above largest"],
)
def test_a_target_supply_temperature_pump_with_bounds_it_cannot_keep_is_refused_when_built(fields: Dict[str, float]) -> None:
    """Without a finite largest flow the pump would run an infinite flow whenever the heat cannot reach the set temperature."""
    with pytest.raises(ValueError, match="TargetSupplyTemperaturePump"):
        TargetSupplyTemperaturePump(**fields)


@pytest.mark.parametrize(
    ("pump", "field_name"),
    [
        (ConstantFlowPump(design_mass_flow_in_kg_per_second=0.3), "design_mass_flow_in_kg_per_second"),
        (FixedLiftPump(target_lift_in_kelvin=5.0), "target_lift_in_kelvin"),
        (ControllerLiftPump(), "maximal_mass_flow_in_kg_per_second"),
        (TargetSupplyTemperaturePump(maximal_mass_flow_in_kg_per_second=0.5), "maximal_mass_flow_in_kg_per_second"),
    ],
    ids=["ConstantFlowPump", "FixedLiftPump", "ControllerLiftPump", "TargetSupplyTemperaturePump"],
)
def test_every_pump_is_a_frozen_subcomponent(pump: Subcomponent, field_name: str) -> None:
    """A pump that was not frozen could be changed by its device in a step; one without the marker would escape the subcomponent checks."""
    assert isinstance(pump, Subcomponent)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(pump, field_name, 0.4)


def test_the_operating_point_is_a_named_tuple() -> None:
    """A per-call frozen dataclass would cost twice a NamedTuple in every pass (coding_style.md, §2 rule 12)."""
    point = ConstantFlowPump(design_mass_flow_in_kg_per_second=0.3).operating_point(heat_power_in_watt=1000.0, return_temperature_in_celsius=30.0)
    assert isinstance(point, tuple)
    assert not dataclasses.is_dataclass(point)
