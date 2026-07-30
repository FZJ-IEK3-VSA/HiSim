"""Regression tests for the date strings handed to the local LPG binary.

The LPG deserializes its calculation spec with Newtonsoft under InvariantCulture, which reads a
bare ``dd-MM-yyyy`` string as ``MM-dd-yyyy``. HiSim used to format the dates as ``%d-%m-%Y``, which
failed in two different ways depending on the day of month:

* day > 12 -- the month is out of range and the LPG aborts with
  ``Could not convert string to DateTime: 14-01-2021``;
* day <= 12 -- the string parses, but as a *different* date. An 11-day request ended on
  12 January, was written ``12-01-2021`` and read as 1 December, so the LPG simulated 335 days.
  This one is silent, which makes it the more dangerous of the two.

Full-year runs happened to survive because they end on 1 January, where both readings agree.

These tests need neither the LPG binary nor a UTSP connection: they exercise
:func:`compute_lpg_start_and_end_date` directly, and the invariant they check is that every
produced string round-trips through :meth:`datetime.date.fromisoformat` to the intended date.
"""
# clean
import datetime

import pytest

from hisim.components.loadprofilegenerator_utsp_connector import (
    LPG_DATE_FORMAT,
    compute_lpg_start_and_end_date,
)
from hisim.simulationparameters import SimulationParameters


def _simulation_parameters(days: int, year: int = 2021) -> SimulationParameters:
    """Build simulation parameters spanning *days* days from 1 January of *year*."""
    start = datetime.datetime(year, 1, 1)
    return SimulationParameters(start, start + datetime.timedelta(days=days), 900)


@pytest.mark.base
def test_lpg_date_format_is_iso_8601() -> None:
    """The format constant must be ISO 8601, the only unambiguous option for the LPG."""
    assert LPG_DATE_FORMAT == "%Y-%m-%d"


@pytest.mark.base
@pytest.mark.parametrize(
    "days, expected_end",
    [
        (1, "2021-01-02"),  # the one-day runs used by the system-setup tests
        (11, "2021-01-12"),  # was "12-01-2021" -> silently read as 1 December
        (13, "2021-01-14"),  # was "14-01-2021" -> aborted the LPG outright
        (30, "2021-01-31"),  # was "31-01-2021" -> aborted the LPG outright
        (31, "2021-02-01"),  # was "01-02-2021" -> silently read as 2 January
        (32, "2021-02-02"),  # unambiguous even under the old format
        (365, "2022-01-01"),  # full year: the case that always worked
    ],
)
def test_end_date_is_written_unambiguously(days: int, expected_end: str) -> None:
    """Every span produces the intended end date, including the ones that used to break."""
    start, end = compute_lpg_start_and_end_date(_simulation_parameters(days))
    assert start == "2021-01-01"
    assert end == expected_end


@pytest.mark.base
def test_dates_round_trip_for_every_span_of_a_year() -> None:
    """Both strings parse back to the intended dates for every simulation length up to a year.

    ``date.fromisoformat`` is strict about ISO 8601, so a string it accepts cannot be reread as a
    different date -- which is exactly the property the old ``%d-%m-%Y`` format lacked.
    """
    january_first = datetime.date(2021, 1, 1)
    for days in range(1, 366):
        start, end = compute_lpg_start_and_end_date(_simulation_parameters(days))
        assert datetime.date.fromisoformat(start) == january_first
        assert datetime.date.fromisoformat(end) == january_first + datetime.timedelta(days=days)


@pytest.mark.base
def test_range_always_starts_on_first_of_january_of_the_simulation_year() -> None:
    """The LPG range starts on 1 January regardless of when the HiSim simulation starts."""
    start_date = datetime.datetime(2023, 6, 15)
    parameters = SimulationParameters(start_date, start_date + datetime.timedelta(days=20), 900)
    start, end = compute_lpg_start_and_end_date(parameters)
    assert start == "2023-01-01"
    assert end == "2023-01-21"


@pytest.mark.base
def test_end_date_covers_the_whole_simulated_span() -> None:
    """The requested range must never be shorter than what HiSim will read back.

    The LPG includes the end day, so the request covers one day more than the simulation needs.
    """
    for days in (1, 11, 31, 200, 365):
        _, end = compute_lpg_start_and_end_date(_simulation_parameters(days))
        covered_days = (datetime.date.fromisoformat(end) - datetime.date(2021, 1, 1)).days + 1
        assert covered_days > days
