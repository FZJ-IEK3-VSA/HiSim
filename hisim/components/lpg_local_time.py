"""Puts the LoadProfileGenerator's minute profiles onto the clock HiSim simulates, and cuts out the run's window.

The LoadProfileGenerator simulates a household on the wall clock of Germany and labels its minutes without the
daylight-saving switches: its year is 525 600 gapless minutes. The spring hour the clock skips (02:00 to 02:59 on
the last Sunday of March) is in it, and the autumn hour the clock passes twice (02:00 to 02:59 on the last Sunday
of October) is in it once. A household that gets up at 07:00 gets up at 07:00 on the wall clock, in winter and in
summer.

HiSim's clock is standard time (``roadmap/midyear_start_epic.md``, owner decision 1 of 2026-10-14): a run's
``start_date`` is read in the standard time of the profile's own frame, which for a German household is UTC+1 all
year, as it is for the weather of a Berlin frame. :meth:`LpgLocalTime.window_in_standard_time` puts a profile onto
that clock in three steps:

1. *Localize*: every row's label is read as a Berlin wall-clock time. A row of the spring hour names no instant
   and is dropped. A row of the autumn hour names two instants, its summer-time one and its winter-time one, and
   stands for both.
2. *Convert*: every instant is labelled in standard time. The labels are an even grid of minutes without a gap.
3. *Slice*: the run's ``start_date`` is looked up among those labels (:meth:`WindowOffset.resolve`), and the
   window's minutes are cut out from there. A window the profile does not cover is refused.

Example: a profile delivered from 1 January 00:00. Its row of 1 January 00:00 is 00:00 standard time, the first
minute of a run from 1 January. Its row of 1 July 07:00 (summer time) is 06:00 standard time, so a run reads the
household's breakfast at 06:00 on its clock in July and at 07:00 in January, on the same clock as the sun and the
weather.

The module imports pandas, numpy and :mod:`hisim.window_offset` only: the car's cached profile is computed with
it, so the car's producer imports it and its cache key fingerprints this module, and the occupancy's cache key
fingerprints it too. An edit here therefore makes both caches miss. :mod:`hisim.utils` imports the simulation
parameters and the cache client, which a producer's import closure must not contain.
"""

import datetime
from typing import ClassVar, Tuple

import numpy as np
import pandas as pd

from hisim.window_offset import WindowOffset

__authors__ = "Noah Pflugradt, Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class LpgLocalTime:
    """The LoadProfileGenerator's wall-clock minutes on HiSim's standard-time clock, and the run's window of them.

    Example: a profile of 525 600 minutes from 1 January 2021 00:00 and a run of the whole of 2021 give the run's
    525 600 minutes: the 60 rows of the spring hour are dropped and the 60 rows of the autumn hour are taken twice.
    """

    #: The time zone whose wall clock the LoadProfileGenerator's labels show.
    TIME_ZONE: ClassVar[str] = "Europe/Berlin"

    #: The resolution of the LoadProfileGenerator's profiles, in seconds.
    SECONDS_PER_MINUTE: ClassVar[int] = 60

    @classmethod
    def window_in_standard_time(
        cls,
        minute_values: pd.DataFrame,
        first_label: datetime.datetime,
        window_start: datetime.datetime,
        window_minutes: int,
        source: str,
    ) -> pd.DataFrame:
        """Return the minutes of a run's window from a wall-clock minute profile, one row per standard-time minute.

        Example: a profile delivered from 1 January 2021 00:00 and a window of one day from 1 July 2021 give the
        1440 rows labelled 1 July 01:00 to 2 July 00:59 on the wall clock, which are 1 July 00:00 to 23:59 in
        standard time.

        Args:
            minute_values: one row per minute of the profile, in the order the LoadProfileGenerator delivered them.
            first_label: the wall-clock time of the first row, without a time zone.
            window_start: the run's ``start_date``, read in standard time.
            window_minutes: the length of the run in minutes.
            source: what the profile is, for the error message.

        Returns:
            pd.DataFrame: ``window_minutes`` rows with the columns of ``minute_values``, indexed from 0.

        Raises:
            hisim.window_offset.WindowOffsetError: if the profile does not cover the window.
        """
        positions, labels = cls.standard_time_rows(first_label, len(minute_values))
        offset = WindowOffset.resolve(
            labels=labels,
            window_start=window_start,
            timesteps=window_minutes,
            seconds_per_timestep=cls.SECONDS_PER_MINUTE,
            source=source,
        )
        window_positions = positions[offset: offset + window_minutes]
        return minute_values.iloc[window_positions].reset_index(drop=True)

    @classmethod
    def standard_time_rows(
        cls, first_label: datetime.datetime, number_of_rows: int
    ) -> Tuple[np.ndarray, pd.DatetimeIndex]:
        """Return which profile row each standard-time minute reads, and that minute's label.

        The profile's rows are labelled on the wall clock from ``first_label`` in steps of a minute. A row of the
        spring hour names no instant and appears in no position. A row of the autumn hour appears twice: once for
        its summer-time instant and once for its winter-time instant, an hour later in standard time.

        Example: for the 525 600 rows of 2021 from 1 January 00:00, the result has 525 600 positions. Position
        260 640, labelled 1 July 00:00, is row 260 700, the wall-clock row of 1 July 01:00.

        Args:
            first_label: the wall-clock time of the first row, without a time zone.
            number_of_rows: the number of rows of the profile.

        Returns:
            Tuple[np.ndarray, pd.DatetimeIndex]: the row positions and their standard-time labels without a time
                zone, both in ascending order of the labels.
        """
        wall_clock = pd.date_range(start=first_label, periods=number_of_rows, freq="min")
        # Each row is localized twice: as summer time where the label is ambiguous, and as winter time. The two
        # differ only on the autumn hour, whose second reading is added; a skipped spring label is NaT in both.
        as_summer_time = wall_clock.tz_localize(
            cls.TIME_ZONE, ambiguous=np.ones(number_of_rows, dtype=bool), nonexistent="NaT"
        )
        as_winter_time = wall_clock.tz_localize(
            cls.TIME_ZONE, ambiguous=np.zeros(number_of_rows, dtype=bool), nonexistent="NaT"
        )
        exists = np.asarray(~as_summer_time.isna())
        repeated = exists & np.asarray(as_summer_time != as_winter_time)
        rows = np.arange(number_of_rows)
        positions = np.concatenate([rows[exists], rows[repeated]])
        instants = as_summer_time[exists].append(as_winter_time[repeated])
        order = np.argsort(instants.asi8, kind="stable")
        labels = WindowOffset.standard_time_labels(pd.DatetimeIndex(instants[order]))
        return positions[order], labels

    @classmethod
    def first_label_in_year(cls, start_time: str, year: int) -> datetime.datetime:
        """Return a profile's first wall-clock minute from the ``StartTime`` it states, laid onto the run's year.

        A profile the LoadProfileGenerator computed for the run is the run's calendar year and starts on
        1 January of it, so laying it onto that year changes nothing. A shipped profile of another year is read
        by position in the run's year, as the weather's files are. Example: ``"2021-01-01T00:00:00"`` in a run
        of 2022 is 1 January 2022 00:00.

        Args:
            start_time: the profile's ``StartTime``, ISO 8601 without a time zone.
            year: the run's year.

        Returns:
            datetime.datetime: the wall-clock time of the profile's first row.
        """
        return datetime.datetime.fromisoformat(start_time).replace(year=year)

    @classmethod
    def last_wall_clock_day(cls, window_start: datetime.datetime, window_minutes: int) -> datetime.date:
        """Return the wall-clock day of a window's last minute: the last day a profile of whole days has to reach.

        Example: a window of 1 to 7 January ends on 7 January 23:59 in standard time and on the wall clock. A
        window of 1 to 7 July ends on 7 July 23:59 in standard time, which is 8 July 00:59 on the wall clock, so a
        profile of the window has to reach 8 July.

        Args:
            window_start: the run's ``start_date``, read in standard time.
            window_minutes: the length of the run in minutes.

        Returns:
            datetime.date: the wall-clock date of the window's last minute.
        """
        last_minute = pd.Timestamp(window_start) + pd.Timedelta(minutes=window_minutes - 1)
        in_utc = (last_minute - cls.standard_utc_offset(last_minute.year)).tz_localize("UTC")
        wall_clock = in_utc.tz_convert(cls.TIME_ZONE)
        return datetime.date(wall_clock.year, wall_clock.month, wall_clock.day)

    @classmethod
    def standard_utc_offset(cls, year: int) -> pd.Timedelta:
        """Return how far the time zone's standard time is ahead of UTC, read on 1 January of ``year``.

        1 January lies outside daylight saving, so its offset is the standard one. Example: one hour for Berlin.

        Args:
            year: the year whose standard time is meant.

        Returns:
            pd.Timedelta: the offset of standard time from UTC.
        """
        new_year = pd.Timestamp(datetime.datetime(year, 1, 1)).tz_localize(cls.TIME_ZONE)
        return pd.Timedelta(new_year.utcoffset())
