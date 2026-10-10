"""The part-load rule: the memoryless law an L1 controller sets its device's part-load ratio by.

:meth:`hisim.part_load.PartLoadRule.next_part_load_ratio` is a pure function of the ratio the device ran with and the
store's start and end temperatures. These tests check it case by case from its table, then iterate it as the simulator
does (each pass reads only the ratio run and the store's end temperature of the pass before) against synthetic stores
with one and with two heating devices, and against a real hot-water tank with a tap draw.
"""

import math
from typing import Callable, List, Sequence, Tuple

import pytest

from hisim.part_load import PartLoadRule
from tests.part_load_rigs import TankRig


class RuleIteration:
    """Plain iterations of the rule against synthetic stores, as the simulator's passes run it."""

    #: The target the tests aim at, in °C.
    TARGET_IN_CELSIUS: float = 60.0

    #: The middle of the target band, in °C.
    AIM_IN_CELSIUS: float = TARGET_IN_CELSIUS + PartLoadRule.TARGET_BAND_IN_KELVIN / 2.0

    @staticmethod
    def next_ratio(ratio_run: float, *, start_in_celsius: float, end_in_celsius: float, target_in_celsius: float) -> float:
        """Return the rule's next ratio for one device, with keyword arguments spelled as the rule's."""
        return PartLoadRule.next_part_load_ratio(
            ratio_run=ratio_run,
            start_temperature_in_celsius=start_in_celsius,
            end_temperature_in_celsius=end_in_celsius,
            target_temperature_in_celsius=target_in_celsius,
        )

    @classmethod
    def iterate(
        cls,
        store: Callable[[Sequence[float]], float],
        *,
        start_in_celsius: float,
        targets_in_celsius: Sequence[float],
        passes: int,
    ) -> List[Tuple[float, ...]]:
        """Return the ratios every device runs in each pass, the devices starting from 0 as after a switch-on.

        In each pass every controller applies the rule to the ratio its device ran in the pass before and the store's
        end temperature for those ratios; the devices then run the new ratios. Nothing else carries over.

        Args:
            store: The store's end temperature for the ratios of all devices, in °C.
            start_in_celsius: The store's start temperature, in °C.
            targets_in_celsius: The target of each device's controller, in °C.
            passes: How many passes to run.

        Returns:
            The ratios of every pass, one tuple per pass.
        """
        ratios: Tuple[float, ...] = tuple(0.0 for _ in targets_in_celsius)
        end_in_celsius = start_in_celsius
        history: List[Tuple[float, ...]] = []
        for _ in range(passes):
            ratios = tuple(
                cls.next_ratio(ratio, start_in_celsius=start_in_celsius, end_in_celsius=end_in_celsius, target_in_celsius=target)
                for ratio, target in zip(ratios, targets_in_celsius)
            )
            history.append(ratios)
            end_in_celsius = store(ratios)
        return history

    @staticmethod
    def concave_store(ratios: Sequence[float]) -> float:
        """Return the end temperature of a store from 58.4 °C that a draw would take to 57.6 °C, heated by one device.

        The rise flattens with the ratio, as a device holding its lift brings less heat to a warmer store; the whole
        step ends at 63.5 °C.
        """
        return 57.6 + 5.9 * -math.expm1(-1.5 * ratios[0]) / -math.expm1(-1.5)


@pytest.mark.base
@pytest.mark.parametrize(
    "case, ratio_run, start_in_celsius, end_in_celsius, expected",
    [
        ("the store starts at the target", 0.5, 60.0, 61.0, 0.0),
        ("the store starts above the target", 1.0, 61.0, 59.0, 0.0),
        ("the store ends at the lower edge of the band", 0.4, 55.0, 60.0, 0.4),
        ("the store ends at the upper edge of the band", 0.4, 55.0, 60.05, 0.4),
        ("the draw takes the store below its start", 0.4, 58.0, 57.0, 1.0),
        ("the store ends at its start", 0.4, 58.0, 58.0, 1.0),
        ("the device ran nothing and the store ends below the target", 0.0, 55.0, 56.0, 1.0),
        ("the device ran nothing and another device heats the store past the band", 0.0, 55.0, 61.0, 0.0),
        ("the whole step overshoots: the line from the start", 1.0, 58.4, 63.5, (60.025 - 58.4) / (63.5 - 58.4)),
        ("a part step falls short: the line from the start", 0.5, 50.0, 59.9, 0.5 * 10.025 / 9.9),
        ("the line asks for more than the whole step", 1.0, 58.0, 59.0, 1.0),
        ("the store ends just above the band", 0.5, 50.0, 60.0501, 0.5 * 10.025 / 10.0501),
    ],
)
def test_every_case_of_the_rules_table_gives_its_ratio(
    case: str, ratio_run: float, start_in_celsius: float, end_in_celsius: float, expected: float
) -> None:
    """Each row of the rule's table, at its edges: a wrong order of the cases or a wrong edge fails one row."""
    ratio = RuleIteration.next_ratio(
        ratio_run, start_in_celsius=start_in_celsius, end_in_celsius=end_in_celsius, target_in_celsius=60.0
    )
    assert ratio == pytest.approx(expected, rel=1e-12, abs=0.0), case


@pytest.mark.base
@pytest.mark.parametrize(
    "ratio_run, start_in_celsius, end_in_celsius",
    [(0.5, float("nan"), 60.0), (0.5, 50.0, float("inf")), (-0.1, 50.0, 60.0), (1.5, 50.0, 60.0)],
)
def test_a_temperature_that_is_not_finite_or_a_ratio_outside_zero_to_one_is_refused(
    ratio_run: float, start_in_celsius: float, end_in_celsius: float
) -> None:
    """A NaN would pass every comparison as false and give a ratio nobody chose; the rule refuses it, as a bad ratio."""
    with pytest.raises(ValueError, match="part-load rule needs"):
        RuleIteration.next_ratio(
            ratio_run, start_in_celsius=start_in_celsius, end_in_celsius=end_in_celsius, target_in_celsius=60.0
        )


@pytest.mark.base
@pytest.mark.parametrize("ratio_run", [0.1, 0.37, 1.0])
def test_the_aim_is_the_fixed_point_wherever_the_store_started(ratio_run: float) -> None:
    """A store that ends at the aim keeps the ratio run, whatever its start temperature: the start sets no answer."""
    for start_in_celsius in (40.0, 55.0, 59.9):
        assert (
            RuleIteration.next_ratio(
                ratio_run,
                start_in_celsius=start_in_celsius,
                end_in_celsius=RuleIteration.AIM_IN_CELSIUS,
                target_in_celsius=RuleIteration.TARGET_IN_CELSIUS,
            )
            == ratio_run
        )


@pytest.mark.base
def test_a_store_that_rises_in_proportion_to_the_ratio_lands_in_one_trial() -> None:
    """Without a draw the line from the start temperature is exact: the second trial lands the store at the aim."""

    def linear_store(ratios: Sequence[float]) -> float:
        """Return the end temperature of a store from 55 °C that the whole step heats by 12 K."""
        return 55.0 + 12.0 * ratios[0]

    history = RuleIteration.iterate(
        linear_store, start_in_celsius=55.0, targets_in_celsius=[RuleIteration.TARGET_IN_CELSIUS], passes=4
    )
    assert history[0] == (1.0,)
    assert history[1][0] == pytest.approx(5.025 / 12.0, rel=1e-12)
    assert history[2] == history[1] == history[3]


@pytest.mark.base
def test_a_concave_store_with_a_draw_ends_in_the_band_and_stays() -> None:
    """A store that a draw cools and whose rise flattens is reached in a few trials and then kept, pass after pass."""
    history = RuleIteration.iterate(
        RuleIteration.concave_store, start_in_celsius=58.4, targets_in_celsius=[RuleIteration.TARGET_IN_CELSIUS], passes=12
    )
    final_ratio = history[-1][0]
    end_in_celsius = RuleIteration.concave_store(history[-1])
    assert 60.0 <= end_in_celsius <= 60.0 + PartLoadRule.TARGET_BAND_IN_KELVIN
    settled_from = next(number for number, ratios in enumerate(history) if ratios[0] == final_ratio)
    assert settled_from <= 6
    assert all(ratios[0] == final_ratio for ratios in history[settled_from:])


@pytest.mark.base
def test_two_devices_with_the_same_target_share_the_charge_without_oscillating() -> None:
    """A collector and a backup that aim at the same target scale down together and settle on the target.

    The rule scales both ratios by the same factor, so the store's rise above its start follows the same line as with
    one device: the store lands at once and neither ratio swings.
    """

    def shared_store(ratios: Sequence[float]) -> float:
        """Return the end temperature of a store from 52 °C that a collector heats by 6 K and a backup by 10 K."""
        return 52.0 + 6.0 * ratios[0] + 10.0 * ratios[1]

    history = RuleIteration.iterate(shared_store, start_in_celsius=52.0, targets_in_celsius=[60.0, 60.0], passes=6)
    assert history[0] == (1.0, 1.0)
    assert shared_store(history[1]) == pytest.approx(RuleIteration.AIM_IN_CELSIUS, rel=1e-12)
    assert history[1:] == [history[1]] * 5


@pytest.mark.base
def test_a_device_whose_target_another_device_overshoots_decays_towards_zero_without_oscillating() -> None:
    """A heat pump that aims at 59.5 °C beside a collector that aims at 60 °C gives way to the collector.

    The collector alone can reach its own aim, so every pass the rule scales the heat pump's ratio down by the same
    factor and the store converges to the collector's aim; the heat pump's ratio falls monotonically towards 0, never
    swings back, and converges geometrically.
    """

    def shared_store(ratios: Sequence[float]) -> float:
        """Return the end temperature of a store from 58 °C that a collector heats by 6 K and a heat pump by 4 K."""
        return 58.0 + 6.0 * ratios[0] + 4.0 * ratios[1]

    history = RuleIteration.iterate(shared_store, start_in_celsius=58.0, targets_in_celsius=[60.0, 59.5], passes=40)
    heat_pump_ratios = [ratios[1] for ratios in history]
    assert all(later <= earlier for earlier, later in zip(heat_pump_ratios[1:], heat_pump_ratios[2:]))
    assert heat_pump_ratios[-1] < 1e-4
    assert 60.0 <= shared_store(history[-1]) <= 60.0 + PartLoadRule.TARGET_BAND_IN_KELVIN


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [900, 3600])
def test_a_tank_with_a_tap_draw_ends_in_the_band_and_the_draw_raises_the_ratio(seconds_per_timestep: int) -> None:
    """A real tank with a draw ends the iterated step in the band, at a higher ratio than without the draw.

    A rule that ignored the draw would land the tank below the target. The rule reads the tank's end temperature with
    the draw in it, so it lands, after more trials the larger the draw is against the rise the step needs.
    """

    def ruled(draw_in_liter_per_step: float) -> Tuple[List[float], float]:
        """Return the ratios and end temperature of a tank from 57 °C with a 70 °C heater and the given draw."""
        return TankRig.ruled_step(
            seconds_per_timestep=seconds_per_timestep,
            start_temperature_in_celsius=57.0,
            target_temperature_in_celsius=60.0,
            draw_in_liter_per_step=draw_in_liter_per_step,
            full_load_mass_flow_in_kg_per_second=0.2 * 900.0 / seconds_per_timestep,
            supply_temperature_in_celsius=70.0,
            passes=12,
        )

    with_draw_ratios, with_draw_end_in_celsius = ruled(30.0)
    without_draw_ratios, without_draw_end_in_celsius = ruled(0.0)
    for end_in_celsius in (with_draw_end_in_celsius, without_draw_end_in_celsius):
        assert 60.0 <= end_in_celsius <= 60.0 + PartLoadRule.TARGET_BAND_IN_KELVIN
    assert 0.0 < without_draw_ratios[-1] < with_draw_ratios[-1] < 1.0
    assert with_draw_ratios[-2] == with_draw_ratios[-1]
