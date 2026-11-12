"""The hours the standard reports, from the time steps of a run (FR10).

ANSI/ASHRAE 140-2023 reports hourly integrated values (Section 3; Table 7-47 note a: Hour 1 runs from
00:00 to 01:00). A run at 900 s or 60 s is brought to that resolution by the mean over each hour's
time steps, for every column. The delivered power is split by its sign *before* the mean into the
heating and the sensible cooling power, so that heating and cooling within one hour do not cancel:
Section 3 defines the two loads as separate quantities. The hourly mean power in W is the hour's
energy in Wh. At 3600 s the hours are the time steps themselves and only the split is made.
"""

import pandas as pd

#: The runner's column of the power the ideal heater delivers: positive heats, negative cools.
DELIVERED_POWER_COLUMN = "thermal_power_delivered_in_watt"

#: The hourly heating and sensible cooling power, both positive.
HEATING_POWER_COLUMN = "heating_power_in_watt"
COOLING_POWER_COLUMN = "sensible_cooling_power_in_watt"

SECONDS_PER_HOUR = 3600


def to_hourly(frame: pd.DataFrame, seconds_per_timestep: int) -> pd.DataFrame:
    """Average a run's time series over each hour, with the delivered power split into heating and cooling.

    Args:
        frame: the run's time series, indexed by the start of each time step.
        seconds_per_timestep: the run's time step; it must divide the hour.

    Returns:
        pd.DataFrame: one row per hour, labelled with the hour's start; the delivered-power column is
            replaced by the heating and the sensible cooling power.

    Raises:
        ValueError: if the time step does not divide the hour, or an hour is not complete.
    """
    if seconds_per_timestep <= 0 or SECONDS_PER_HOUR % seconds_per_timestep:
        raise ValueError(f"a time step of {seconds_per_timestep} s does not divide the hour")
    steps_per_hour = SECONDS_PER_HOUR // seconds_per_timestep
    split = frame.copy()
    if DELIVERED_POWER_COLUMN in split:
        delivered = split.pop(DELIVERED_POWER_COLUMN)
        split[HEATING_POWER_COLUMN] = delivered.clip(lower=0.0)
        split[COOLING_POWER_COLUMN] = (-delivered).clip(lower=0.0)
    hours = pd.DatetimeIndex(split.index).floor("h")
    steps = split.groupby(hours).size()
    if (steps != steps_per_hour).any():
        incomplete = steps[steps != steps_per_hour].index[0]
        raise ValueError(f"the hour from {incomplete} has {int(steps[incomplete])} time steps, not {steps_per_hour}")
    hourly = split.groupby(hours).mean()
    hourly.index.name = frame.index.name
    return hourly
