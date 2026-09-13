""" Handles all the weather data processing.

Part of the ``hisim.components.weather`` package (see the package ``__init__`` for the layout): this
module holds the component. Its configuration is in :mod:`hisim.components.weather.config`, and the
processing itself in :mod:`hisim.components.weather.calculation` -- a static producer whose cache key
carries a fingerprint of its own source, so a change to the way the series is computed can no longer be
served from a cache written before it (the #628 cache finding, and ``roadmap/cache_service_spec.md`` §3).
What is left here is the component around that series -- its outputs, the location entry, the 24 h
temperature forecast and the yearly arrays the PV system reads.
"""

# clean
import datetime
import math
from typing import Any, ClassVar, Dict, List, Mapping, Optional

import pandas as pd

from hisim import loadtypes as lt
from hisim import log, utils
from hisim.caching import CacheClient, CacheEntry, CacheKey
# The module object itself is what the cache key is fingerprinted from: ``CacheKey.for_producer`` walks
# its import closure and hashes the source of everything in it, so an edit to the calculation changes
# the key without anyone declaring anything.
from hisim.components.weather import calculation
from hisim.components.weather.calculation import (
    ARTIFACT_KIND,
    WeatherSeriesInputs,
    WeatherSourceFiles,
    get_coordinates,
    produce_weather_series,
)
from hisim.components.weather.config import WeatherConfig
from hisim.config import DisplayConfig
from hisim.component import Component, ComponentOutput, SingleTimeStepValues, OpexCostDataClass, CapexCostDataClass
from hisim.simulationparameters import SimulationParameters
from hisim.economics.facts import CostRelevance

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Noah Pflugradt"

""" The functions cited in this module are at some degree based on the tsib project:

[tsib-kotzur]: Kotzur, Leander, Detlef Stolten, and Hermann-Josef Wagner. Future grid load of the residential building sector.
No. RWTH-2018-231872. Lehrstuhl für Brennstoffzellen (FZ Jülich), 2019.
ID: http://hdl.handle.net/2128/21115
    http://nbn-resolving.org/resolver?verb=redirect&identifier=urn:nbn:de:0001-2019020614

The implementation of the tsib project can be found under the following repository:
https://github.com/FZJ-IEK3-VSA/tsib
"""


class Weather(Component):
    """Provide thermal and solar conditions of local weather."""

    cost_relevance = CostRelevance.FREE_OF_COST

    # The weather is not a device: there is nothing to buy, nothing to run and no indicator of its own
    # to report. See Component.MODELS_NO_DEVICE, which answers the cost and KPI hooks for it.
    MODELS_NO_DEVICE: ClassVar[bool] = True

    # Inputs
    # None

    # Outputs
    TemperatureOutside: str = "TemperatureOutside"
    DirectNormalIrradiance: str = "DirectNormalIrradiance"
    DiffuseHorizontalIrradiance: str = "DiffuseHorizontalIrradiance"
    DirectNormalIrradianceExtra: str = "DirectNormalIrradianceExtra"
    GlobalHorizontalIrradiance: str = "GlobalHorizontalIrradiance"
    Altitude: str = "Altitude"
    Azimuth: str = "Azimuth"
    ApparentZenith: str = "ApparentZenith"
    WindSpeed: str = "WindSpeed"
    Pressure: str = "Pressure"
    Weather_Temperature_Forecast_24h: str = "Weather_Temperature_Forecast_24h"
    DailyAverageOutsideTemperatures: str = "DailyAverageOutsideTemperatures"

    #: The key this component publishes the identity of its produced series under, in the
    #: per-simulation repository. The value is the digest of the series' cache key, which is what
    #: ``roadmap/cache_service_spec.md`` §3.1 calls the artifact key: a downstream producer that
    #: computes from the weather -- the PV series is the first -- takes this string as key material and
    #: the series themselves as payload, so that the two keys chain Merkle-style and any change to the
    #: weather, its code included, moves every key downstream of it.
    SERIES_ARTIFACT_KEY: str = "weather_series_artifact_key"

    # Keys under which this component publishes its full-year series into the per-simulation
    # repository (``self.simulation_repository``). They live here, on the writer, so that the
    # readers -- the PV system and the predictive branch of the Building -- import the name
    # instead of repeating a string literal that could drift away from the writer's.
    YEARLY_TEMPERATURE_OUTSIDE: str = "weather_yearly_temperature_outside_in_celsius"
    YEARLY_DIFFUSE_HORIZONTAL_IRRADIANCE: str = "weather_yearly_diffuse_horizontal_irradiance_in_watt_per_square_meter"
    YEARLY_DIRECT_NORMAL_IRRADIANCE: str = "weather_yearly_direct_normal_irradiance_in_watt_per_square_meter"
    YEARLY_DIRECT_NORMAL_IRRADIANCE_EXTRA: str = (
        "weather_yearly_direct_normal_irradiance_extra_in_watt_per_square_meter"
    )
    YEARLY_GLOBAL_HORIZONTAL_IRRADIANCE: str = "weather_yearly_global_horizontal_irradiance_in_watt_per_square_meter"
    YEARLY_AZIMUTH: str = "weather_yearly_azimuth_in_degrees"
    YEARLY_APPARENT_ZENITH: str = "weather_yearly_apparent_zenith_in_degrees"
    YEARLY_WIND_SPEED: str = "weather_yearly_wind_speed_in_meter_per_second"
    # The pressure list is in hectopascal; the per-timestep output converts it to pascal.

    #: Which of this component's per-timestep lists each column of the produced frame fills.
    #: :data:`hisim.components.weather.calculation.PRODUCED_COLUMNS` is the schema of that frame, on a
    #: cache hit as much as on a fresh computation, and this mapping names every one of those columns
    #: exactly once -- ``tests/test_weather.py`` asserts the two agree, so a column added to the
    #: producer without a reader here is a test failure rather than a ``KeyError`` mid-run.
    LIST_ATTRIBUTE_PER_COLUMN: ClassVar[Mapping[str, str]] = {
        "DNI": "dni_list",
        "DHI": "dhi_list",
        "GHI": "ghi_list",
        "t_out": "temperature_list",
        "altitude": "altitude_list",
        "azimuth": "azimuth_list",
        "apparent_zenith": "apparent_zenith_list",
        "Wspd": "wind_speed_list",
        "Pressure": "pressure_list",
        "DNIextra": "dniextra_list",
        "t_out_daily_average": "daily_average_outside_temperature_list_in_celsius",
    }

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: WeatherConfig,
        my_display_config: Optional[DisplayConfig] = None,
    ) -> None:
        """Initialize the Weather component and register its outputs.

        Args:
            my_simulation_parameters: Simulation parameters controlling time
                stepping and duration.
            config: Weather configuration specifying location, data source, and
                file path.
            my_display_config: Optional display configuration; defaults to a
                new DisplayConfig if None.
        """
        if my_simulation_parameters is None:
            raise ValueError("my_simulation_parameters was None")
        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.last_timestep_with_update = -1
        self.weather_config = config
        self.parameter_string = my_simulation_parameters.get_unique_key()

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.air_temperature_output: ComponentOutput = self.add_output(
            self.component_name,
            self.TemperatureOutside,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.TemperatureOutside} will follow.",
        )

        self.dni_output: ComponentOutput = self.add_output(
            self.component_name,
            self.DirectNormalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            output_description=f"here a description for {self.DirectNormalIrradiance} will follow.",
        )

        self.dni_extra_output: ComponentOutput = self.add_output(
            self.component_name,
            self.DirectNormalIrradianceExtra,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            output_description=f"here a description for {self.DirectNormalIrradianceExtra} will follow.",
        )

        self.dhi_output: ComponentOutput = self.add_output(
            self.component_name,
            self.DiffuseHorizontalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            output_description=f"here a description for {self.DiffuseHorizontalIrradiance} will follow.",
        )

        self.ghi_output: ComponentOutput = self.add_output(
            self.component_name,
            self.GlobalHorizontalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            output_description=f"here a description for {self.GlobalHorizontalIrradiance} will follow.",
        )

        self.altitude_output: ComponentOutput = self.add_output(
            self.component_name,
            self.Altitude,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            output_description=f"here a description for {self.Altitude} will follow.",
        )

        self.azimuth_output: ComponentOutput = self.add_output(
            self.component_name,
            self.Azimuth,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            output_description=f"here a description for {self.Azimuth} will follow.",
        )

        self.apparent_zenith_output: ComponentOutput = self.add_output(
            self.component_name,
            self.ApparentZenith,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            output_description=f"here a description for {self.ApparentZenith} will follow.",
        )

        self.wind_speed_output: ComponentOutput = self.add_output(
            self.component_name,
            self.WindSpeed,
            lt.LoadTypes.SPEED,
            lt.Units.METER_PER_SECOND,
            output_description=f"here a description for {self.WindSpeed} will follow.",
        )

        self.pressure_output: ComponentOutput = self.add_output(
            self.component_name,
            self.Pressure,
            lt.LoadTypes.PRESSURE,
            lt.Units.PASCAL,
            output_description=f"here a description for {self.Pressure} will follow.",
        )

        self.daily_average_outside_temperature_output: ComponentOutput = self.add_output(
            self.component_name,
            self.DailyAverageOutsideTemperatures,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.DailyAverageOutsideTemperatures} will follow.",
        )

        self.temperature_list: List[float]
        self.dni_list: List[float]
        self.dniextra_list: List[float]
        self.altitude_list: List[float]
        self.azimuth_list: List[float]
        self.wind_speed_list: List[float]
        self.pressure_list: List[float]
        self.ghi_list: List[float]
        self.apparent_zenith_list: List[float]
        self.dhi_list: List[float]
        self.daily_average_outside_temperature_list_in_celsius: List[float]

    def write_to_report(self):
        """Write configuration to the report."""
        return self.weather_config.get_string_dict()

    def i_save_state(self) -> None:
        """Saves the current state."""
        pass

    def i_restore_state(self) -> None:
        """Restores the previous state. Not needed for weather."""
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Double chekc."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Performs the simulation."""
        if self.last_timestep_with_update == timestep:
            return
        if force_convergence:
            return
        """ Performs the simulation. """
        stsv.set_output_value(self.air_temperature_output, self.temperature_list[timestep])
        stsv.set_output_value(self.dni_output, self.dni_list[timestep])
        stsv.set_output_value(self.dni_extra_output, self.dniextra_list[timestep])
        stsv.set_output_value(self.dhi_output, self.dhi_list[timestep])
        stsv.set_output_value(self.ghi_output, self.ghi_list[timestep])
        stsv.set_output_value(self.altitude_output, self.altitude_list[timestep])
        stsv.set_output_value(self.azimuth_output, self.azimuth_list[timestep])
        stsv.set_output_value(self.wind_speed_output, self.wind_speed_list[timestep])
        stsv.set_output_value(self.apparent_zenith_output, self.apparent_zenith_list[timestep])
        stsv.set_output_value(
            self.pressure_output, self.pressure_list[timestep] * 100
        )  # *100 umrechnung von hPA bzw mbar in PA
        stsv.set_output_value(
            self.daily_average_outside_temperature_output,
            self.daily_average_outside_temperature_list_in_celsius[timestep],
        )

        # set the temperature forecast
        if self.weather_config.predictive_control:
            timesteps_24h = 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
            last_forecast_timestep = int(timestep + timesteps_24h)
            last_forecast_timestep = min(last_forecast_timestep, len(self.temperature_list))
            # log.information( type(self.temperature))
            temperatureforecast = self.temperature_list[timestep:last_forecast_timestep]
            self.simulation_repository.set_entry(self.Weather_Temperature_Forecast_24h, temperatureforecast)
        self.last_timestep_with_update = timestep

    def i_prepare_simulation(self) -> None:
        """Fetches the processed weather series -- from the cache when it is there, from the producer when it is not."""
        log.information("Weather config: " + self.weather_config.to_json())  # type: ignore
        location_dict = get_coordinates(
            filepath=self.weather_config.source_path,
            source_enum=self.weather_config.data_source,
        )
        self.simulation_repository.set_entry("weather_location", location_dict)

        calculation_inputs = self.build_calculation_inputs(location_dict)
        key = self.series_cache_key(calculation_inputs)
        entry = self.cache_entry(key)
        if entry.exists:
            log.information(f"Weather series cache hit: {entry.path}")
            # float_precision="round_trip": pandas' default CSV reader uses a fast, inexact float
            # parser, so without this a cached run and an uncached one are not the same run. See the
            # measurement in the commit that introduced this across the caches.
            weather_series = pd.read_csv(
                entry.path, sep=",", decimal=".", encoding="cp1252", float_precision="round_trip"
            )
            origin = str(entry.path)
        else:
            log.information(f"Weather series cache miss: computing it and filing it at {entry.path}")
            weather_series = produce_weather_series(calculation_inputs)
            with entry.writing() as temporary_cache_filepath:
                weather_series.to_csv(temporary_cache_filepath)
            origin = "the weather producer"
        self.read_series(weather_series, origin)

        # Publish the full-year weather series into this simulation's repository, unconditionally.
        # The PV system needs the whole year rather than the current timestep: it runs one
        # vectorized pvlib pass over the year in its own i_prepare_simulation. It runs after this
        # component, because prepare_calculation walks the components in the order the setup added
        # them (hisim/simulator.py, prepare_calculation) -- the Weather therefore has to be added
        # before it, and the PV says so if it is not.
        # Publishing is free: the repository only stores references to the lists this component
        # keeps alive as attributes anyway. It is the per-simulation repository, created by the
        # Simulator and cleared when the run ends, so no array outlives the run that computed it
        # and a second simulation in the same process cannot read this one's weather.
        self.simulation_repository.set_entry(self.YEARLY_TEMPERATURE_OUTSIDE, self.temperature_list)
        self.simulation_repository.set_entry(self.YEARLY_DIFFUSE_HORIZONTAL_IRRADIANCE, self.dhi_list)
        self.simulation_repository.set_entry(self.YEARLY_DIRECT_NORMAL_IRRADIANCE, self.dni_list)
        self.simulation_repository.set_entry(self.YEARLY_DIRECT_NORMAL_IRRADIANCE_EXTRA, self.dniextra_list)
        self.simulation_repository.set_entry(self.YEARLY_GLOBAL_HORIZONTAL_IRRADIANCE, self.ghi_list)
        self.simulation_repository.set_entry(self.YEARLY_AZIMUTH, self.azimuth_list)
        self.simulation_repository.set_entry(self.YEARLY_APPARENT_ZENITH, self.apparent_zenith_list)
        self.simulation_repository.set_entry(self.YEARLY_WIND_SPEED, self.wind_speed_list)

        # Publish which series these are, so that a component computing from them can name them in its
        # own cache key without knowing anything about how they were made (spec §3.1). The digest is
        # enough: it already stands for the producer's code, its libraries and every input.
        self.simulation_repository.set_entry(self.SERIES_ARTIFACT_KEY, key.digest)

    def build_calculation_inputs(self, location_dict: Dict[str, Any]) -> WeatherSeriesInputs:
        """Build the DTO the weather producer is a pure function of.

        Everything the calculation depends on is extracted here from the configuration and the
        simulation parameters, and nothing else: the data file enters as the hash of its contents
        rather than as its path, and the simulated span only for the one data source whose reader is
        sized by it. What the component keeps to itself -- its name, the building it belongs to, the
        predictive-control flag, cost and display settings -- is not part of the calculation and
        therefore not part of its key.

        Args:
            location_dict: the station header read by :func:`get_coordinates`.

        Returns:
            WeatherSeriesInputs: the producer's single argument.
        """
        data_source = self.weather_config.data_source
        duration_in_days = (
            int(self.my_simulation_parameters.duration.days)
            if data_source in WeatherSeriesInputs.DURATION_DEPENDENT_SOURCES
            else None
        )
        return WeatherSeriesInputs(
            data_source=data_source,
            source_content_hash=WeatherSourceFiles.content_hash(data_source, self.weather_config.source_path),
            year=self.my_simulation_parameters.year,
            seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep,
            latitude_in_degrees=float(location_dict["latitude"]),
            longitude_in_degrees=float(location_dict["longitude"]),
            duration_in_days=duration_in_days,
            source_path=self.weather_config.source_path,
        )

    def series_cache_key(self, calculation_inputs: WeatherSeriesInputs) -> CacheKey:
        """Build the key the produced series is filed under, and which identifies it downstream.

        The key is ``sha256(artifact kind : code fingerprint : third-party fingerprint : DTO JSON)``
        (``roadmap/cache_service_spec.md`` §3). The two fingerprints are read from the producer
        module's import closure, so an edit to the calculation invalidates the entry by itself -- the
        thing the weather cache did not do when #628 fixed the direct normal irradiance and CI, running
        on a restored cache, reported every one of the golden pairs of the day -- 24 of them -- unchanged.

        Its digest is also what :attr:`SERIES_ARTIFACT_KEY` publishes, so a downstream producer's key
        inherits everything this one stands for.

        Args:
            calculation_inputs: the DTO from :meth:`build_calculation_inputs`.

        Returns:
            CacheKey: the key.
        """
        return CacheKey.for_producer(ARTIFACT_KIND, calculation, calculation_inputs)

    def cache_entry(self, key: CacheKey) -> CacheEntry:
        """Look the produced series up in the cache under the key of the producer that makes it.

        Args:
            key: the key from :meth:`series_cache_key`.

        Returns:
            CacheEntry: where the entry is or will be, and whether it is there.
        """
        return CacheClient.from_environment().lookup_producer(key, self.my_simulation_parameters.cache_dir_path)

    def read_series(self, weather_series: pd.DataFrame, origin: str) -> None:
        """Take the component's per-timestep lists out of the produced frame.

        The frame has one schema -- :attr:`LIST_ATTRIBUTE_PER_COLUMN`, which is the producer's
        :data:`~hisim.components.weather.calculation.PRODUCED_COLUMNS` -- and the same reading serves a
        cache hit and a fresh computation, so the two cannot drift apart.

        A column that is not there is refused rather than filled in. An entry filed under a producer
        key was written by that producer and carries every column it produces; a frame that lacks one
        is a foreign or truncated file, and reading it as zeros would put a fabricated series (the
        pressure the PV system reads, say) into a run that reports its results as measurements.

        Args:
            weather_series: the frame from the cache or from the producer.
            origin: where the frame came from -- the cache entry's path, or the producer -- so that a
                missing column names the file to look at.

        Raises:
            KeyError: if the frame lacks a column the component reads.
        """
        for column, attribute in self.LIST_ATTRIBUTE_PER_COLUMN.items():
            if column not in weather_series.columns:
                raise KeyError(
                    f"The weather series from {origin} has no column {column!r}, which the component "
                    f"reads into {attribute}. A frame filed under this producer's key carries every "
                    f"column the producer writes, so this one was not written by it; delete it and let "
                    f"the run recompute the series. Columns found: {sorted(weather_series.columns)}."
                )
            setattr(self, attribute, weather_series[column].tolist())

    def calc_sun_position(self, latitude_deg, longitude_deg, year, hoy):
        """Calculates the Sun Position for a specific hour and location.

        :param latitude_deg: Geographical Latitude in Degrees
        :type latitude_deg: float
        :param longitude_deg: Geographical Longitude in Degrees
        :type longitude_deg: float
        :param year: year
        :type year: int
        :param hoy: Hour of the year from the start. The first hour of January is 1
        :type hoy: int
        :return: altitude, azimuth: Sun position in altitude and azimuth degrees [degrees]
        :rtype: tuple
        """
        # Convert to Radians
        latitude_rad = math.radians(latitude_deg)
        # longitude_rad = math.radians(longitude_deg)  # Note: this is never used

        # Set the date in UTC based off the hour of year and the year itself
        start_of_year = datetime.datetime(year, 1, 1, 0, 0, 0, 0)
        utc_datetime = start_of_year + datetime.timedelta(hours=hoy)

        # Angular distance of the sun north or south of the earths equator
        # Determine the day of the year.
        day_of_year = utc_datetime.timetuple().tm_yday

        # Calculate the declination angle: The variation due to the earths tilt
        # http://www.pveducation.org/pvcdrom/properties-of-sunlight/declination-angle
        declination_rad = math.radians(23.45 * math.sin((2 * math.pi / 365.0) * (day_of_year - 81)))

        # Normalise the day to 2*pi
        # There is some reason as to why it is 364 and not 365.26
        angle_of_day = (day_of_year - 81) * (2 * math.pi / 364)

        # The deviation between local standard time and true solar time
        equation_of_time = (
            (9.87 * math.sin(2 * angle_of_day)) - (7.53 * math.cos(angle_of_day)) - (1.5 * math.sin(angle_of_day))
        )

        # True Solar Time
        solar_time = ((utc_datetime.hour * 60) + utc_datetime.minute + (4 * longitude_deg) + equation_of_time) / 60.0

        # Angle between the local longitude and longitude where the sun is at
        # higher altitude
        hour_angle_rad = math.radians(15 * (12 - solar_time))

        # Altitude Position of the Sun in Radians
        altitude_rad = math.asin(
            math.cos(latitude_rad) * math.cos(declination_rad) * math.cos(hour_angle_rad)
            + math.sin(latitude_rad) * math.sin(declination_rad)
        )

        # Azimuth Position fo the sun in radians
        azimuth_rad = math.asin(math.cos(declination_rad) * math.sin(hour_angle_rad) / math.cos(altitude_rad))

        # I don't really know what this code does, it has been imported from
        # PySolar
        if math.cos(hour_angle_rad) >= (math.tan(declination_rad) / math.tan(latitude_rad)):
            return math.degrees(altitude_rad), math.degrees(azimuth_rad)
        return math.degrees(altitude_rad), (180 - math.degrees(azimuth_rad))

    def calc_sun_position2(self, hoy: Any) -> Any:
        """Calculates the sun position."""
        return self.altitude_list[hoy], self.azimuth_list[hoy]

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(config: WeatherConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class


# The public name of this class is the package, not the submodule. ``get_full_classname`` and the
# energy-system recorder derive a component's class path from ``__module__``, and 33 recorded twins plus
# the generated JSON schema spell the weather ``hisim.components.weather.Weather``. The pin lives here,
# beside the class, rather than in the package ``__init__``: the facade is lazy, so it has not
# necessarily run when someone imports this module directly, and the class must carry its public name
# however it was reached. Its one cost is that ``inspect.getsourcelines(Weather)`` no longer finds the
# class body (the code-overview generator already handles that case); imports, pickling, ``describe``
# and the JSON class resolution are unaffected.
Weather.__module__ = "hisim.components.weather"
