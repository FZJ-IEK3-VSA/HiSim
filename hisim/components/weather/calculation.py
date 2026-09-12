"""Produces the processed weather series that :mod:`hisim.components.weather` simulates from.

What this module produces is one artifact: the full-year frame of twelve columns -- irradiances, air
temperature, sun position, wind speed, pressure -- read from a weather data file, interpolated to one
minute, aggregated to the simulation's timestep and handed back as a :class:`pandas.DataFrame`. It is the
first producer under ``roadmap/cache_service_spec.md`` (§3, §12), which is why it is a module of its own
rather than a section of the component.

The reason is the #628 cache finding. A fix to
:func:`calculate_direct_normal_irradiance_in_watt_per_square_meter` changed 33 KPIs locally and not a
single one in CI: the processed frame, the corrected DNI included, was cached under
``sha256(config JSON + simulation-parameter key)``, CI restores the cache directory from the previous
run, and a key that says nothing about the code kept serving the old numbers to all 24 golden pairs. The
answer the spec chose is not a version constant an author has to remember to bump but a key that carries
a fingerprint of the producer's own source and of its import closure, so that any edit to the code that
computes the frame changes the key by itself. That only works if the closure is small, which is what the
layering rule below buys.

**The DTO contract.** :func:`produce_weather_series` takes exactly one argument, the frozen
:class:`WeatherSeriesInputs`, and is a pure function of it: everything the calculation depends on is a
field, and nothing else is. The data file is identified by the hash of its contents rather than by its
path, so editing a bundled weather file changes the key and moving a checkout does not; the path travels
as a payload field that is excluded from the key material (see :class:`hisim.caching.keys.KeyMaterial`),
paired with the hash that identifies what it points at. Cost, CO2, display and identity fields of
``WeatherConfig`` are not fields here, so renaming a component or repricing a fuel no longer invalidates
a physics result.

**The layering rule.** This module may import numpy, pandas, pvlib and plain values; it may not import
the component base class, the simulator or a repository (:class:`hisim.caching.keys.ProducerLayering`
checks it, and ``tests/test_weather_producer.py`` runs the check). Two reasons: the producer stays
callable without a running HiSim, so the prewarm CLI of the spec can fill the cache without simulating;
and every module in the closure is hashed into the key, so importing a frequently edited registry like
``loadtypes`` would throw the weather's cache away on every enum addition. Anything a calculation needs
from the component's world is passed as a plain value through the DTO instead -- which is why
:class:`WeatherDataSourceEnum` lives here and is re-exported by the component.
"""

# clean

import csv
import datetime
import hashlib
import math
import os
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd
import pvlib

from hisim.caching.keys import KeyMaterial

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Noah Pflugradt"

""" The functions in this module are at some degree based on the tsib project:

[tsib-kotzur]: Kotzur, Leander, Detlef Stolten, and Hermann-Josef Wagner. Future grid load of the residential building sector.
No. RWTH-2018-231872. Lehrstuhl für Brennstoffzellen (FZ Jülich), 2019.
ID: http://hdl.handle.net/2128/21115
    http://nbn-resolving.org/resolver?verb=redirect&identifier=urn:nbn:de:0001-2019020614

The implementation of the tsib project can be found under the following repository:
https://github.com/FZJ-IEK3-VSA/tsib
"""

#: The name this producer files its artifact under: the ``{component}`` half of the cache key and the
#: prefix of the entry's filename. It names the calculation, not the component instance, so two
#: differently named weather components that read the same file share one entry.
ARTIFACT_KIND: str = "weather_series"


@unique
class WeatherDataSourceEnum(str, Enum):
    """Describes where the weather data is from. Used to choose the correct reading function.

    Every member carries its own name as its value so that a serialized config
    reads ``"DWD_TRY"`` instead of an opaque integer code. Only the member
    identity matters at runtime; nothing in the weather module depends on an
    ordinal.

    It lives in the producer module because the reader choice is part of the calculation and its value
    is key material; ``hisim.components.weather`` re-exports it, so ``weather.WeatherDataSourceEnum``
    keeps working for configurations and system setups.
    """

    DWD_TRY = "DWD_TRY"
    NSRDB = "NSRDB"
    NSRDB_15MIN = "NSRDB_15MIN"
    DWD_10MIN = "DWD_10MIN"
    ERA5 = "ERA5"
    DWD_15MIN = "DWD_15MIN"


class WeatherSourceFiles:
    """Which files on disk one configured source path stands for, and what they contain.

    A weather configuration names a source by path, and depending on the data source that path is
    either a file or a stem two files share (the DWD test reference years ship an ``.dat`` and,
    where one has been generated, a ``.csv`` beside it). The cache key must not contain the path --
    it differs between checkouts, containers and the cluster, and a shared cache would then never
    hit -- so what enters the key is :meth:`content_hash`, a hash of the bytes of every file the
    readers may consult. Editing a bundled data file therefore changes every key that depends on it,
    with no version discipline anywhere (spec §3.1).
    """

    #: The suffixes appended to a configured source path, per data source, in the order they are
    #: hashed. The empty string means the path is the file itself.
    SUFFIXES: ClassVar[Mapping[WeatherDataSourceEnum, Tuple[str, ...]]] = {
        WeatherDataSourceEnum.DWD_TRY: (".dat", ".csv"),
        WeatherDataSourceEnum.NSRDB: (".dat",),
        WeatherDataSourceEnum.NSRDB_15MIN: ("",),
        WeatherDataSourceEnum.DWD_10MIN: ("",),
        WeatherDataSourceEnum.DWD_15MIN: ("",),
        WeatherDataSourceEnum.ERA5: ("",),
    }

    #: What a suffix contributes when no file of that name exists. A ``.csv`` beside a DWD test
    #: reference year is optional and changes which branch of the reader runs, so its absence has to
    #: be part of the hash rather than silently skipped.
    ABSENT: ClassVar[str] = "absent"

    #: How much of a file is read at a time. Weather files are a few hundred kilobytes today, but a
    #: chunked read costs nothing and keeps a large one from being held in memory twice.
    BLOCK_SIZE: ClassVar[int] = 1 << 20

    @classmethod
    def candidates(cls, data_source: WeatherDataSourceEnum, source_path: str) -> Tuple[Tuple[str, str], ...]:
        """Return the ``(suffix, path)`` pairs the readers of this data source may open.

        Args:
            data_source: the data source the configuration names.
            source_path: the configured path, with or without an extension depending on the source.

        Returns:
            Tuple[Tuple[str, str], ...]: one pair per suffix, in hashing order.

        Raises:
            ValueError: if the data source has no entry in :attr:`SUFFIXES`, which means a member was
                added to the enum without saying which files it reads.
        """
        if data_source not in cls.SUFFIXES:
            raise ValueError(
                f"The weather data source {data_source!r} does not say which files it reads, so its "
                f"content hash cannot be computed; add it to {cls.__name__}.SUFFIXES."
            )
        return tuple((suffix, source_path + suffix) for suffix in cls.SUFFIXES[data_source])

    @classmethod
    def content_hash(cls, data_source: WeatherDataSourceEnum, source_path: str) -> str:
        """Hash the contents of every file this source path stands for.

        Args:
            data_source: the data source the configuration names.
            source_path: the configured path.

        Returns:
            str: a sha256 hex digest over the (suffix, contents) pairs, absent files included by name.

        Raises:
            FileNotFoundError: if none of the candidate files exists. The readers would fail a moment
                later anyway, and hashing nothing would give every unreadable configuration one key.
        """
        digest = hashlib.sha256()
        found = False
        for suffix, path in cls.candidates(data_source, source_path):
            digest.update(suffix.encode("utf-8"))
            digest.update(b"\0")
            if os.path.isfile(path):
                found = True
                cls._update_with_file(digest, path)
            else:
                digest.update(cls.ABSENT.encode("utf-8"))
            digest.update(b"\0")
        if not found:
            candidates = ", ".join(path for _, path in cls.candidates(data_source, source_path))
            raise FileNotFoundError(
                f"The weather source {source_path!r} for data source {data_source.value} names no file that "
                f"exists; looked for: {candidates}."
            )
        return digest.hexdigest()

    @classmethod
    def _update_with_file(cls, digest: "hashlib._Hash", path: str) -> None:
        """Feed one file's bytes into a running digest.

        Args:
            digest: the digest to update.
            path: the file to read.
        """
        with open(path, "rb") as data_file:
            for block in iter(lambda: data_file.read(cls.BLOCK_SIZE), b""):
                digest.update(block)


@dataclass(frozen=True)
class WeatherSeriesInputs:
    """Everything :func:`produce_weather_series` depends on, and nothing else.

    Every field except :attr:`source_path` is key material: two runs whose inputs render to the same
    canonical JSON (:class:`hisim.caching.keys.CanonicalJson`) produce the same frame, on any machine.
    The path is the payload that pairs with :attr:`source_content_hash` -- the producer needs it to open
    the file, the key must not contain it -- and is excluded from the key by
    :attr:`hisim.caching.keys.KeyMaterial.PAYLOAD`.
    """

    #: Which reader understands the file, and therefore which columns and resampling path apply.
    data_source: WeatherDataSourceEnum

    #: :meth:`WeatherSourceFiles.content_hash` of the data file(s) behind :attr:`source_path`. This is
    #: what identifies the data in the key; the path is not key material.
    source_content_hash: str

    #: The simulated year. Every reader stamps its raw frame with an index starting on the first of
    #: January of this year, and the hourly interpolation reaches back to its 31st of December.
    year: int

    #: The simulation's timestep. Decides whether the one-minute series are aggregated and to what.
    seconds_per_timestep: int

    #: Latitude of the station in degrees, from the data file's own header. Used for the sun position.
    latitude_in_degrees: float

    #: Longitude of the station in degrees, from the data file's own header. Used for the sun position.
    longitude_in_degrees: float

    #: The length of the simulated span in whole days, for the one data source whose raw frame is
    #: sized by it (``DWD_15MIN``, whose reader builds exactly ``24 * 4 * days`` rows). ``None`` for
    #: every other source, whose frame covers the full year whatever the run's start and end are --
    #: which is why a one-week run and a one-year run of the same station now share one entry.
    duration_in_days: Optional[int]

    #: Where the file is on this machine. Payload, not key material: it differs between a checkout, a
    #: container and the cluster while naming the same bytes, which :attr:`source_content_hash` covers.
    source_path: str = field(metadata=KeyMaterial.PAYLOAD)

    #: The sources whose raw frame is sized by the simulated span rather than by the year alone.
    DURATION_DEPENDENT_SOURCES: ClassVar[FrozenSet[WeatherDataSourceEnum]] = frozenset(
        {WeatherDataSourceEnum.DWD_15MIN}
    )

    def required_duration_in_days(self) -> int:
        """Return :attr:`duration_in_days` for a source that needs it, refusing ``None``.

        Returns:
            int: the simulated span in whole days.

        Raises:
            ValueError: if the field is unset although this data source's reader is sized by it. The
                component fills the field for exactly the sources in
                :attr:`DURATION_DEPENDENT_SOURCES`; an unset value means a source was added to that set
                without the component being taught to fill it, and computing a silently shorter frame
                would be worse than stopping.
        """
        if self.duration_in_days is None:
            raise ValueError(
                f"The weather data source {self.data_source.value} sizes its raw frame by the simulated "
                "span, but duration_in_days is not set in the calculation inputs."
            )
        return self.duration_in_days


#: The raw columns every reader provides and the producer carries forward to one-minute resolution.
RAW_COLUMNS: Tuple[str, ...] = ("DNI", "T", "DHI", "GHI", "Wspd", "Pressure")

#: Data sources that arrive at a resolution finer than an hour: their columns are resampled to one
#: minute and linearly interpolated in place. The hourly sources go through :func:`interpolate`, which
#: additionally pads the year's two open ends before interpolating.
SUB_HOURLY_SOURCES: FrozenSet[WeatherDataSourceEnum] = frozenset(
    {
        WeatherDataSourceEnum.NSRDB_15MIN,
        WeatherDataSourceEnum.DWD_10MIN,
        WeatherDataSourceEnum.DWD_15MIN,
        WeatherDataSourceEnum.ERA5,
    }
)

#: The columns of the produced frame, in the order the component and the cached CSV expect them.
PRODUCED_COLUMNS: Tuple[str, ...] = (
    "DNI",
    "DHI",
    "GHI",
    "t_out",
    "altitude",
    "azimuth",
    "apparent_zenith",
    "DryBulb",
    "Wspd",
    "Pressure",
    "DNIextra",
    "t_out_daily_average",
)


def produce_weather_series(inputs: WeatherSeriesInputs) -> pd.DataFrame:
    """Produce the full-year weather frame the component simulates from.

    Reads the configured data file, brings every column to one-minute resolution, computes the sun
    position and the extraterrestrial irradiance for that index, aggregates everything to the
    simulation's timestep and adds the running daily average of the air temperature. The result is a
    pure function of ``inputs``; it is what the cache stores under the key built from them.

    Args:
        inputs: the calculation's inputs.

    Returns:
        pd.DataFrame: one row per timestep of the simulated year, with the columns of
            :data:`PRODUCED_COLUMNS`.
    """
    raw_data = read_source_data(inputs)
    # todo: check if this should indeed be daily resample or if another time frequency would be needed.
    one_minute = resample_to_one_minute(raw_data, inputs)
    index = one_minute["DNI"].index
    # The extraterrestrial irradiance is needed by the Perez diffuse-irradiance models downstream.
    dni_extra = pd.Series(pvlib.irradiance.get_extra_radiation(index), index=index)  # type: ignore
    solar_position = pvlib.solarposition.get_solarposition(  # type: ignore
        index, inputs.latitude_in_degrees, inputs.longitude_in_degrees
    )
    columns = dict(one_minute)
    columns["DNIextra"] = dni_extra
    columns["altitude"] = solar_position["elevation"]
    columns["azimuth"] = solar_position["azimuth"]
    columns["apparent_zenith"] = solar_position["apparent_zenith"]
    per_timestep = aggregate_to_timestep(columns, inputs.seconds_per_timestep)
    temperature_list = per_timestep["T"]
    daily_average = calculate_daily_average_outside_temperature(
        temperature_list=temperature_list, seconds_per_timestep=inputs.seconds_per_timestep
    )
    series = [
        per_timestep["DNI"],
        per_timestep["DHI"],
        per_timestep["GHI"],
        temperature_list,
        per_timestep["altitude"],
        per_timestep["azimuth"],
        per_timestep["apparent_zenith"],
        # The dry-bulb temperature is the same series as the air temperature; it is written as its own
        # column because readers of the cached frame have always found it there.
        temperature_list,
        per_timestep["Wspd"],
        per_timestep["Pressure"],
        per_timestep["DNIextra"],
        daily_average,
    ]
    return pd.DataFrame(np.transpose(series), columns=list(PRODUCED_COLUMNS))


def read_source_data(inputs: WeatherSeriesInputs) -> pd.DataFrame:
    """Read the raw weather file with the reader its data source names.

    Based on the tsib project @[tsib-kotzur] (Check header)

    Args:
        inputs: the calculation's inputs; supplies the path, the year and, where a reader needs it,
            the simulated span.

    Returns:
        pd.DataFrame: the raw frame, indexed by time at the file's own resolution.

    Raises:
        ValueError: if no reader is registered for the data source.
    """
    filepath = inputs.source_path
    if inputs.data_source == WeatherDataSourceEnum.NSRDB:
        return read_nsrdb_data(filepath, inputs.year)
    if inputs.data_source == WeatherDataSourceEnum.DWD_TRY:
        return read_dwd_try_data(filepath, inputs.year)
    if inputs.data_source == WeatherDataSourceEnum.NSRDB_15MIN:
        return read_nsrdb_15min_data(filepath, inputs.year)
    if inputs.data_source == WeatherDataSourceEnum.DWD_10MIN:
        return read_dwd_10min_data(filepath, inputs.year)
    if inputs.data_source == WeatherDataSourceEnum.DWD_15MIN:
        return read_dwd_15min_data(filepath, inputs.year, inputs.required_duration_in_days())
    if inputs.data_source == WeatherDataSourceEnum.ERA5:
        return read_era5_data(filepath, inputs.year)
    raise ValueError(f"Unsupported weather data source: {inputs.data_source}")


def resample_to_one_minute(raw_data: pd.DataFrame, inputs: WeatherSeriesInputs) -> Dict[str, pd.Series]:
    """Bring every raw column to a one-minute index by linear interpolation.

    The sub-hourly sources are interpolated between their own samples. The hourly ones go through
    :func:`interpolate`, which first pads the series at both ends of the year so that the
    interpolation covers the whole of it.

    Args:
        raw_data: the raw frame from :func:`read_source_data`.
        inputs: the calculation's inputs; supplies the data source and the year.

    Returns:
        Dict[str, pd.Series]: one one-minute series per column of :data:`RAW_COLUMNS`.
    """
    if inputs.data_source in SUB_HOURLY_SOURCES:
        return {
            column: raw_data[column].resample("1min").asfreq().interpolate(method="linear")
            for column in RAW_COLUMNS
        }
    return {column: interpolate(raw_data[column], inputs.year) for column in RAW_COLUMNS}


def aggregate_to_timestep(columns: Mapping[str, pd.Series], seconds_per_timestep: int) -> Dict[str, List[float]]:
    """Average the one-minute series over the simulation's timestep, as plain lists.

    A timestep of one minute is already the resolution of the series, so they are handed over as they
    are -- except the wind speed, which is resampled in that case too. On an already-minutely series
    that is a no-op; it is kept because dropping it would be a numeric change rather than an
    extraction, and this module's job was to move the calculation, not to alter it.

    Args:
        columns: the one-minute series, keyed by column name.
        seconds_per_timestep: the simulation's timestep.

    Returns:
        Dict[str, List[float]]: one list of values per column.
    """
    if seconds_per_timestep != 60:
        rule = str(seconds_per_timestep) + "s"
        return {name: series.resample(rule).mean().tolist() for name, series in columns.items()}
    aggregated = {name: series.tolist() for name, series in columns.items()}
    aggregated["Wspd"] = columns["Wspd"].resample(str(seconds_per_timestep) + "s").mean().tolist()
    return aggregated


def interpolate(pd_database: Any, year: int) -> Any:
    """Interpolates a time series to one minute, padding the year's first and last hour.

    Args:
        pd_database: the hourly series.
        year: the simulated year.

    Returns:
        Any: the one-minute series.
    """
    firstday = pd.Series(
        [0.0],
        index=[pd.to_datetime(datetime.datetime(year - 1, 12, 31, 23, 0), utc=True).tz_convert(tz="Europe/Berlin")],
    )
    lastday = pd.Series(
        pd_database.iloc[-1],
        index=[pd.to_datetime(datetime.datetime(year, 12, 31, 22, 59), utc=True).tz_convert(tz="Europe/Berlin")],
    )
    pd_database = pd.concat([pd_database, firstday, lastday])
    pd_database = pd_database.sort_index()
    return pd_database.resample("1min").asfreq().interpolate(method="linear")


def calculate_daily_average_outside_temperature(
    temperature_list: List[float], seconds_per_timestep: int
) -> List[float]:
    """Calculate the daily average outside temperatures.

    Args:
        temperature_list: the air temperature per timestep.
        seconds_per_timestep: the simulation's timestep.

    Returns:
        List[float]: one average per timestep, held constant within a day.
    """
    timestep_24h = int(24 * 3600 / seconds_per_timestep)
    total_number_of_timesteps_temperature_list = len(temperature_list)
    daily_averages: List[float] = []
    start_index = 0
    for index in range(0, total_number_of_timesteps_temperature_list):
        daily_average_temperature = float(np.mean(temperature_list[start_index: start_index + timestep_24h]))
        if index == start_index + timestep_24h:
            start_index = index
        daily_averages.append(daily_average_temperature)
    return daily_averages


def get_coordinates(filepath: str, source_enum: WeatherDataSourceEnum) -> Dict[str, Any]:
    """Reads the station name and its coordinates from the header of a weather data file.

    Based on the tsib project @[tsib-kotzur] (Check header)

    Args:
        filepath: the configured source path.
        source_enum: the data source, which decides where in the file the header is.

    Returns:
        Dict[str, Any]: ``name``, ``latitude`` and ``longitude``.
    """
    if source_enum == WeatherDataSourceEnum.NSRDB_15MIN:
        with open(filepath, encoding="utf-8") as csvfile:
            spamreader = csv.reader(csvfile)
            for i, row in enumerate(spamreader):
                if i == 1:
                    location_name = row[1]
                    lat = float(row[5])
                    lon = float(row[6])
                elif i > 1:
                    break

    elif source_enum in (WeatherDataSourceEnum.DWD_10MIN, WeatherDataSourceEnum.DWD_15MIN):
        with open(filepath, encoding="utf-8") as csvfile:
            spamreader = csv.reader(csvfile)
            for i, row in enumerate(spamreader):
                if i == 1:
                    location_name = row[0]
                    lat = float(row[1])
                    lon = float(row[2])
                elif i > 1:
                    break

    elif source_enum == WeatherDataSourceEnum.ERA5:
        with open(filepath, encoding="utf-8") as csvfile:
            spamreader = csv.reader(csvfile)
            for i, row in enumerate(spamreader):
                if i == 1:
                    location_name = row[0]
                    lat = float(row[1])
                    lon = float(row[2])
                elif i > 1:
                    break

    else:
        # get the geoposition
        with open(filepath + ".dat", encoding="utf-8") as file_stream:
            lines = file_stream.readlines()
            location_name = lines[0].split(maxsplit=2)[2].replace("\n", "")
            lat = float(lines[1][20:37])
            lon = float(lines[2][15:30])
    return {"name": location_name, "latitude": lat, "longitude": lon}


def read_dwd_try_data(filepath: str, year: int) -> pd.DataFrame:
    """Reads the DWD Test Reference Year (TRY) data.

    Args:
        filepath: the source path without extension; ``.dat`` holds the header, and a ``.csv`` beside
            it, when one exists, holds a series whose DNI has already been computed.
        year: the simulated year.

    Returns:
        pd.DataFrame: the hourly raw frame.
    """
    # get the geoposition
    with open(filepath + ".dat", encoding="utf-8") as file_stream:
        lines = file_stream.readlines()
        lat_in_degrees = float(lines[1][20:37])
        lon_in_degrees = float(lines[2][15:30])
    # check if time series data already exists as .csv with DNI
    if os.path.isfile(filepath + ".csv"):
        data = pd.read_csv(filepath + ".csv", index_col=0, parse_dates=True, sep=";", decimal=",")
        data.index = pd.to_datetime(data.index, utc=True).tz_convert("Europe/Berlin")
    # else read from .dat and calculate DNI etc.
    else:
        # get data
        data = pd.read_csv(filepath + ".dat", sep=r"\s+", skiprows=list(range(0, 31)))

        data.index = pd.date_range(f"{year}-01-01 00:30:00", periods=8760, freq="h", tz="Europe/Berlin")
        data["GHI"] = data["D"] + data["B"]
        data = data.rename(
            columns={
                "D": "DHI",
                "t": "T",
                "WG": "Wspd",
                "MM": "Month",
                "DD": "Day",
                "HH": "Hour",
                "p": "Pressure",
                "WR": "Wdir",
            }
        )

        # calculate direct normal
        data["DNI"] = calculate_direct_normal_irradiance_in_watt_per_square_meter(data["B"], lon_in_degrees, lat_in_degrees)
    return data


def read_nsrdb_data(filepath: str, year: int) -> pd.DataFrame:
    """Reads a set of NSRDB data.

    Args:
        filepath: the source path without extension; the data is in the ``.dat`` beside it.
        year: the simulated year.

    Returns:
        pd.DataFrame: the hourly raw frame.
    """
    # get data
    data = pd.read_csv(filepath + ".dat", sep=",", skiprows=list(range(0, 11)))
    data = data.drop(data.index[8761:8772])
    data.index = pd.date_range(f"{year}-01-01 00:30:00", periods=8760, freq="h", tz="Europe/Berlin")
    data = data.rename(
        columns={
            "DHI": "DHI",
            "Temperature": "T",
            "Wind Speed": "Wspd",
            "MM": "Month",
            "DD": "Day",
            "HH": "Hour",
            "Pressure": "Pressure",
            "Wind Direction": "Wdir",
            "GHI": "GHI",
            "DNI": "DNI",
        }
    )
    return data


def read_nsrdb_15min_data(filepath: str, year: int) -> pd.DataFrame:
    """Reads a set of NSRDB data in 15 min resolution.

    Args:
        filepath: the data file.
        year: the simulated year.

    Returns:
        pd.DataFrame: the quarter-hourly raw frame.
    """
    data = pd.read_csv(filepath, encoding="utf-8", skiprows=[0, 1])
    # get data
    data.index = pd.date_range(f"{year}-01-01 00:00:00", periods=24 * 4 * 365, freq="900s", tz="UTC")
    data = data.rename(
        columns={
            "Temperature": "T",
            "Wind Speed": "Wspd",
        }
    )
    return data


def read_dwd_10min_data(filepath: str, year: int) -> pd.DataFrame:
    """Reads a set of DWD data in 10 min resolution.

    https://github.com/earthobservations/wetterdienst/tree/main

    Args:
        filepath: the data file, whose second row carries the station coordinates.
        year: the simulated year.

    Returns:
        pd.DataFrame: the ten-minutely raw frame.
    """
    # get location
    location = pd.read_csv(  # type: ignore
        filepath,
        nrows=1,
        skiprows=1,
        header=None,
        names=pd.read_csv(filepath, nrows=1).columns,
    )
    longitude_in_degrees = location["longitude"][0]
    latitude_in_degrees = location["latitude"][0]

    # get data
    data = pd.read_csv(filepath, encoding="utf-8", skiprows=[0, 1])
    data.index = pd.date_range(f"{year}-01-01 00:00:00", periods=24 * 6 * 365, freq="600s", tz="UTC")
    data = data.rename(
        columns={
            "diffuse_irradiance": "DHI",
            "temperature": "T",
            "wind_speed": "Wspd",
            "month": "Month",
            "day": "Day",
            "hour": "Hour",
            "minute": "Minutes",
            "pressure": "Pressure",
            "wind_direction": "Wdir",
            "global_irradiance": "GHI",
        }
    )
    # calculate direct normal
    data["direct_horizontal_irradiance"] = data["GHI"] - data["DHI"]
    data["DNI"] = calculate_direct_normal_irradiance_in_watt_per_square_meter(data["direct_horizontal_irradiance"], longitude_in_degrees, latitude_in_degrees)

    return data


def read_dwd_15min_data(filepath: str, year: int, duration_in_days: int) -> pd.DataFrame:
    """Reads a set of DWD data in 15 min resolution.

    https://github.com/earthobservations/wetterdienst/tree/main

    Args:
        filepath: the data file, whose second row carries the station coordinates.
        year: the simulated year.
        duration_in_days: the simulated span in whole days; this reader alone sizes its frame by it.

    Returns:
        pd.DataFrame: the quarter-hourly raw frame.
    """
    # get location
    location = pd.read_csv(  # type: ignore
        filepath,
        nrows=1,
        skiprows=1,
        header=None,
        names=pd.read_csv(filepath, nrows=1).columns,
    )
    longitude_in_degrees = location["longitude"][0]
    latitude_in_degrees = location["latitude"][0]

    # get data
    data = pd.read_csv(filepath, encoding="utf-8", skiprows=[0, 1])

    data.index = pd.date_range(
        f"{year}-01-01 00:00:00",
        periods=24 * 4 * int(duration_in_days),
        freq="900s",
        tz="UTC",
    )

    data = data.rename(
        columns={
            "diffuse_irradiance": "DHI",
            "temperature": "T",
            "wind_speed": "Wspd",
            "month": "Month",
            "day": "Day",
            "hour": "Hour",
            "minute": "Minutes",
            "pressure": "Pressure",
            "wind_direction": "Wdir",
            "global_irradiance": "GHI",
        }
    )
    # calculate direct normal
    data["direct_horizontal_irradiance"] = data["GHI"] - data["DHI"]
    data["DNI"] = calculate_direct_normal_irradiance_in_watt_per_square_meter(data["direct_horizontal_irradiance"], longitude_in_degrees, latitude_in_degrees)

    return data


def read_era5_data(filepath: str, year: int) -> pd.DataFrame:
    """Reads a set of era5 in 60 min resolution.

    https://cds.climate.copernicus.eu/cdsapp#!/dataset/reanalysis-era5-single-levels?tab=overview

    Args:
        filepath: the data file, whose second row carries the station coordinates.
        year: the simulated year.

    Returns:
        pd.DataFrame: the hourly raw frame.
    """
    # get location
    location = pd.read_csv(  # type: ignore
        filepath,
        nrows=1,
        skiprows=1,
        header=None,
        names=pd.read_csv(filepath, nrows=1).columns,
    )
    longitude_in_degrees = location["longitude"][0]
    latitude_in_degrees = location["latitude"][0]

    # get data
    data = pd.read_csv(filepath, encoding="utf-8", skiprows=[0, 1])
    data.index = pd.date_range(f"{year}-01-01 00:00:00", periods=8760, freq="h", tz="UTC")
    data = data.rename(
        columns={
            "month": "Month",
            "day": "Day",
            "hour": "Hour",
            "minute": "Minutes",
            "temperature": "T",
            "pressure": "Pressure",
            "wind_direction": "Wdir",
            "wind_speed": "Wspd",
            "global_irradiance": "GHI",
        }
    )
    # calculate direct normal
    data["DHI"] = data["GHI"] - data["direct_irradiance"]
    data["DNI"] = calculate_direct_normal_irradiance_in_watt_per_square_meter(data["direct_irradiance"], longitude_in_degrees, latitude_in_degrees)

    return data


def calculate_direct_normal_irradiance_in_watt_per_square_meter(
    direct_horizontal_irradiance_in_watt_per_square_meter: pd.Series,
    lon_in_degrees: float,
    lat_in_degrees: float,
    zenith_tol_in_degrees: float = 87.0,
) -> pd.Series:
    """Calculates the direct NORMAL irradiance in W/m² from the direct horizontal irradiance in W/m² using PV lib.

    Based on the tsib project @[tsib-kotzur] (Check header)

    Parameters
    ----------
    direct_horizontal_irradiance_in_watt_per_square_meter: pd.Series with time index
        Direct horizontal irradiance in W/m²
    lon_in_degrees: float
        Longitude of the location in degrees
    lat_in_degrees: float
        Latitude of the location in degrees
    zenith_tol_in_degrees: float, optional
        The zenith angle in degrees at which the sun's position is clamped before dividing by its cosine,
        so that the divisor cannot approach zero toward the horizon or go negative past it. Must lie
        strictly between 0 and 90.

    Returns
    -------
    dni_in_watt_per_square_meter: pd.Series
        Direct normal irradiance in W/m²

    Raises
    ------
    ValueError
        If ``zenith_tol_in_degrees`` is not strictly between 0 and 90, or if the result contains NaN, which
        means the horizontal irradiance or the solar position was NaN at some timestep.

    """
    if not 0 < zenith_tol_in_degrees < 90:
        raise ValueError(
            "zenith_tol_in_degrees must lie strictly between 0 and 90 degrees so that its cosine is "
            f"positive, got {zenith_tol_in_degrees}."
        )
    solar_pos = pvlib.solarposition.get_solarposition(
        direct_horizontal_irradiance_in_watt_per_square_meter.index, lat_in_degrees, lon_in_degrees
    )
    # Clamp the zenith angle at zenith_tol_in_degrees before dividing by its cosine. This must be a single
    # .loc assignment: the earlier chained form (solar_pos["apparent_zenith"][mask] = tol) wrote into a
    # temporary under pandas copy-on-write and had no effect, so DNI was unbounded near the horizon
    # and negative past it (pandas reported this as ChainedAssignmentError).
    solar_pos.loc[solar_pos["apparent_zenith"] > zenith_tol_in_degrees, "apparent_zenith"] = zenith_tol_in_degrees
    dni_in_watt_per_square_meter = direct_horizontal_irradiance_in_watt_per_square_meter.div(
        solar_pos["apparent_zenith"].apply(math.radians).apply(math.cos)
    )
    if dni_in_watt_per_square_meter.isnull().any():
        nan_irradiance = int(direct_horizontal_irradiance_in_watt_per_square_meter.isnull().sum())
        nan_zenith = int(solar_pos["apparent_zenith"].isnull().sum())
        nan_mask = dni_in_watt_per_square_meter.isnull()
        raise ValueError(
            f"The direct normal irradiance is NaN at {int(nan_mask.sum())} of "
            f"{len(dni_in_watt_per_square_meter)} timesteps, the first at "
            f"{dni_in_watt_per_square_meter.index[nan_mask][0]}. The direct horizontal irradiance is NaN "
            f"at {nan_irradiance} timesteps and the solar zenith angle at {nan_zenith} "
            f"(lat={lat_in_degrees}, lon={lon_in_degrees}). A NaN irradiance points at a gap in the "
            "weather file; a NaN zenith at the time index or the coordinates."
        )
    return dni_in_watt_per_square_meter
