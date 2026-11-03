"""The quantities Section 7.3 of ANSI/ASHRAE 140-2023 asks for, from the hours of a run (FR9, FR10).

Every quantity comes from the hourly aggregate (``aggregate``), as Section 3 defines them: the annual
loads are the sums of the hourly loads, a peak is the largest hourly load, the free-float statistics
are the mean, minimum and maximum of the hourly zone air temperature. A time is reported as month,
day and hour, Hour 1 running from 00:00 to 01:00 (Table 7-47 note a), so a row labelled with the start
of its hour is that label's hour plus one. The zone air temperature is the air node θ_air of the 5R1C
model (Section 3, 7.3.6); the operative and the mass temperature are HiSim's own diagnostics. The
largest single time step of a sub-hourly run is kept for information only (FR10).
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from hisim.verification import aggregate
from hisim.verification.suite import RequestedOutputs

#: The runner's columns this module reads.
AIR_TEMPERATURE_COLUMN = "indoor_air_temperature_in_celsius"
OPERATIVE_TEMPERATURE_COLUMN = "operative_temperature_in_celsius"
MASS_TEMPERATURE_COLUMN = "thermal_mass_temperature_in_celsius"
SOLAR_GAIN_COLUMN = "solar_gain_through_windows_in_watt"

#: The summary's names of the hourly quantities of Table 7-47, with their units.
DAILY_KEYS = {"load": "load_in_kilowatt_hour", "zone_air_temperature": "zone_air_temperature_in_celsius"}

#: How the hours were formed, stated in every summary (FR10).
AGGREGATION = "hourly mean of the time steps; the delivered power split into heating and sensible cooling before the mean; Hour 1 is 00:00 to 01:00"


def occurrence(series: pd.Series, largest: bool) -> Dict[str, Any]:
    """The largest or smallest value of an hourly series with its month, day and hour (Hour 1 = 00:00 to 01:00)."""
    label = pd.Timestamp(series.idxmax() if largest else series.idxmin())
    return {"value": float(series[label]), "at": str(label), "month": label.month, "day": label.day, "hour": label.hour + 1}


def peak_in_kilowatt(power_in_watt: pd.Series) -> Optional[Dict[str, Any]]:
    """The largest hourly load in kW with its time, or None when there is no load in the series."""
    if not power_in_watt.max() > 0.0:
        return None
    return occurrence(power_in_watt / 1000.0, largest=True)


def temperature_statistics(series: pd.Series) -> Dict[str, Any]:
    """Mean, minimum and maximum of an hourly temperature, the extremes with their times."""
    return {"mean": float(series.mean()), "min": occurrence(series, largest=False), "max": occurrence(series, largest=True)}


def loads(hourly: pd.DataFrame) -> Dict[str, Any]:
    """Annual and monthly heating and sensible cooling loads with their peaks (7.3.1, 7.3.2.4)."""
    heating = hourly[aggregate.HEATING_POWER_COLUMN]
    cooling = hourly[aggregate.COOLING_POWER_COLUMN]
    monthly: List[Dict[str, Any]] = []
    for month, hours in hourly.groupby(pd.DatetimeIndex(hourly.index).month):
        monthly.append(
            {
                "month": int(month),
                "heating_in_kilowatt_hour": float(hours[aggregate.HEATING_POWER_COLUMN].sum() / 1000.0),
                "sensible_cooling_in_kilowatt_hour": float(hours[aggregate.COOLING_POWER_COLUMN].sum() / 1000.0),
                "peak_heating_in_kilowatt": peak_in_kilowatt(hours[aggregate.HEATING_POWER_COLUMN]),
                "peak_sensible_cooling_in_kilowatt": peak_in_kilowatt(hours[aggregate.COOLING_POWER_COLUMN]),
            }
        )
    return {
        "annual_heating_in_megawatt_hour": float(heating.sum() / 1e6),
        "annual_sensible_cooling_in_megawatt_hour": float(cooling.sum() / 1e6),
        "annual_peak_heating_in_kilowatt": peak_in_kilowatt(heating),
        "annual_peak_sensible_cooling_in_kilowatt": peak_in_kilowatt(cooling),
        "monthly": monthly,
    }


def bin_frequencies(series: pd.Series, lowest_in_celsius: int, highest_in_celsius: int) -> Dict[str, Any]:
    """Hours per 1 K bin of an hourly temperature, a bin named by its lower bound: 20 holds 20 <= T < 21 (7.3.7.1.1)."""
    lower_bounds = np.floor(series.to_numpy()).astype(int)
    hours_by_bin = {str(bound): int((lower_bounds == bound).sum()) for bound in range(lowest_in_celsius, highest_in_celsius + 1)}
    return {
        "lowest_in_celsius": lowest_in_celsius,
        "highest_in_celsius": highest_in_celsius,
        "hours_by_bin": hours_by_bin,
        "hours_below": int((lower_bounds < lowest_in_celsius).sum()),
        "hours_above": int((lower_bounds > highest_in_celsius).sum()),
    }


def day_of(hourly: pd.DataFrame, day: str) -> pd.DataFrame:
    """The 24 hours of a day written MM-DD."""
    month, day_of_month = (int(part) for part in day.split("-"))
    index = pd.DatetimeIndex(hourly.index)
    hours = hourly[(index.month == month) & (index.day == day_of_month)]
    if len(hours) != 24:
        raise ValueError(f"the day {day} has {len(hours)} hours in the run, not 24")
    return hours


def daily_hourly(hourly: pd.DataFrame, quantity: str, day: str) -> List[float]:
    """The 24 hourly values of Table 7-47 on a day: the load in kWh (cooling negative) or the zone air temperature."""
    hours = day_of(hourly, day)
    if quantity == "load":
        load = (hours[aggregate.HEATING_POWER_COLUMN] - hours[aggregate.COOLING_POWER_COLUMN]) / 1000.0
        return [float(value) for value in load]
    return [float(value) for value in hours[AIR_TEMPERATURE_COLUMN]]


def case_metrics(frame: pd.DataFrame, seconds_per_timestep: int, requested: Optional[RequestedOutputs] = None) -> Dict[str, Any]:
    """Everything Section 7.3 asks of one case at one time step, plus HiSim's diagnostics, as plain data.

    Args:
        frame: the run's time series (the reported pass).
        seconds_per_timestep: the run's time step.
        requested: the hourly days and the temperature bins the suite asks of this case.

    Returns:
        Dict[str, Any]: the loads (not for a free-float case), the temperatures, the solar gain, and
            what ``requested`` names.
    """
    hourly = aggregate.to_hourly(frame, seconds_per_timestep)
    metrics: Dict[str, Any] = {
        "seconds_per_timestep": seconds_per_timestep,
        "timesteps": int(len(frame)),
        "hours": int(len(hourly)),
        "aggregation": AGGREGATION,
    }
    if aggregate.HEATING_POWER_COLUMN in hourly:
        metrics.update(loads(hourly))
        delivered = frame[aggregate.DELIVERED_POWER_COLUMN]
        metrics["largest_time_step_heating_in_kilowatt"] = float(delivered.clip(lower=0.0).max() / 1000.0)
        metrics["largest_time_step_sensible_cooling_in_kilowatt"] = float((-delivered).clip(lower=0.0).max() / 1000.0)
    metrics["zone_air_temperature_in_celsius"] = temperature_statistics(hourly[AIR_TEMPERATURE_COLUMN])
    metrics["operative_temperature_in_celsius"] = temperature_statistics(hourly[OPERATIVE_TEMPERATURE_COLUMN])
    metrics["thermal_mass_temperature_in_celsius"] = temperature_statistics(hourly[MASS_TEMPERATURE_COLUMN])
    metrics["solar_gain_through_windows_in_kilowatt_hour"] = float(hourly[SOLAR_GAIN_COLUMN].sum() / 1000.0)
    if requested is not None:
        if requested.temperature_bins_in_celsius is not None:
            lowest, highest = requested.temperature_bins_in_celsius
            metrics["zone_air_temperature_bins"] = bin_frequencies(hourly[AIR_TEMPERATURE_COLUMN], lowest, highest)
        daily: Dict[str, Dict[str, List[float]]] = {}
        for quantity, days in requested.daily_hourly:
            for day in days:
                daily.setdefault(day, {})[DAILY_KEYS[quantity]] = daily_hourly(hourly, quantity, day)
        if daily:
            metrics["daily_hourly"] = daily
    return metrics
