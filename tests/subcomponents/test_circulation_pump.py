"""The circulation pump's four flow laws, its clamping and the settings it refuses when it is built.

:class:`hisim.subcomponents.pumps.CirculationPump` is the one pump law of every device that pumps its own loop
(``system_docs/component_design_principles.md``, section C). Each test states the law of one mode and checks it on
plain numbers, without building a component.
"""

import dataclasses
import math
from typing import ClassVar

import pytest

from hisim.subcomponents.base import Subcomponent
from hisim.subcomponents.pumps import CirculationPump, PumpMode, PumpOperatingPoint

pytestmark = pytest.mark.base


class Water:
    """The specific heat the pump computes with, so each expected value below is written out with it."""

    #: The specific heat of water, in J/(kg K).
    SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN: ClassVar[float] = CirculationPump.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN


def test_a_constant_pump_runs_its_design_flow_and_its_outlet_follows_the_heat() -> None:
    """A constant pump that changed its flow with the heat would not be the design-flow pump of a distribution system."""
    pump = CirculationPump.constant(design_mass_flow_in_kg_per_second=0.25)
    point = pump.operating_point(heat_power_in_watt=5225.0, return_temperature_in_celsius=30.0)
    assert point == PumpOperatingPoint(0.25, 30.0 + 5225.0 / (0.25 * Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN), False)
    assert point.supply_temperature_in_celsius == pytest.approx(35.0, rel=1e-15)
    assert pump.operating_point(heat_power_in_watt=0.0, return_temperature_in_celsius=30.0) == (0.25, 30.0, False)


def test_a_target_lift_pump_carries_the_heat_over_its_lift_within_its_bounds() -> None:
    """Within its bounds a temperature-sensor pump runs ``Q / (c dT)`` and the water leaves exactly ``dT`` warmer."""
    pump = CirculationPump.target_lift(
        target_lift_in_kelvin=20.0, minimal_mass_flow_in_kg_per_second=0.005, maximal_mass_flow_in_kg_per_second=0.05
    )
    point = pump.operating_point(heat_power_in_watt=1672.0, return_temperature_in_celsius=40.0)
    assert point == PumpOperatingPoint(1672.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 20.0), 60.0, False)
    assert point.mass_flow_in_kg_per_second == pytest.approx(0.02, rel=1e-15)


@pytest.mark.parametrize(
    ("heat_power_in_watt", "expected_mass_flow_in_kg_per_second"),
    [(200.0, 0.005), (8360.0, 0.05)],
    ids=["below the smallest flow", "above the largest flow"],
)
def test_a_target_lift_pump_outside_its_bounds_runs_the_nearer_bound_and_says_so(
    heat_power_in_watt: float, expected_mass_flow_in_kg_per_second: float
) -> None:
    """At a bound the flow is the bound itself, the outlet ``T_ret + Q / (c m)``, and the clamp is reported.

    A pump that kept the target lift at a bound would carry heat its flow cannot carry; one that hid the clamp would
    leave a collector, whose heat depends on its outlet, at a heat it no longer gives.
    """
    pump = CirculationPump.target_lift(
        target_lift_in_kelvin=20.0, minimal_mass_flow_in_kg_per_second=0.005, maximal_mass_flow_in_kg_per_second=0.05
    )
    point = pump.operating_point(heat_power_in_watt=heat_power_in_watt, return_temperature_in_celsius=40.0)
    assert point.mass_flow_in_kg_per_second == expected_mass_flow_in_kg_per_second
    assert point.supply_temperature_in_celsius == 40.0 + heat_power_in_watt / (
        expected_mass_flow_in_kg_per_second * Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN
    )
    assert point.flow_is_clamped


def test_a_target_lift_pump_without_bounds_never_clamps() -> None:
    """A pump built without bounds runs whatever flow the heat asks, as a district-heating hot-water pump does."""
    pump = CirculationPump.target_lift(target_lift_in_kelvin=100.0)
    point = pump.operating_point(heat_power_in_watt=15000.0, return_temperature_in_celsius=50.0)
    assert point == PumpOperatingPoint(15000.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 100.0), 150.0, False)


def test_a_target_lift_pump_given_heat_taken_out_stands_at_its_smallest_flow_of_zero() -> None:
    """A negative heat asks for a negative flow; the pump runs its smallest flow, 0 kg/s, and the water stays put."""
    pump = CirculationPump.target_lift(target_lift_in_kelvin=5.0)
    assert pump.operating_point(heat_power_in_watt=-500.0, return_temperature_in_celsius=40.0) == (0.0, 40.0, True)


def test_a_target_lift_pump_per_area_has_its_bounds_for_the_whole_field() -> None:
    """Bounds per m² and hour become bounds in kg/s for the field: 5 to 40 kg/(m² h) on 4 m² are 0.0056 to 0.0444."""
    pump = CirculationPump.target_lift_per_area(
        target_lift_in_kelvin=20.0,
        minimal_mass_flow_in_kg_per_hour_per_m2=5.0,
        maximal_mass_flow_in_kg_per_hour_per_m2=40.0,
        area_m2=4.0,
    )
    assert pump.mode is PumpMode.TARGET_LIFT
    assert pump.minimal_mass_flow_in_kg_per_second == 5.0 * 4.0 / 3600.0
    assert pump.maximal_mass_flow_in_kg_per_second == 40.0 * 4.0 / 3600.0


def test_a_matched_flow_pump_carries_the_heat_over_the_lift_asked_for() -> None:
    """Unclamped, a matched-flow pump runs ``Q / (c lift)`` and supplies the return plus the lift asked for."""
    pump = CirculationPump.target_supply_temperature()
    point = pump.operating_point(
        heat_power_in_watt=1500.0, return_temperature_in_celsius=50.0, lift_to_supply_set_temperature_in_kelvin=25.0
    )
    assert point == PumpOperatingPoint(1500.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 25.0), 75.0, False)


def test_a_matched_flow_pump_raises_a_small_lift_to_the_rise_at_its_largest_flow() -> None:
    """A lift close to 0 K would ask for an unbounded flow; the pump runs its largest and the supply ends higher.

    The pump clamps the lift, the variable this mode holds: the rise is ``Q / (c m_max)``, the flow the heat over that
    rise, which is the largest flow to within rounding. This is the boiler's arithmetic, so its results stay bit for
    bit what they were.
    """
    pump = CirculationPump.target_supply_temperature_sized_for(
        maximal_heat_power_in_watt=10450.0, minimal_lift_at_maximal_heat_in_kelvin=5.0
    )
    largest_in_kg_per_second = 10450.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 5.0)
    assert pump.maximal_mass_flow_in_kg_per_second == largest_in_kg_per_second
    point = pump.operating_point(
        heat_power_in_watt=10450.0, return_temperature_in_celsius=40.0, lift_to_supply_set_temperature_in_kelvin=0.01
    )
    rise_in_kelvin = 10450.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * largest_in_kg_per_second)
    assert point.supply_temperature_in_celsius == 40.0 + rise_in_kelvin
    assert point.mass_flow_in_kg_per_second == 10450.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * rise_in_kelvin)
    assert point.mass_flow_in_kg_per_second == pytest.approx(largest_in_kg_per_second, rel=1e-15)
    assert point.flow_is_clamped
    unclamped = pump.operating_point(
        heat_power_in_watt=10450.0, return_temperature_in_celsius=40.0, lift_to_supply_set_temperature_in_kelvin=8.0
    )
    assert unclamped == PumpOperatingPoint(10450.0 / (Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN * 8.0), 48.0, False)


def test_a_matched_flow_pump_lowers_a_large_lift_to_the_rise_at_its_smallest_flow() -> None:
    """A lift so large that the flow would fall below the smallest flow is lowered to the rise that flow carries."""
    pump = CirculationPump.target_supply_temperature(minimal_mass_flow_in_kg_per_second=0.1)
    point = pump.operating_point(
        heat_power_in_watt=2090.0, return_temperature_in_celsius=40.0, lift_to_supply_set_temperature_in_kelvin=30.0
    )
    assert point.supply_temperature_in_celsius == pytest.approx(45.0, rel=1e-15)
    assert point.mass_flow_in_kg_per_second == pytest.approx(0.1, rel=1e-15)
    assert point.flow_is_clamped


@pytest.mark.parametrize(
    ("signal", "expected_mass_flow_in_kg_per_second", "expected_clamped"),
    [(0.4, 0.2, False), (1.0, 0.5, False), (1.5, 0.5, True), (-0.2, 0.0, True)],
)
def test_an_external_signal_pump_runs_the_share_of_its_largest_flow(
    signal: float, expected_mass_flow_in_kg_per_second: float, expected_clamped: bool
) -> None:
    """The controller's signal is the share of the largest flow; outside 0 to 1 it is kept there and reported."""
    pump = CirculationPump.external_signal(maximal_mass_flow_in_kg_per_second=0.5)
    point = pump.operating_point(heat_power_in_watt=2090.0, return_temperature_in_celsius=40.0, signal=signal)
    assert point.mass_flow_in_kg_per_second == pytest.approx(expected_mass_flow_in_kg_per_second, rel=1e-15)
    assert point.flow_is_clamped is expected_clamped
    if expected_mass_flow_in_kg_per_second > 0.0:
        assert point.supply_temperature_in_celsius == pytest.approx(
            40.0 + 2090.0 / (expected_mass_flow_in_kg_per_second * Water.SPECIFIC_HEAT_IN_JOULE_PER_KG_PER_KELVIN),
            rel=1e-15,
        )
    else:
        assert point.supply_temperature_in_celsius == 40.0


def test_a_standing_pump_moves_no_water_and_leaves_it_at_the_return() -> None:
    """The standing point is the one a device without heat at its pump's bound reports: no flow, no lift."""
    assert PumpOperatingPoint.standing(return_temperature_in_celsius=45.0) == (0.0, 45.0, False)


def test_the_result_is_a_named_tuple_and_the_pump_a_frozen_subcomponent() -> None:
    """A per-call frozen dataclass would cost twice a NamedTuple in every pass (coding_style.md, §2 rule 12)."""
    pump = CirculationPump.constant(design_mass_flow_in_kg_per_second=0.3)
    point = pump.operating_point(heat_power_in_watt=1000.0, return_temperature_in_celsius=30.0)
    assert isinstance(point, tuple)
    assert not dataclasses.is_dataclass(point)
    assert isinstance(pump, Subcomponent)
    with pytest.raises(dataclasses.FrozenInstanceError):
        pump.design_mass_flow_in_kg_per_second = 0.4  # type: ignore[misc]


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": "constant", "design_mass_flow_in_kg_per_second": 0.3},
        {"mode": PumpMode.CONSTANT},
        {"mode": PumpMode.CONSTANT, "design_mass_flow_in_kg_per_second": 0.0},
        {"mode": PumpMode.CONSTANT, "design_mass_flow_in_kg_per_second": math.inf},
        {"mode": PumpMode.CONSTANT, "design_mass_flow_in_kg_per_second": 0.3, "target_lift_in_kelvin": 5.0},
        {"mode": PumpMode.CONSTANT, "design_mass_flow_in_kg_per_second": 0.3, "maximal_mass_flow_in_kg_per_second": 0.5},
        {"mode": PumpMode.TARGET_LIFT},
        {"mode": PumpMode.TARGET_LIFT, "target_lift_in_kelvin": 0.0},
        {"mode": PumpMode.TARGET_LIFT, "target_lift_in_kelvin": -5.0},
        {"mode": PumpMode.TARGET_LIFT, "target_lift_in_kelvin": math.nan},
        {"mode": PumpMode.TARGET_LIFT, "target_lift_in_kelvin": 5.0, "design_mass_flow_in_kg_per_second": 0.3},
        {"mode": PumpMode.TARGET_LIFT, "target_lift_in_kelvin": 5.0, "minimal_mass_flow_in_kg_per_second": -0.1},
        {"mode": PumpMode.TARGET_LIFT, "target_lift_in_kelvin": 5.0, "maximal_mass_flow_in_kg_per_second": 0.0},
        {
            "mode": PumpMode.TARGET_LIFT,
            "target_lift_in_kelvin": 5.0,
            "minimal_mass_flow_in_kg_per_second": 0.5,
            "maximal_mass_flow_in_kg_per_second": 0.1,
        },
        {"mode": PumpMode.TARGET_SUPPLY_TEMPERATURE, "target_lift_in_kelvin": 5.0},
        {"mode": PumpMode.TARGET_SUPPLY_TEMPERATURE, "maximal_mass_flow_in_kg_per_second": math.nan},
        {"mode": PumpMode.EXTERNAL_SIGNAL},
        {"mode": PumpMode.EXTERNAL_SIGNAL, "maximal_mass_flow_in_kg_per_second": 0.0},
        {"mode": PumpMode.EXTERNAL_SIGNAL, "maximal_mass_flow_in_kg_per_second": 0.5, "minimal_mass_flow_in_kg_per_second": 0.1},
    ],
)
def test_a_pump_with_a_setting_its_mode_cannot_run_is_refused_when_built(fields: dict) -> None:
    """A pump that accepted these would divide by zero, run no water, or carry a field its mode silently ignores."""
    with pytest.raises(ValueError):
        CirculationPump(**fields)


def test_a_matched_flow_pump_sized_for_no_heat_is_refused() -> None:
    """A boiler of 0 W would size a pump of no largest flow, which the flow law divides by."""
    with pytest.raises(ValueError, match="largest flow above 0"):
        CirculationPump.target_supply_temperature_sized_for(
            maximal_heat_power_in_watt=0.0, minimal_lift_at_maximal_heat_in_kelvin=5.0
        )
