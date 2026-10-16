"""Produces the sun's position over a solar-thermal collector at every timestep of the run.

What this module produces is one artifact: two series, one value per simulated timestep -- the sun's
apparent zenith and its azimuth, in degrees, at the collector's coordinates. It is the only stage of the
collector calculation that can be worked out before the run: the irradiance on the collector plane needs
the weather, which arrives through wired inputs, and the collector's efficiency needs the storage's
inlet temperature, which is the simulation's own state feeding back.

It is a module of its own, like the weather, PV and solar-gains producers, so that the cache key of the
artifact carries a fingerprint of exactly the code that computes it: the key is
``sha256(artifact kind : code fingerprint : third-party fingerprint : DTO JSON)``
(:class:`hisim.caching.keys.CacheKey`). The third-party fingerprint pins pvlib's version, so a pvlib
upgrade -- which can move the sun -- is a cache miss. The legacy key it replaces hashed the collector's
configuration and the simulation parameters only, and saw neither the code nor the library.

**The layering rule.** This module may import pandas and pvlib; it may not import the component base
class, the simulator or a repository (:class:`hisim.caching.keys.ProducerLayering`, checked by
``tests/test_solar_thermal_sun_position_producer.py``).
"""

import datetime
from dataclasses import dataclass
from typing import ClassVar, Tuple

import pandas as pd
import pvlib

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


@dataclass(frozen=True)
class SunPositionInputs:
    """Everything :func:`produce_sun_position` depends on, and nothing else: where, from when, how often.

    Every field is key material. The collector's area, its optics and its costs are not fields, so two
    collectors at the same place in the same run share one entry.
    """

    #: The name this producer files its artifact under: the ``{component}`` half of the cache key and
    #: the prefix of the entry's filename.
    ARTIFACT_KIND: ClassVar[str] = "solar_thermal_sun_position"

    #: The columns of the produced frame and of the cached CSV, in this order.
    COLUMNS: ClassVar[Tuple[str, str]] = ("apparent_zenith", "azimuth")

    #: Latitude of the collector in degrees.
    latitude_in_degrees: float

    #: Longitude of the collector in degrees.
    longitude_in_degrees: float

    #: The first instant of the run, ISO 8601 without a time zone, for example
    #: ``"2021-01-01T00:00:00"``. Every timestep's instant counts from it.
    window_start: str

    #: The simulation's timestep in seconds.
    seconds_per_timestep: int

    #: How many values each produced series has.
    timesteps: int

    def timestamps(self) -> pd.DatetimeIndex:
        """Return the instant of every timestep of the run, in simulation order.

        Example: a run from ``2021-01-01T00:00:00`` at 900 s has the instants 00:00, 00:15, 00:30 and
        so on. They carry no time zone, and pvlib reads instants without one as UTC.

        Returns:
            pd.DatetimeIndex: one instant per timestep.
        """
        return pd.date_range(
            start=datetime.datetime.fromisoformat(self.window_start),
            periods=self.timesteps,
            freq=f"{self.seconds_per_timestep}s",
        )


def produce_sun_position(inputs: SunPositionInputs) -> pd.DataFrame:
    """Produce the sun's apparent zenith and azimuth at every timestep of the run.

    Args:
        inputs: the calculation's inputs.

    Returns:
        pd.DataFrame: the columns of :attr:`SunPositionInputs.COLUMNS`, one row per timestep, indexed by
            the timestep's instant.
    """
    solar_position: pd.DataFrame = pvlib.solarposition.get_solarposition(
        time=inputs.timestamps(),
        latitude=inputs.latitude_in_degrees,
        longitude=inputs.longitude_in_degrees,
    )[list(SunPositionInputs.COLUMNS)]
    return solar_position
