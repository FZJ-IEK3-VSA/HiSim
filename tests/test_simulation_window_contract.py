"""Tests for the window contract of :class:`hisim.simulationparameters.SimulationParameters`.

The window is the span from ``start_date`` to ``end_date`` a run simulates. Every profile-driven
component, the simulator's results index and the carpet and single-day plots rely on four facts
about it, and ``SimulationParameters`` -- the only way a run gets its parameters -- refuses a
window that breaks any of them: it starts at midnight, covers whole days, lies within one
calendar year, and divides into whole timesteps. These tests pin each rule from both sides, the
factories that build the shipped windows, and the energy-system reader reporting a refusal as a
format error.
"""

import datetime
from pathlib import Path

import numpy as np
import pytest

from hisim.energy_system.errors import EnergySystemFormatError
from hisim.energy_system.executor import SimulationParametersReader
from hisim.simulationparameters import SimulationParameters, SimulationWindowError

pytestmark = pytest.mark.base


def _window(start: datetime.datetime, end: datetime.datetime, seconds_per_timestep: int = 900) -> int:
    """Return the number of timesteps the contract grants a window, raising if it refuses it.

    Args:
        start: The first moment of the window.
        end: The moment the window ends.
        seconds_per_timestep: The step length in seconds.

    Returns:
        The number of timesteps of the window.
    """
    return SimulationParameters(start_date=start, end_date=end, seconds_per_timestep=seconds_per_timestep).timesteps


class TestAcceptedWindows:
    """Windows that keep the contract, and the number of timesteps each one has."""

    def test_a_july_week_at_15_minutes_has_672_timesteps(self) -> None:
        """Seven days of 96 quarter hours."""
        assert _window(datetime.datetime(2021, 7, 1), datetime.datetime(2021, 7, 8)) == 672

    def test_a_full_year_ends_on_the_first_of_january_of_the_next_year(self) -> None:
        """The end is excluded, so the last timestep starts on 31 December and the window is one year."""
        assert _window(datetime.datetime(2021, 1, 1), datetime.datetime(2022, 1, 1), 3600) == 8760

    def test_a_leap_year_has_8784_hours(self) -> None:
        """Whether a source can serve 29 February is the source's question, not the window's."""
        assert _window(datetime.datetime(2020, 1, 1), datetime.datetime(2021, 1, 1), 3600) == 8784

    def test_the_last_day_of_the_year_is_a_window(self) -> None:
        """A window that ends on 1 January at midnight has its last timestep on 31 December."""
        assert _window(datetime.datetime(2021, 12, 31), datetime.datetime(2022, 1, 1), 60) == 1440

    @pytest.mark.parametrize("seconds_per_timestep", [np.int64(900), np.float64(900.0)])
    def test_a_numpy_step_length_is_a_number_of_seconds(self, seconds_per_timestep: float) -> None:
        """A step length read through numpy or pandas is accepted, as before the contract."""
        assert _window(datetime.datetime(2021, 7, 1), datetime.datetime(2021, 7, 2), seconds_per_timestep) == 96

    def test_a_step_length_that_divides_the_window_but_not_a_day_is_accepted(self) -> None:
        """Two days in three steps of 16 hours: the rule is whole timesteps over the window."""
        assert _window(datetime.datetime(2021, 3, 1), datetime.datetime(2021, 3, 3), 16 * 3600) == 3


class TestRefusedWindows:
    """Each rule of the contract, broken on its own."""

    def test_a_start_after_midnight_is_refused(self) -> None:
        """A window starting at 06:00 would put every carpet row and every profile day off by six hours."""
        with pytest.raises(SimulationWindowError, match="midnight"):
            _window(datetime.datetime(2021, 7, 1, 6), datetime.datetime(2021, 7, 8, 6))

    def test_a_part_of_a_day_is_refused(self) -> None:
        """Twelve hours are not a whole number of days."""
        with pytest.raises(SimulationWindowError, match="whole number of days"):
            _window(datetime.datetime(2021, 7, 1), datetime.datetime(2021, 7, 1, 12))

    def test_an_empty_window_is_refused(self) -> None:
        """Zero days are not a window."""
        with pytest.raises(SimulationWindowError, match="at least one"):
            _window(datetime.datetime(2021, 7, 1), datetime.datetime(2021, 7, 1))

    def test_a_window_ending_before_it_starts_is_refused(self) -> None:
        """A negative duration is not a window either."""
        with pytest.raises(SimulationWindowError, match="at least one"):
            _window(datetime.datetime(2021, 7, 8), datetime.datetime(2021, 7, 1))

    def test_a_window_across_new_year_is_refused(self) -> None:
        """30 December to 2 January needs two years of profiles; the run must be split."""
        with pytest.raises(SimulationWindowError, match="one calendar year"):
            _window(datetime.datetime(2021, 12, 30), datetime.datetime(2022, 1, 2))

    def test_a_step_length_that_does_not_divide_the_window_is_refused(self) -> None:
        """86 400 s are not a multiple of 7000 s; the old code dropped the remainder silently."""
        with pytest.raises(SimulationWindowError, match="whole number of 7000 s timesteps"):
            _window(datetime.datetime(2021, 7, 1), datetime.datetime(2021, 7, 2), 7000)

    @pytest.mark.parametrize("seconds_per_timestep", [0, -60, float("inf"), float("nan")])
    def test_a_step_length_that_is_not_positive_is_refused(self, seconds_per_timestep: int) -> None:
        """A step of zero, negative or not finite seconds divides nothing, and is refused as such."""
        with pytest.raises(SimulationWindowError, match="positive"):
            _window(datetime.datetime(2021, 7, 1), datetime.datetime(2021, 7, 2), seconds_per_timestep)

    def test_a_boolean_step_length_is_refused(self) -> None:
        """``True`` is an int to Python, but not a step length."""
        with pytest.raises(SimulationWindowError, match="positive"):
            SimulationParameters.validated_window(datetime.datetime(2021, 7, 1), datetime.datetime(2021, 7, 2), True)

    def test_the_error_is_a_value_error(self) -> None:
        """Callers that catch ``ValueError`` for bad parameters keep catching this one."""
        assert issubclass(SimulationWindowError, ValueError)


class TestFactories:
    """The named windows of the class all keep the contract."""

    @pytest.mark.parametrize(
        "factory",
        [
            SimulationParameters.full_year,
            SimulationParameters.full_year_all_options,
            SimulationParameters.full_year_with_only_plots,
            SimulationParameters.january_only_with_all_options,
            SimulationParameters.january_only_with_only_plots,
            SimulationParameters.three_months_only,
            SimulationParameters.three_months_with_plots_only,
            SimulationParameters.one_week_only,
            SimulationParameters.one_week_july,
            SimulationParameters.one_week_with_only_plots,
            SimulationParameters.one_day_only,
            SimulationParameters.one_day_only_with_all_options,
            SimulationParameters.one_day_only_with_only_plots,
        ],
    )
    @pytest.mark.parametrize("year", [2020, 2021])
    def test_every_factory_builds_a_valid_window(self, factory, year: int) -> None:
        """Each factory window at 15 minutes, in a leap and a common year, has whole timesteps."""
        parameters = factory(year, 900)
        assert parameters.timesteps * 900 == parameters.duration.total_seconds()


class TestEnergySystemReader:
    """A parameter file whose window breaks the contract is a format error, naming the rule."""

    def test_a_file_with_a_start_after_midnight_is_a_format_error(self, tmp_path: Path) -> None:
        """The reader wraps the refusal like every other parameter it cannot build."""
        path = tmp_path / "broken.simulation.yaml"
        path.write_text(
            'start_date: "2021-07-01T06:00:00"\nend_date: "2021-07-08T00:00:00"\nseconds_per_timestep: 900\n',
            encoding="utf-8",
        )
        with pytest.raises(EnergySystemFormatError, match="midnight"):
            SimulationParametersReader.read(path)
