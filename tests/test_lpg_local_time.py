"""Tests for putting the LoadProfileGenerator's wall-clock minutes onto the run's standard-time clock.

The LoadProfileGenerator labels its minutes on the German wall clock without the daylight-saving switches. A run's
clock is standard time (``roadmap/midyear_start_epic.md``, owner decision 1 of 2026-10-14), UTC+1 all year for a
German household, as for the weather of a Berlin frame. Before hisim-9g2.5 the conversion went to UTC instead: it
started every run at the profile's row 60, 01:00 on the wall clock, and filled the hour that left missing at the
end with a copy of the year's last hour. These tests pin the conversion that replaced it: localize, convert,
slice.
"""

import datetime
import pathlib

import numpy as np
import pandas as pd
import pytest

from hisim import utils
from hisim.caching import Fingerprints, ImportClosure
from hisim.components import lpg_local_time
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector, UtspLpgConnectorConfig
from hisim.components.lpg_local_time import LpgLocalTime
from hisim.simulationparameters import SimulationParameters
from hisim.window_offset import WindowOffsetError

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


class Profile:
    """A profile of 2021 whose value in every minute is the number of its row, so a window shows which rows it read."""

    #: The first wall-clock label of the profile.
    NEW_YEAR = datetime.datetime(2021, 1, 1)

    #: The minutes of a year that is not a leap year.
    MINUTES_OF_A_YEAR = 525_600

    @classmethod
    def rows(cls, number_of_rows: int = MINUTES_OF_A_YEAR) -> pd.DataFrame:
        """Return a profile whose column ``row`` holds the number of each row.

        Args:
            number_of_rows: how many minutes the profile has.

        Returns:
            pd.DataFrame: one row per minute from 1 January 2021 00:00 on the wall clock.
        """
        return pd.DataFrame({"row": np.arange(number_of_rows)})

    @classmethod
    def row_of(cls, wall_clock: datetime.datetime) -> int:
        """Return the row of the profile labelled ``wall_clock``.

        Args:
            wall_clock: a wall-clock minute of 2021.

        Returns:
            int: its row, counted in minutes from 1 January 00:00.
        """
        return int((wall_clock - cls.NEW_YEAR).total_seconds() // 60)

    @classmethod
    def window(cls, start: datetime.datetime, minutes: int, number_of_rows: int = MINUTES_OF_A_YEAR) -> np.ndarray:
        """Return which rows of the profile a window reads.

        Args:
            start: the window's start in standard time.
            minutes: the window's length.
            number_of_rows: how many minutes the profile has.

        Returns:
            np.ndarray: the row read in each minute of the window.
        """
        window = LpgLocalTime.window_in_standard_time(
            minute_values=cls.rows(number_of_rows),
            first_label=cls.NEW_YEAR,
            window_start=start,
            window_minutes=minutes,
            source="the test profile",
        )
        return window["row"].to_numpy()


@pytest.mark.base
def test_a_january_window_starts_at_the_profiles_first_row() -> None:
    """Catches the window starting an hour into the profile, as the conversion to UTC did (row 60, 01:00)."""
    rows = Profile.window(Profile.NEW_YEAR, 7 * 1440)

    assert list(rows) == list(range(7 * 1440))


@pytest.mark.base
def test_a_full_year_drops_the_spring_hour_repeats_the_autumn_hour_and_ends_on_the_profiles_last_row() -> None:
    """Catches a fabricated last hour, a kept spring hour and a lost autumn hour.

    On 28 March 2021 the wall clock skips 02:00 to 02:59, and on 31 October 2021 it passes 02:00 to 02:59 twice.
    """
    rows = Profile.window(Profile.NEW_YEAR, Profile.MINUTES_OF_A_YEAR)

    spring = Profile.row_of(datetime.datetime(2021, 3, 28, 2))
    autumn = Profile.row_of(datetime.datetime(2021, 10, 31, 2))
    assert len(rows) == Profile.MINUTES_OF_A_YEAR
    assert not np.isin(np.arange(spring, spring + 60), rows).any()
    assert all(np.count_nonzero(rows == row) == 2 for row in range(autumn, autumn + 60))
    assert list(rows[-60:]) == list(range(Profile.MINUTES_OF_A_YEAR - 60, Profile.MINUTES_OF_A_YEAR))
    first_autumn_position = int(np.flatnonzero(rows == autumn)[0])
    assert list(rows[first_autumn_position - 1: first_autumn_position + 121]) == (
        [autumn - 1] + list(range(autumn, autumn + 60)) * 2 + [autumn + 60]
    )


@pytest.mark.base
def test_a_july_window_reads_the_wall_clock_an_hour_ahead() -> None:
    """Catches a July window read from the wall clock's 00:00 or counted in minutes from 1 January.

    1 July 00:00 standard time is 01:00 summer time, row 260 700 of the profile. It is the run's minute 260 640 of
    the year, the anchor of ``tests/test_window_offset.py``: the July window of a full-year run.
    """
    july = Profile.window(datetime.datetime(2021, 7, 1), 1440)
    whole_year = Profile.window(Profile.NEW_YEAR, Profile.MINUTES_OF_A_YEAR)

    assert july[0] == Profile.row_of(datetime.datetime(2021, 7, 1, 1)) == 260_700
    assert list(july) == list(range(260_700, 260_700 + 1440))
    assert list(whole_year[260_640: 260_640 + 1440]) == list(july)


@pytest.mark.base
def test_a_run_from_january_into_the_summer_is_served() -> None:
    """Catches the failure of the UTC conversion for a run that ends between the two clock changes.

    It removed the spring hour but added the autumn hour only for a run that reached it, so its series came out
    an hour short of its timestamps. The run here ends on 7 July, the end of the mid-year oracle's reference.
    """
    minutes = 188 * 1440

    rows = Profile.window(Profile.NEW_YEAR, minutes, number_of_rows=minutes + 60)

    assert len(rows) == minutes
    assert rows[-1] == Profile.row_of(datetime.datetime(2021, 7, 8, 0, 59))


@pytest.mark.base
def test_a_profile_that_ends_with_the_window_on_the_wall_clock_cannot_serve_a_summer_window() -> None:
    """Catches the missing summer hour being filled instead of refused.

    A profile of the days 1 to 7 July ends on 7 July 23:59 on the wall clock, which is 22:59 in standard time, an
    hour before the window does.
    """
    first_label = datetime.datetime(2021, 7, 1)
    profile = Profile.rows(7 * 1440)

    with pytest.raises(WindowOffsetError, match="ends before the run does"):
        LpgLocalTime.window_in_standard_time(profile, first_label, first_label, 7 * 1440, "the test profile")


@pytest.mark.base
@pytest.mark.parametrize(
    ("start", "days", "last_day"),
    [
        (datetime.datetime(2021, 1, 1), 7, datetime.date(2021, 1, 7)),
        (datetime.datetime(2021, 7, 1), 7, datetime.date(2021, 7, 8)),
        (datetime.datetime(2021, 1, 1), 365, datetime.date(2021, 12, 31)),
        (datetime.datetime(2021, 10, 25), 7, datetime.date(2021, 10, 31)),
    ],
)
def test_the_last_wall_clock_day_a_profile_of_whole_days_has_to_reach(
    start: datetime.datetime, days: int, last_day: datetime.date
) -> None:
    """Catches a request for whole days that stops an hour short of a window ending in summer.

    A window ending at midnight in summer time ends at 00:59 of the next day on the wall clock; one ending in
    winter, including one ending on the day of the autumn clock change, ends on its own last day.
    """
    assert LpgLocalTime.last_wall_clock_day(start, days * 1440) == last_day


@pytest.mark.base
def test_reading_the_window_and_an_hour_covers_a_summer_window() -> None:
    """Catches a reader that reads exactly the window's minutes from its first day, and so misses its last hour.

    The profile starts at 00:00 of the window's first day on the wall clock, which in July is an hour before the
    window starts in standard time.
    """
    minutes = 7 * 1440
    first_day = datetime.datetime(2021, 7, 1)
    profile = Profile.rows(LpgLocalTime.minutes_to_read(minutes))

    window = LpgLocalTime.window_in_standard_time(profile, first_day, first_day, minutes, "the test profile")

    assert list(window["row"]) == list(range(60, 60 + minutes))


class PredefinedOccupancy:
    """The shipped CHR01 profile read through the occupancy, and the raw rows it was read from."""

    #: The shipped profile's files, as the occupancy finds them.
    FILES = utils.HISIMPATH["occupancy"]["CHR01 Couple both at Work"]

    @classmethod
    def build(cls, start: datetime.datetime, days: int, directory: pathlib.Path) -> UtspLpgConnector:
        """Build the occupancy of the shipped profile for a window at 60 s.

        Args:
            start: the window's start.
            days: the window's length in days.
            directory: where the run writes and caches.

        Returns:
            UtspLpgConnector: the built occupancy, its series read.
        """
        parameters = SimulationParameters(
            start_date=start,
            end_date=start + datetime.timedelta(days=days),
            seconds_per_timestep=60,
            result_directory=str(directory / "results"),
            post_processing_options=[],
            cache_dir_path=str(directory / "cache"),
        )
        config = UtspLpgConnectorConfig.preset_couple_both_at_work("Occupancy")
        return UtspLpgConnector(my_simulation_parameters=parameters, config=config)

    @classmethod
    def raw_electricity_in_watt(cls) -> np.ndarray:
        """Return the shipped electricity profile of every minute of 2021, in W.

        Returns:
            np.ndarray: 525 600 values.
        """
        frame = pd.read_csv(cls.FILES["electricity_consumption"], sep=";")
        return frame["Sum [kWh]"].to_numpy() * 1000 * 60


@pytest.mark.base
def test_the_occupancy_reads_the_profiles_first_minute_at_the_runs_first_timestep(tmp_path: pathlib.Path) -> None:
    """Catches the occupancy's January run starting at the profile's 01:00, as the conversion to UTC made it."""
    occupancy = PredefinedOccupancy.build(datetime.datetime(2021, 1, 1), 2, tmp_path)
    raw = PredefinedOccupancy.raw_electricity_in_watt()

    assert len(occupancy.electricity_consumption) == 2 * 1440
    assert occupancy.electricity_consumption == pytest.approx(list(raw[: 2 * 1440]), rel=1e-12)


@pytest.mark.base
def test_the_occupancys_cache_key_carries_the_conversions_fingerprint(tmp_path: pathlib.Path) -> None:
    """Catches an occupancy cache entry outliving the conversion that wrote it.

    The entry holds the profiles after the conversion, so a key that names only the configuration and the
    simulation parameters serves the old values after the conversion changes.
    """
    material = PredefinedOccupancy.build(datetime.datetime(2021, 1, 1), 1, tmp_path).extra_cache_key_material()

    assert material == ":lpg_local_time=" + Fingerprints.code(ImportClosure.of(lpg_local_time))
