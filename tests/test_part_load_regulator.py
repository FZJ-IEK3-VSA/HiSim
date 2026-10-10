"""The part-load regulator: the search an L1 controller runs over the simulator's passes for its device's ratio.

:class:`hisim.part_load.PartLoadRatioRegulator` is a pure state machine: its memory is a frozen
:class:`hisim.part_load.PartLoadSearch`, and :meth:`~hisim.part_load.PartLoadRatioRegulator.advanced_search` returns the
memory after one pass. These tests drive it with given readings, with synthetic stores that answer with a lag of one or
two passes (the evaluation orders of the simulator), and with a real hot-water tank with a tap draw.
"""

import math
from typing import Callable, List, Tuple

import pytest

from hisim.part_load import BracketEnd, BracketSide, PartLoadRatioRegulator, PartLoadSearch
from tests.part_load_rigs import TankRig


class Search:
    """Helpers that drive the regulator's pure search function."""

    #: The target the tests regulate to, °C.
    TARGET_IN_CELSIUS: float = 60.0

    #: The middle of the target band, °C.
    AIM_IN_CELSIUS: float = TARGET_IN_CELSIUS + PartLoadRatioRegulator.TARGET_BAND_IN_KELVIN / 2.0

    @classmethod
    def advance(
        cls, search: PartLoadSearch, end_temperature_in_celsius: float, start_temperature_in_celsius: float = 58.0
    ) -> PartLoadSearch:
        """Return the search after one pass with the given end temperature, the 60 °C target and a start temperature."""
        return PartLoadRatioRegulator.advanced_search(
            search,
            start_temperature_in_celsius=start_temperature_in_celsius,
            end_temperature_in_celsius=end_temperature_in_celsius,
            target_temperature_in_celsius=cls.TARGET_IN_CELSIUS,
        )

    @classmethod
    def answered(cls, search: PartLoadSearch, end_temperature_in_celsius: float, **start: float) -> PartLoadSearch:
        """Return the search after as many passes as the current trial needs to be read, all with the same reading."""
        trial = search.trial_ratio
        passes = PartLoadRatioRegulator.PASSES_BEFORE_FIRST_READING if search.trials == 1 else PartLoadRatioRegulator.PASSES_PER_TRIAL
        for _ in range(passes + 1 - search.passes_with_trial):
            search = cls.advance(search, end_temperature_in_celsius, **start)
            if search.trial_ratio != trial:
                break
        return search

    @staticmethod
    def iterate(
        store: Callable[[float], float], *, lag: int, passes: int, start_temperature_in_celsius: float
    ) -> List[float]:
        """Return the ratios a regulator publishes over ``passes`` passes against a store answering with ``lag``.

        The store's answer read in pass ``k`` is its end temperature for the ratio published in pass ``k - lag``; before
        that, it reads the start temperature, as the first passes of a step read the values of the step before.
        """
        regulator = PartLoadRatioRegulator()
        published: List[float] = []
        for number in range(passes):
            reading = store(published[number - lag]) if number >= lag else start_temperature_in_celsius
            published.append(
                regulator.next_ratio(
                    start_temperature_in_celsius=start_temperature_in_celsius,
                    end_temperature_in_celsius=reading,
                    target_temperature_in_celsius=Search.TARGET_IN_CELSIUS,
                )
            )
        return published

    @staticmethod
    def concave_store(ratio: float) -> float:
        """Return the end temperature of a store that ends at 57.6 °C without the device and at 63.5 °C with all of it.

        The rise flattens with the ratio as a device holding its lift brings less heat to a warmer store.
        """
        return 57.6 + 5.9 * -math.expm1(-1.5 * ratio) / -math.expm1(-1.5)


@pytest.mark.base
def test_a_fresh_search_publishes_the_whole_step_and_reads_it_only_after_three_passes() -> None:
    """The first trial is 1 and stays for three passes whatever is read; a regulator that read stale values fails."""
    search = PartLoadRatioRegulator.fresh_search()
    for _ in range(PartLoadRatioRegulator.PASSES_BEFORE_FIRST_READING):
        search = Search.advance(search, 80.0)
        assert search.trial_ratio == 1.0
    assert Search.advance(search, 80.0).trial_ratio < 1.0


@pytest.mark.base
def test_a_search_restarted_after_an_off_pass_is_read_in_the_steps_fourth_pass() -> None:
    """A device the controller switches on in the second pass has its first trial read in the fourth pass, not later.

    The off pass counts towards the step's passes: a regulator that counted only its own passes would wait one pass
    more, and a regulator that forgot the off pass would read a stale answer.
    """
    regulator = PartLoadRatioRegulator()
    regulator.restart()
    ratios = [
        regulator.next_ratio(start_temperature_in_celsius=58.0, end_temperature_in_celsius=64.0, target_temperature_in_celsius=60.0)
        for _ in range(PartLoadRatioRegulator.PASSES_BEFORE_FIRST_READING)
    ]
    assert ratios[:-1] == [1.0, 1.0]
    assert ratios[-1] < 1.0
    assert regulator.search.passes_in_step == PartLoadRatioRegulator.PASSES_BEFORE_FIRST_READING + 1


@pytest.mark.base
def test_a_store_that_stays_below_the_target_keeps_the_whole_step() -> None:
    """A whole step that leaves the store too cold is the answer: the trial stays 1, and no further trial is made."""
    search = Search.answered(PartLoadRatioRegulator.fresh_search(), 55.0)
    assert search.trial_ratio == 1.0
    assert search.trials == 1
    assert Search.advance(search, 55.0).trial_ratio == 1.0


@pytest.mark.base
@pytest.mark.parametrize("deviation_in_kelvin", [0.0, 0.01, PartLoadRatioRegulator.TARGET_BAND_IN_KELVIN])
def test_an_end_temperature_in_the_band_is_accepted(deviation_in_kelvin: float) -> None:
    """A reading from the target to the target plus the band keeps the trial; it fails if the band check is off."""
    search = Search.answered(PartLoadRatioRegulator.fresh_search(), Search.TARGET_IN_CELSIUS + deviation_in_kelvin)
    assert search.trial_ratio == 1.0
    assert search.lower is None and search.upper is None


@pytest.mark.base
def test_a_too_hot_whole_step_gives_the_line_from_the_start_temperature() -> None:
    """The second trial lies on the line from the start temperature at 0 through the whole step's end temperature."""
    search = Search.answered(PartLoadRatioRegulator.fresh_search(), 63.47, start_temperature_in_celsius=58.41)
    assert search.trial_ratio == pytest.approx((Search.AIM_IN_CELSIUS - 58.41) / (63.47 - 58.41), rel=1e-12)
    assert search.upper == BracketEnd(1.0, 63.47)
    assert search.trials == 2
    assert search.passes_with_trial == 1


@pytest.mark.base
def test_a_second_too_hot_trial_gives_the_parabola_through_the_start_temperature() -> None:
    """With two too-hot trials the next lies on the parabola through the start temperature and both of them."""
    search = Search.answered(PartLoadRatioRegulator.fresh_search(), 63.64, start_temperature_in_celsius=48.27)
    second_ratio = search.trial_ratio
    search = Search.answered(search, 61.50, start_temperature_in_celsius=48.27)
    rise_1, rise_2 = 61.50 - 48.27, 63.64 - 48.27
    curvature = (rise_2 / 1.0 - rise_1 / second_ratio) / (1.0 - second_ratio)
    slope = rise_1 / second_ratio - curvature * second_ratio
    needed = Search.AIM_IN_CELSIUS - 48.27
    expected = (-slope + math.sqrt(slope * slope + 4.0 * curvature * needed)) / (2.0 * curvature)
    assert search.trial_ratio == pytest.approx(expected, rel=1e-12)
    assert 0.0 < search.trial_ratio < second_ratio
    assert search.previous_upper == BracketEnd(1.0, 63.64)


@pytest.mark.base
def test_without_a_parabola_below_the_latest_trial_the_line_is_used() -> None:
    """A parabola that does not reach the aim below the latest too-hot trial falls back to the straight line."""
    upper, previous_upper = BracketEnd(0.5, 61.0), BracketEnd(0.5, 62.0)
    assert PartLoadRatioRegulator.parabola_ratio(
        upper, previous_upper, aim_temperature_in_celsius=60.0, start_temperature_in_celsius=58.0
    ) is None
    assert PartLoadRatioRegulator.ratio_below_hot_trials(
        upper, previous_upper, aim_temperature_in_celsius=60.0, start_temperature_in_celsius=58.0
    ) == pytest.approx(0.5 * 2.0 / 3.0, rel=1e-12)


@pytest.mark.base
@pytest.mark.parametrize("start_temperature_in_celsius", [60.0, 61.0])
def test_a_store_that_starts_at_or_above_the_aim_gives_the_line_ratio_zero(start_temperature_in_celsius: float) -> None:
    """The line from a start temperature at or above the aim gives 0, so the device is tried off."""
    assert PartLoadRatioRegulator.line_ratio(
        BracketEnd(1.0, 64.0), aim_temperature_in_celsius=60.0, start_temperature_in_celsius=start_temperature_in_celsius
    ) == 0.0


@pytest.mark.base
def test_after_the_estimates_below_hot_trials_the_search_tries_zero_and_keeps_a_too_hot_zero() -> None:
    """A store that ends too hot at every estimate gets the trial 0, and keeps it when it is too hot even then."""
    search = PartLoadRatioRegulator.fresh_search()
    for _ in range(PartLoadRatioRegulator.MAXIMUM_ESTIMATES_BELOW_HOT_TRIALS + 1):
        search = Search.answered(search, 70.0, start_temperature_in_celsius=40.0)
        assert search.trial_ratio > 0.0 or search.estimates_below_hot_trials == (
            PartLoadRatioRegulator.MAXIMUM_ESTIMATES_BELOW_HOT_TRIALS
        )
    assert search.trial_ratio == 0.0
    search = Search.answered(search, 70.0, start_temperature_in_celsius=40.0)
    assert search.trial_ratio == 0.0
    assert Search.advance(search, 70.0, start_temperature_in_celsius=40.0).trial_ratio == 0.0


@pytest.mark.base
def test_a_cold_and_a_hot_trial_give_the_interpolation_and_illinois_halves_the_end_that_stays() -> None:
    """Between a too-cold and a too-hot end the trial is the line's root; an end kept twice gets half its weight."""
    lower, upper = BracketEnd(0.2, 59.0), BracketEnd(0.6, 61.0)
    ratio = PartLoadRatioRegulator.interpolated_ratio(lower, upper, aim_temperature_in_celsius=60.025)
    assert ratio == pytest.approx(0.2 + 0.4 * 1.025 / 2.0, rel=1e-12)
    search = PartLoadSearch(trial_ratio=0.3, lower=lower, upper=upper, last_moved=BracketSide.LOWER, trials=4)
    moved_again = PartLoadRatioRegulator.bracket_with_reading(
        search, end_temperature_in_celsius=59.5, is_too_cold=True, aim_temperature_in_celsius=60.025
    )
    assert moved_again.lower == BracketEnd(0.3, 59.5)
    assert moved_again.upper == BracketEnd(0.6, 61.0, weight=0.5)
    assert moved_again.last_moved == BracketSide.LOWER


@pytest.mark.base
def test_an_end_on_the_wrong_side_of_a_moved_target_is_dropped() -> None:
    """When the target rises past a too-hot end (an energy manager's raise), that end leaves the bracket."""
    search = PartLoadSearch(trial_ratio=0.5, lower=None, upper=BracketEnd(1.0, 62.0), trials=2)
    bracketed = PartLoadRatioRegulator.bracket_with_reading(
        search, end_temperature_in_celsius=61.0, is_too_cold=True, aim_temperature_in_celsius=65.0
    )
    assert bracketed.upper is None
    assert bracketed.lower == BracketEnd(0.5, 61.0)
    assert PartLoadRatioRegulator.with_next_trial(
        bracketed, aim_temperature_in_celsius=65.0, start_temperature_in_celsius=55.0
    ).trial_ratio == 1.0


@pytest.mark.base
def test_a_closed_bracket_settles_on_its_upper_end() -> None:
    """A bracket narrower than the closing width with no reading in the band keeps the upper end and stops."""
    search = PartLoadSearch(
        trial_ratio=0.4, lower=BracketEnd(0.4, 59.0), upper=BracketEnd(0.4 + 1e-12, 61.0), trials=5
    )
    settled = PartLoadRatioRegulator.with_next_trial(search, aim_temperature_in_celsius=60.025, start_temperature_in_celsius=55.0)
    assert settled.is_settled
    assert settled.trial_ratio == 0.4 + 1e-12
    assert Search.advance(settled, 80.0).trial_ratio == settled.trial_ratio


@pytest.mark.base
def test_the_search_settles_after_the_most_trials() -> None:
    """The trial that reaches :attr:`MAXIMUM_TRIALS` is kept for the rest of the step."""
    search = PartLoadSearch(
        trial_ratio=0.5,
        lower=BracketEnd(0.1, 58.0),
        upper=BracketEnd(0.9, 62.0),
        trials=PartLoadRatioRegulator.MAXIMUM_TRIALS - 1,
    )
    settled = PartLoadRatioRegulator.with_next_trial(search, aim_temperature_in_celsius=60.025, start_temperature_in_celsius=55.0)
    assert settled.is_settled
    assert settled.trials == PartLoadRatioRegulator.MAXIMUM_TRIALS


@pytest.mark.base
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_a_temperature_that_is_not_finite_is_refused(bad: float) -> None:
    """A NaN or infinite reading raises instead of steering the device with it."""
    with pytest.raises(ValueError, match="finite end_temperature_in_celsius"):
        Search.advance(PartLoadRatioRegulator.fresh_search(), bad)


@pytest.mark.base
def test_reset_starts_the_next_step_from_the_whole_step() -> None:
    """After :meth:`reset` the regulator publishes 1 again; a regulator that kept the last step's ratio fails."""
    regulator = PartLoadRatioRegulator()
    for _ in range(PartLoadRatioRegulator.PASSES_BEFORE_FIRST_READING + 1):
        regulator.next_ratio(start_temperature_in_celsius=58.0, end_temperature_in_celsius=64.0, target_temperature_in_celsius=60.0)
    assert regulator.held_ratio() < 1.0
    regulator.reset()
    assert regulator.held_ratio() == 1.0
    assert regulator.search == PartLoadRatioRegulator.fresh_search()


@pytest.mark.base
@pytest.mark.parametrize("lag", [1, 2])
def test_the_search_lands_in_the_band_whatever_the_evaluation_order(lag: int) -> None:
    """Against a store answering one or two passes late the search ends in the band, within the simulator's passes.

    A regulator that read its answer one pass too early in the lag-2 order would pair a trial with the answer to the
    one before and miss the band or need more passes.
    """
    published = Search.iterate(Search.concave_store, lag=lag, passes=11, start_temperature_in_celsius=58.4)
    final_ratio = published[-1]
    assert published[-1] == published[-2] == published[-3]
    end_temperature_in_celsius = Search.concave_store(final_ratio)
    assert Search.TARGET_IN_CELSIUS <= end_temperature_in_celsius <= Search.TARGET_IN_CELSIUS + (
        PartLoadRatioRegulator.TARGET_BAND_IN_KELVIN
    )


@pytest.mark.base
def test_both_evaluation_orders_settle_on_the_same_ratio() -> None:
    """The ratio the search lands on does not depend on whether the store answers one or two passes late."""
    lag_one = Search.iterate(Search.concave_store, lag=1, passes=12, start_temperature_in_celsius=58.4)[-1]
    lag_two = Search.iterate(Search.concave_store, lag=2, passes=12, start_temperature_in_celsius=58.4)[-1]
    assert lag_one == lag_two


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [900, 3600])
def test_a_tank_with_a_tap_draw_ends_in_the_band_and_the_draw_raises_the_ratio(seconds_per_timestep: int) -> None:
    """A real tank with a draw ends the regulated step in the band, at a higher ratio than without the draw.

    A search that ignored the draw (a store reading without the tap) would land the tank below the target here; the
    twin tests caught that only in a slow year run.
    """

    def regulated(draw_in_liter_per_step: float) -> Tuple[float, float]:
        """Return the ratio and end temperature of a tank from 57 °C with a 70 °C heater and the given draw."""
        return TankRig.regulated_step(
            seconds_per_timestep=seconds_per_timestep,
            start_temperature_in_celsius=57.0,
            target_temperature_in_celsius=60.0,
            draw_in_liter_per_step=draw_in_liter_per_step,
            full_load_mass_flow_in_kg_per_second=0.2 * 900.0 / seconds_per_timestep,
            supply_temperature_in_celsius=70.0,
            passes=12,
        )

    with_draw_ratio, with_draw_end_in_celsius = regulated(30.0)
    without_draw_ratio, without_draw_end_in_celsius = regulated(0.0)
    band_in_kelvin = PartLoadRatioRegulator.TARGET_BAND_IN_KELVIN
    for end_in_celsius in (with_draw_end_in_celsius, without_draw_end_in_celsius):
        assert 60.0 <= end_in_celsius <= 60.0 + band_in_kelvin
    assert 0.0 < without_draw_ratio < with_draw_ratio < 1.0
