"""Produces the car's driving profile at the run's resolution: where the car is and how far it drove.

What this module produces is one artifact: two series, one value per simulated timestep -- the car's
location as a number (0 away, 1 at home, 2 at the workplace) and the metres it drove in the timestep --
computed from the minute profile the occupancy publishes for the car. The LoadProfileGenerator reports
that profile per minute in local time, so the calculation converts it to UTC and then aggregates it to
the run's timestep: distances are summed, and the location is the most frequent one of the minutes in
the timestep.

It is a module of its own, like the weather, PV and solar-gains producers, so that the cache key of the
artifact carries a fingerprint of exactly the code that computes it: the key is
``sha256(artifact kind : code fingerprint : third-party fingerprint : DTO JSON)``
(:class:`hisim.caching.keys.CacheKey`), and the code fingerprint covers this module and the LPG time
conversion it imports, so an edit to either makes every cached car profile a miss. The legacy key it
replaces hashed the car's configuration and the simulation parameters only, so a changed conversion
kept serving the profiles the old one had written.

**The profile as data, identified by its hash.** The minute profile is a LoadProfileGenerator result
that no configuration determines, so it enters the key as :attr:`CarProfileInputs.profile_content_hash`,
a hash of the profile itself, while the profile travels as payload. Two cars driving the same profile
share one entry; a profile that differs in a single minute is a different entry.

**The layering rule.** This module may import pandas, numpy and the LPG time conversion; it may not
import the component base class, the simulator or a repository
(:class:`hisim.caching.keys.ProducerLayering`, checked by ``tests/test_car_profile_producer.py``).
"""

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from hisim.caching.keys import KeyMaterial
from hisim.components import lpg_local_time

__authors__ = "Johanna Ganglbauer, Noah Pflugradt"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Johanna Ganglbauer"
__status__ = "development"


@dataclass(frozen=True)
class CarProfileInputs:
    """Everything :func:`produce_car_profile` depends on, and nothing else.

    Key material: the hash of the minute profile, its resolution and the run's span and timestep.
    Payload, excluded from the key by :attr:`hisim.caching.keys.KeyMaterial.PAYLOAD`: the two minute
    series themselves, which the hash identifies.
    """

    #: The name this producer files its artifact under: the ``{component}`` half of the cache key and
    #: the prefix of the entry's filename.
    ARTIFACT_KIND: ClassVar[str] = "car_profile"

    #: The columns of the produced frame and of the cached CSV, in this order.
    LOCATION_COLUMN: ClassVar[str] = "car_location"
    DISTANCE_COLUMN: ClassVar[str] = "meters_driven"

    #: The number each location of the LoadProfileGenerator stands for in the produced series: 1 at
    #: home, 2 at the workplace and 0 anywhere else, a missing location included.
    LOCATION_NUMBERS: ClassVar[Mapping[Optional[str], int]] = {
        "School": 0,
        "Event Location": 0,
        "Shopping": 0,
        None: 0,
        "Home": 1,
        "Workplace": 2,
    }

    #: :meth:`content_hash` of the minute profile. This is what identifies the profile in the key.
    profile_content_hash: str

    #: The resolution of the minute profile as the LoadProfileGenerator states it, ``"HH:MM:SS"``.
    lpg_time_resolution: str

    #: The simulated year. The conversion to UTC looks up that year's daylight-saving switches.
    year: int

    #: The simulation's timestep in seconds; the minute profile is aggregated to it.
    seconds_per_timestep: int

    #: How many values each produced series has.
    timesteps: int

    #: The simulated span in whole days. The profile is cut to this many days before the conversion.
    duration_in_days: int

    #: Where the car is in each minute of the profile, as the LoadProfileGenerator names it. Payload.
    car_location: Tuple[Optional[str], ...] = field(metadata=KeyMaterial.PAYLOAD)

    #: The metres the car drove in each minute of the profile. Payload.
    driven_distance_per_minute_in_meters: Tuple[float, ...] = field(metadata=KeyMaterial.PAYLOAD)

    @staticmethod
    def content_hash(
        lpg_time_resolution: str,
        car_location: Sequence[Optional[str]],
        driven_distance_per_minute_in_meters: Sequence[float],
    ) -> str:
        """Hash a minute profile, so that the key names the profile without containing it.

        Example: two cars of two households whose profiles agree in every minute get the same hash;
        a profile with one different minute gets another one.

        Args:
            lpg_time_resolution: the profile's resolution, ``"HH:MM:SS"``.
            car_location: the location in each minute.
            driven_distance_per_minute_in_meters: the distance driven in each minute.

        Returns:
            str: a sha256 hex digest over the three, in JSON.
        """
        rendered = json.dumps(
            [
                lpg_time_resolution,
                [None if location is None else str(location) for location in car_location],
                [float(distance) for distance in driven_distance_per_minute_in_meters],
            ],
            separators=(",", ":"),
        )
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def produce_car_profile(inputs: CarProfileInputs) -> pd.DataFrame:
    """Produce the car's location and driven distance per timestep of the run.

    The minute profile is cut to the run's span, stamped with the minutes of that span from 1 January
    of the simulated year, converted to UTC and aggregated to the run's timestep: the distances of a
    timestep are summed and its location is the most frequent of its minutes.

    Args:
        inputs: the calculation's inputs.

    Returns:
        pd.DataFrame: the columns :attr:`CarProfileInputs.LOCATION_COLUMN` and
            :attr:`CarProfileInputs.DISTANCE_COLUMN`, one row per timestep.
    """
    time_resolution_original = dt.datetime.strptime(inputs.lpg_time_resolution, "%H:%M:%S")
    seconds_per_timestep_original = (
        time_resolution_original.hour * 3600 + time_resolution_original.minute * 60 + time_resolution_original.second
    )
    minutes_per_timestep = int(inputs.seconds_per_timestep / seconds_per_timestep_original)
    steps_desired_in_minutes = inputs.timesteps * minutes_per_timestep

    # extract values for location and distance of car, include time information and translate the car
    # location to integers
    initial_data = pd.DataFrame(
        {
            "Time": pd.date_range(
                start=dt.datetime(year=inputs.year, month=1, day=1),
                end=dt.datetime(year=inputs.year, month=1, day=1)
                + dt.timedelta(days=inputs.duration_in_days)
                - dt.timedelta(seconds=60),
                freq="min",
            ),
            CarProfileInputs.DISTANCE_COLUMN: list(
                inputs.driven_distance_per_minute_in_meters[:steps_desired_in_minutes]
            ),
            CarProfileInputs.LOCATION_COLUMN: [
                CarProfileInputs.LOCATION_NUMBERS[location] for location in inputs.car_location
            ][:steps_desired_in_minutes],
        }
    )
    initial_data = lpg_local_time.convert_lpg_data_to_utc(data=initial_data, year=inputs.year)
    meters_driven: Any = pd.to_numeric(initial_data[CarProfileInputs.DISTANCE_COLUMN]).tolist()
    car_location: List[Any] = pd.to_numeric(initial_data[CarProfileInputs.LOCATION_COLUMN]).tolist()

    # sum / extract the most common value to match the run's time resolution
    if minutes_per_timestep > 1:
        meters_driven = resample_meters_driven(
            meters_driven=meters_driven, seconds_per_timestep=inputs.seconds_per_timestep
        )
        car_location = [
            most_frequent(input_list=car_location[i * minutes_per_timestep: (i + 1) * minutes_per_timestep])
            for i in range(inputs.timesteps)
        ]
    columns: Dict[str, Any] = {
        CarProfileInputs.LOCATION_COLUMN: car_location,
        CarProfileInputs.DISTANCE_COLUMN: meters_driven,
    }
    return pd.DataFrame(columns)


def resample_meters_driven(meters_driven: List, seconds_per_timestep: int) -> Any:
    """Sum the minute distances over each timestep of the run.

    Example: at a timestep of 900 s, every 15 minute values become one value, their sum. Minutes that
    do not fill a last whole timestep are dropped.

    Args:
        meters_driven: the distance driven in each minute, in metres.
        seconds_per_timestep: the run's timestep in seconds.

    Returns:
        Any: a numpy array with the distance driven in each timestep, in metres.
    """
    # Convert seconds per timestep to minutes per timestep
    minutes_per_timestep = seconds_per_timestep // 60

    # Check the length of the input list
    total_minutes = len(meters_driven)

    # Calculate the number of complete timesteps
    num_timesteps = total_minutes // minutes_per_timestep

    # Trim the list to be a multiple of minutes_per_timestep
    trimmed_meters_driven = meters_driven[: num_timesteps * minutes_per_timestep]

    # Reshape and sum the data
    reshaped_meters: np.ndarray = np.reshape(trimmed_meters_driven, (num_timesteps, minutes_per_timestep))
    resampled_meters = np.sum(reshaped_meters, axis=1)
    return resampled_meters


def most_frequent(input_list: List) -> Any:
    """Return the most frequent value of a list, the first of them on a tie.

    It down-samples the location from one-minute resolution to the run's: a timestep's location is
    where the car was for most of its minutes.

    Args:
        input_list: the values of one timestep's minutes.

    Returns:
        Any: the most frequent value.
    """
    max_count = 0
    most_frequent_value = input_list[0]

    for value in input_list:
        curr_frequency = input_list.count(value)
        if curr_frequency > max_count:
            max_count = curr_frequency
            most_frequent_value = value
    return most_frequent_value
