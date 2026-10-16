"""Finds where a run's window starts in a year-long profile, by looking its start up among the profile's own labels.

A component that owns a year-long profile -- the weather is the first -- keeps the year and simulates a
window of it: the run's ``start_date`` and its ``timesteps``. Timestep 0 of the run is the row of the
profile whose label is ``start_date``; that row is the window's *offset*, and timestep ``t`` reads row
``offset + t``. For a run starting on 1 January the offset is 0, which is what every run did before.

**The offset is looked up, never computed.** "181 days of minutes after 1 January" is an answer only
for a profile whose first row is 1 January 00:00 and whose rows are evenly spaced in the clock
``start_date`` is read in. The sources differ in exactly that: the hourly test reference years are
labelled at 00:30, the interpolation pads them to 00:00, and some sources count in UTC while others
count in Berlin time. Looking the start up among the labels the source delivered gives the right row
for each of them, and refuses a window the profile does not cover instead of reading past its end.

**The clock is standard time.** A run's results are stamped from ``start_date`` on an even grid
without daylight saving: a full-year run labels row 260 640 of a minutely Berlin profile "1 July 00:00",
although in German summer time that row is 01:00. A window has to be the same slice of the year as
the full-year run calls it, so ``start_date`` is read in the profile's standard time -- UTC+1 all year
for a Berlin profile, UTC for a UTC profile -- and not in the wall-clock time that would skip the
spring hour. :meth:`WindowOffset.standard_time_labels` turns a profile's time-zone-aware index into
those labels.

**What cannot be served is refused here.** Whether a profile can serve a window is the profile's
answer: a 365-day source laid onto the leap year 2020 has no 31 December, and a source whose last
sample is 23:45 cannot serve the minutes after it. The lookup is the place where that becomes known,
so it raises :class:`WindowOffsetError` and says which date is missing and why.

This module imports pandas and the standard library only, so that a cache producer may import it.
"""

import calendar
import datetime
from typing import ClassVar

import pandas as pd

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


class WindowOffsetError(ValueError):
    """A profile cannot serve the run's window: its start is not among its labels, or it ends before the window."""


class WindowOffset:
    """The window offset of a run in one year-long profile, and the standard-time labels it is looked up in.

    Example: a minutely Berlin weather profile of 2021 starts at 00:00 standard time on 1 January. A run
    from 1 July 2021 00:00 for one week at 60 s resolves to row 260 640 (181 days of minutes), and its
    timestep ``t`` reads row ``260 640 + t``. A run from 1 January resolves to row 0.
    """

    #: The number of days of a profile that can serve every date of a leap year.
    LEAP_YEAR_DAYS: ClassVar[int] = 366

    @staticmethod
    def standard_time_labels(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
        """Return a profile's labels without time zone, in its time zone's standard time.

        The standard time is the UTC offset the zone has outside daylight saving. It is read off the
        first label, which for every profile HiSim reads lies on 1 January, outside daylight saving.
        Example: a Berlin label of 2021-07-01 02:00+02:00 becomes 2021-07-01 01:00, the same instant in
        UTC+1. An index without time zone is returned as it is: its labels are already the source's own
        clock.

        Args:
            index: the profile's index.

        Returns:
            pd.DatetimeIndex: one label per row, without time zone, evenly spaced across the
                daylight-saving switches.
        """
        if index.tz is None or len(index) == 0:
            return index
        first = index[0]
        # A zone without daylight saving, UTC among them, may answer None instead of zero.
        standard_offset = first.utcoffset() - (first.dst() or datetime.timedelta(0))
        labels: pd.DatetimeIndex = index.tz_convert("UTC").tz_localize(None) + standard_offset
        return labels

    @classmethod
    def resolve(
        cls,
        labels: pd.DatetimeIndex,
        window_start: datetime.datetime,
        timesteps: int,
        seconds_per_timestep: int,
        source: str,
    ) -> int:
        """Return the row of ``labels`` the run's timestep 0 reads, after checking the profile covers the window.

        Args:
            labels: the profile's labels at the run's resolution, without time zone, in standard time
                (:meth:`standard_time_labels`).
            window_start: the run's ``start_date``.
            timesteps: the run's number of timesteps.
            seconds_per_timestep: the run's timestep in seconds.
            source: what the profile is, for the error message, for example
                ``"the weather of aachen_center (DWD_TRY)"``.

        Returns:
            int: the window offset, the row labelled ``window_start``.

        Raises:
            WindowOffsetError: if the labels are empty, carry a time zone or are not in ascending
                order, if ``window_start`` carries a time zone or is not among them, if the profile
                ends before the window's last timestep, or if the label of that last timestep is not
                where an even grid at the run's resolution puts it.
        """
        if len(labels) == 0:
            raise WindowOffsetError(
                f"{source} has no rows, so it cannot serve a window starting at {window_start}."
            )
        if window_start.tzinfo is not None:
            raise WindowOffsetError(
                f"The window of {source} starts at {window_start}, which carries a time zone; a window start is "
                "read in the profile's standard time without a time zone, as SimulationParameters.start_date is."
            )
        if labels.tz is not None:
            raise WindowOffsetError(
                f"The labels of {source} carry the time zone {labels.tz}; a window is looked up among labels "
                "in standard time without a time zone. Convert them with WindowOffset.standard_time_labels."
            )
        if not labels.is_monotonic_increasing:
            raise WindowOffsetError(
                f"The labels of {source} are not in ascending order, so no window can be looked up in them."
            )
        start = pd.Timestamp(window_start)
        position = int(labels.searchsorted(start))
        if position >= len(labels) or labels[position] != start:
            raise WindowOffsetError(
                f"{source} has no row labelled {start} (standard time), so it cannot serve a window starting "
                f"there. Its rows run from {cls._first_and_last(labels)}.{cls._leap_year_note(labels, start)}"
            )
        last_position = position + timesteps - 1
        window_end = start + pd.Timedelta(seconds=seconds_per_timestep * (timesteps - 1))
        if last_position >= len(labels):
            raise WindowOffsetError(
                f"{source} ends before the run does: the window's last timestep, {window_end}, would be row "
                f"{last_position}, but the profile has {len(labels)} rows, from "
                f"{cls._first_and_last(labels)}.{cls._leap_year_note(labels, window_end)}"
            )
        if labels[last_position] != window_end:
            raise WindowOffsetError(
                f"{source} does not run on an even grid of {seconds_per_timestep} s across the window: row "
                f"{last_position}, the window's last timestep, is labelled {labels[last_position]} instead of "
                f"{window_end}. The profile was produced at another resolution, or it has a gap."
            )
        return position

    @staticmethod
    def _first_and_last(labels: pd.DatetimeIndex) -> str:
        """Name a profile's first and last label for an error message.

        Args:
            labels: the profile's labels, at least one.

        Returns:
            str: ``"<first> to <last>"``.
        """
        return f"{labels[0]} to {labels[-1]}"

    @classmethod
    def _leap_year_note(cls, labels: pd.DatetimeIndex, missing: pd.Timestamp) -> str:
        """Explain a missing date by the leap year, when that is the reason.

        A source of 365 days laid onto a leap year by position covers 1 January to 30 December, so it
        cannot serve 31 December, and a full-year run of that year is one day longer than the source.

        Args:
            labels: the profile's labels.
            missing: the date the profile lacks.

        Returns:
            str: a sentence beginning with a space, or an empty string when the year is not a leap year
                or the profile covers all of it.
        """
        year = missing.year
        if not calendar.isleap(year) or len(labels) == 0:
            return ""
        covered_days = (labels[-1] - labels[0]).days + 1
        if covered_days >= cls.LEAP_YEAR_DAYS:
            return ""
        return (
            f" {year} is a leap year of {cls.LEAP_YEAR_DAYS} days and this source delivers {covered_days}: laid "
            "onto the year by position it has no 31 December. Run a year that is not a leap year, or a "
            "window that ends before the source does."
        )
