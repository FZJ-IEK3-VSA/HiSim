"""How a day of a run splits into timesteps: the one exact number of timesteps per day.

A run's window starts at midnight and covers whole days (``SimulationParameters.validated_window``),
and its step length divides a day, so every day of the run is the same whole number of timesteps
and starts a timestep. :class:`SimulationDay` holds that rule and the number it gives. The window
contract refuses a step that breaks it; the single-day chart slices a day by the number; the
weather producer averages the outside temperature over blocks of that many timesteps.

The module imports the standard library only. The weather producer imports it, and every module a
producer imports is part of its cache key (``hisim.caching.keys.ImportClosure``), so it must stay
small and must not pull in the simulation parameters or anything else that changes often.
"""

import datetime
from typing import ClassVar


class SimulationDay:
    """The day a run is made of, and how many timesteps of a given length fit into it.

    Example: at 900 s a day is 96 timesteps, at 7200 s it is 12; 57 600 s (16 h) does not divide a
    day and has no number of timesteps per day. The arithmetic is exact: it is done on
    ``datetime.timedelta`` values, which count whole microseconds, so a float step such as 900.0
    gives the same answer as 900.
    """

    #: The length of a day of a run, in seconds.
    SECONDS_PER_DAY: ClassVar[int] = 86400

    @classmethod
    def divides_a_day(cls, seconds_per_timestep: float) -> bool:
        """Return whether a day is a whole number of timesteps of this length.

        Example: ``divides_a_day(640)`` is true (135 timesteps), ``divides_a_day(57600)`` is false,
        and so is any step longer than a day.

        Args:
            seconds_per_timestep: The step length, positive and finite, in seconds.

        Returns:
            True if 86 400 s are a whole number of such steps.
        """
        one_day = datetime.timedelta(seconds=cls.SECONDS_PER_DAY)
        return one_day % datetime.timedelta(seconds=float(seconds_per_timestep)) == datetime.timedelta(0)

    @classmethod
    def timesteps_per_day(cls, seconds_per_timestep: float) -> int:
        """Return the exact number of timesteps of this length in one day.

        Example: 96 at 900 s, 24 at 3600 s, 1 at 86 400 s.

        Args:
            seconds_per_timestep: The step length, positive and finite, in seconds.

        Returns:
            The number of timesteps per day, at least 1.

        Raises:
            ValueError: If the step does not divide 86 400 s, so that a day is not a whole number of
                timesteps. The window contract refuses such a step before a run starts.
        """
        if not cls.divides_a_day(seconds_per_timestep):
            raise ValueError(
                f"A timestep of {seconds_per_timestep} s does not divide the {cls.SECONDS_PER_DAY} s of a day, "
                f"so a day of the run is not a whole number of timesteps; use a step length that divides "
                f"{cls.SECONDS_PER_DAY} s."
            )
        one_day = datetime.timedelta(seconds=cls.SECONDS_PER_DAY)
        return one_day // datetime.timedelta(seconds=float(seconds_per_timestep))
