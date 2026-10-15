"""Converts the LoadProfileGenerator's local-time minute profiles to the UTC minute series HiSim simulates.

The LoadProfileGenerator reports every profile per minute in local time and without daylight-saving
shifts: every day of its year is 1440 gapless minutes (525 600 minutes in a 365-day year). HiSim's
occupancy and its cars run on the same UTC-based minute series, so the conversion here drops the hour
that does not exist in spring, repeats the one that exists twice in autumn and moves the profile by the
winter offset of one hour.

The function lives in a module of its own, rather than in :mod:`hisim.utils`, because the car's cached
profile is computed with it: the car's producer imports it, so the cache key fingerprints this module
and an edit here makes every cached car profile a miss. :mod:`hisim.utils` imports the simulation
parameters and the cache client, which a producer's import closure must not contain. This module imports
pandas, pytz and the standard library only.
"""

import datetime as dt

import pandas as pd
import pytz

__authors__ = "Noah Pflugradt, Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021-2022, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


def convert_lpg_data_to_utc(data: pd.DataFrame, year: int) -> pd.DataFrame:
    """Transform LPG data from local time (not having explicit time shifts) to UTC.

    Args:
        data: DataFrame with a ``Time`` column of local-time strings.
        year: simulation year for DST lookup.

    Returns:
        pd.DataFrame: data with reformatted UTC ``Time`` column.

    Note:
        The input DataFrame is changed in place -- its index becomes the parsed times, and the
        nonexistent spring hour is dropped from it -- but the result is a new DataFrame. Use the
        returned object, never the input, after the call.
    """
    # convert Time information to pandas datetime and make it to index
    data.index = pd.DatetimeIndex(pd.to_datetime(data["Time"]))
    lastdate = data.index[-1]

    # find out time shifts of selected year
    timeshifts = pytz.timezone("Europe/Berlin")._utc_transition_times  # type: ignore # pylint: disable=W0212
    timeshifts = [elem for elem in timeshifts if elem.year == year]

    # delete hour in spring if neceary:
    if lastdate > timeshifts[0]:
        indices_of_additional_hour_in_spring = data.loc[
            timeshifts[0]
            + dt.timedelta(seconds=3600) : timeshifts[0]  # noqa: E203
            + dt.timedelta(seconds=60 * (60 + 59))
        ].index

        data.drop(index=indices_of_additional_hour_in_spring, inplace=True)

    # add hour in autumn if necesary
    if lastdate > timeshifts[1]:
        additional_hours_in_autumn = data.loc[
            timeshifts[1]
            + dt.timedelta(seconds=3600) : timeshifts[1]  # noqa: E203
            + dt.timedelta(seconds=60 * (60 + 59))
        ]
        data = pd.concat([data, additional_hours_in_autumn])
        data.sort_index(inplace=True)

    # delete hour at beginning
    data = data[data.index >= dt.datetime(year=year, month=1, day=1, hour=1)]

    # add hour at end
    last_hour = data[
        data.index >= dt.datetime(year=year, month=lastdate.month, day=lastdate.day, hour=23)  # type: ignore
    ]
    data = pd.concat([data, last_hour])  # type: ignore

    # make integer index again, paste new timestamp (UTC) and format
    data.index = pd.Index(list(range(len(data))))
    data["Time"] = pd.date_range(
        start=dt.datetime(year=year, month=1, day=1, hour=0),
        end=dt.datetime(year=year, month=lastdate.month, day=lastdate.day, hour=23, minute=59),  # type: ignore
        freq="min",
        tz="UTC",
    )
    data["Time"] = data["Time"].dt.strftime("%m/%d/%Y %H:%M")
    return data
