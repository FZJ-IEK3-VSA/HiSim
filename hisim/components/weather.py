""" Handles all the weather data processing.

The processing itself lives next door, in :mod:`hisim.components.weather_calculation`: it is a static
producer whose cache key carries a fingerprint of its own source, so a change to the way the series is
computed can no longer be served from a cache written before it (the #628 cache finding, and
``roadmap/cache_service_spec.md`` §3). What is left here is the component around that series -- its
outputs, the location entry, the 24 h temperature forecast and the yearly arrays the PV system reads.
"""

# clean
import datetime
import math
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath
from typing import Any, Dict, List, Optional, Union

from dataclasses_json import dataclass_json
import pandas as pd

from hisim import loadtypes as lt
from hisim import log, utils
from hisim.caching import CacheClient, CacheEntry, CacheKey
# The module object itself is what the cache key is fingerprinted from: ``CacheKey.for_producer`` walks
# its import closure and hashes the source of everything in it, so an edit to the calculation changes
# the key without anyone declaring anything. The names below are re-exported for the configuration and
# for every system setup that spells ``weather.WeatherDataSourceEnum``.
from hisim.components import weather_calculation
from hisim.components.weather_calculation import (
    ARTIFACT_KIND,
    WeatherDataSourceEnum,
    WeatherSeriesInputs,
    WeatherSourceFiles,
    get_coordinates,
    produce_weather_series,
)
from hisim.config import ConfigBase, ComponentID, DisplayConfig, FactContribution, constructor, preset
from hisim.component import Component, ComponentOutput, SingleTimeStepValues, OpexCostDataClass, CapexCostDataClass
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry
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


class LocationEnum(Enum):
    """contains all the locations and their corresponding directories."""

    AACHEN = (
        "Aachen",
        "test-reference-years_1995-2012_1-location",
        "data_processed",
        "aachen_center",
        WeatherDataSourceEnum.DWD_TRY,
    )
    BREMERHAVEN = (
        "01_Bremerhaven",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_01",
        WeatherDataSourceEnum.DWD_TRY,
    )
    ROSTOCK = (
        "02_Rostock",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_02",
        WeatherDataSourceEnum.DWD_TRY,
    )
    HAMBURG = (
        "03Hamburg",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_03",
        WeatherDataSourceEnum.DWD_TRY,
    )
    POTSDAM = (
        "04Potsdam",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_04",
        WeatherDataSourceEnum.DWD_TRY,
    )
    ESSEN = (
        "05Essen",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_05",
        WeatherDataSourceEnum.DWD_TRY,
    )
    BAD_MARIENBURG = (
        "06Bad Marienburg",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_06",
        WeatherDataSourceEnum.DWD_TRY,
    )
    KASSEL = (
        "07Kassel",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_07",
        WeatherDataSourceEnum.DWD_TRY,
    )
    BRAUNLAGE = (
        "08Braunlage",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_08",
        WeatherDataSourceEnum.DWD_TRY,
    )
    CHEMNITZ = (
        "09Chemnitz",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_09",
        WeatherDataSourceEnum.DWD_TRY,
    )
    HOF = (
        "10Hof",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_10",
        WeatherDataSourceEnum.DWD_TRY,
    )
    FICHTELBERG = (
        "11Fichtelberg",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_11",
        WeatherDataSourceEnum.DWD_TRY,
    )
    MANNHEIM = (
        "12Mannheim",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_12",
        WeatherDataSourceEnum.DWD_TRY,
    )
    MUEHLDORF = (
        "13Muehldorf",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_13",
        WeatherDataSourceEnum.DWD_TRY,
    )
    STOETTEN = (
        "14Stoetten",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_14",
        WeatherDataSourceEnum.DWD_TRY,
    )
    GARMISCH_PARTENKIRCHEN = (
        "15Garmisch Partenkirchen",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_15",
        WeatherDataSourceEnum.DWD_TRY,
    )
    MADRID = (
        "Madrid",
        "NSRDB",
        "Madrid",
        "Madrid",
        WeatherDataSourceEnum.NSRDB,
    )
    SEVILLE = (
        "Seville",
        "NSRDB",
        "Seville",
        "Seville",
        WeatherDataSourceEnum.NSRDB,
    )
    ATHENS = (
        "Athens",
        "NSRDB",
        "Athens",
        "Athens",
        WeatherDataSourceEnum.NSRDB,
    )
    BELGRADE = (
        "Belgrade",
        "NSRDB",
        "Belgrade",
        "Belgrade",
        WeatherDataSourceEnum.NSRDB,
    )
    CYPRUS = (
        "Cyprus",
        "NSRDB",
        "Cyprus",
        "Cyprus",
        WeatherDataSourceEnum.NSRDB,
    )
    LJUBLIANA = (
        "Ljubljana",
        "NSRDB",
        "Ljubljana",
        "Ljubljana",
        WeatherDataSourceEnum.NSRDB,
    )
    MILAN = (
        "Milan",
        "NSRDB",
        "Milan",
        "Milan",
        WeatherDataSourceEnum.NSRDB,
    )
    SARAJEVO = (
        "Sarajevo",
        "NSRDB",
        "Sarajevo",
        "Sarajevo",
        WeatherDataSourceEnum.NSRDB,
    )
    VRANJE = (
        "Vranje",
        "NSRDB",
        "Vranje",
        "Vranje",
        WeatherDataSourceEnum.NSRDB,
    )
    FR = (
        "Paris",
        "NSRDB_15min",
        "Paris",
        "403286_48.85_2.34_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    DE = (
        "Potsdam",
        "NSRDB_15min",
        "Potsdam",
        "742114_52.41_13.06_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    PL = (
        "Warsaw",
        "NSRDB_15min",
        "Warsaw",
        "1138443_52.25_21.02_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    SE = (
        "Stockholm",
        "NSRDB_15min",
        "Stockholm",
        "984998_59.33_18.06_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    NO = (
        "Oslo",
        "NSRDB_15min",
        "Oslo",
        "653025_59.93_10.74_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    RS = (
        "Belgrad",
        "NSRDB_15min",
        "Belgrad",
        "1108363_44.77_20.46_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    IT = (
        "Rome",
        "NSRDB_15min",
        "Rome",
        "718838_41.89_12.50_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    GB = (
        "London",
        "NSRDB_15min",
        "London",
        "337089_51.49_-0.10_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    CY = (
        "Nicosia",
        "NSRDB_15min",
        "Nicosia",
        "1809004_35.17_33.38_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    GR = (
        "Athens",
        "NSRDB_15min",
        "Athens",
        "1291832_37.97_23.74_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    IE = (
        "Dublin",
        "NSRDB_15min",
        "Dublin",
        "165308_53.37_-6.26_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    SI = (
        "Ljubljana",
        "NSRDB_15min",
        "Ljubljana",
        "808557_46.05_14.50_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    CZ = (
        "Prague",
        "NSRDB_15min",
        "Prague",
        "804583_50.09_14.42_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    AT = (
        "Viena",
        "NSRDB_15min",
        "Viena",
        "902141_48.21_16.38_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    HU = (
        "Budapest",
        "NSRDB_15min",
        "Budapest",
        "1035927_47.49_19.06_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    BE = (
        "Uccle",
        "NSRDB_15min",
        "Uccle",
        "454992_50.81_4.34_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    ES = (
        "Malaga",
        "NSRDB_15min",
        "Malaga",
        "213028_36.73_-4.42_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    DK = (
        "Copenhagen",
        "NSRDB_15min",
        "Copenhagen",
        "721796_55.69_12.58_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    NL = (
        "Amsterdam",
        "NSRDB_15min",
        "Amsterdam",
        "469536_52.37_4.90_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )
    BG = (
        "Sofia",
        "NSRDB_15min",
        "Sofia",
        "1267064_42.69_23.30_2019.csv",
        WeatherDataSourceEnum.NSRDB_15MIN,
    )


@dataclass_json
@dataclass
class WeatherConfig(ConfigBase):
    """Configuration class for Weather.

    Decorated with dataclass_json like every other config class, so that
    serialization uses the dataclass field names verbatim (snake_case).
    """

    component_id: ComponentID
    location: str
    source_path: str
    data_source: WeatherDataSourceEnum
    predictive_control: bool

    @classmethod
    def get_main_classname(cls) -> str:
        """Get the name of the main class."""
        return Weather.get_full_classname()  # type: ignore[no-any-return]

    @classmethod
    def get_default(
        cls,
        location_entry: Union[LocationEnum, str],
        name: str = "Weather",
        component_id: Optional[ComponentID] = None,
        weather_direct_filepath: Optional[str] = None,
        weather_direct_data_source: Optional[WeatherDataSourceEnum] = None,
    ) -> "WeatherConfig":
        """Gets the default configuration for a given location."""

        if component_id is None:
            component_id = ComponentID(name=name)
        enum_entry = None
        # If location_entry is enum entry, use it directly
        if isinstance(location_entry, LocationEnum):
            enum_entry = location_entry

        # Read location_entry from enum
        elif isinstance(location_entry, str):
            enum_entry = getattr(LocationEnum, location_entry.strip(), None)

        if enum_entry is not None:
            location = enum_entry.value[0]
            path = os.path.join(
            utils.get_input_directory(),
            "weather",
            enum_entry.value[1],
            enum_entry.value[2],
            enum_entry.value[3],
            )
            data_source = enum_entry.value[4]

        # Use direct filepath
        else:
            if weather_direct_filepath is None:
                raise ValueError(
                    f"Location '{location_entry}' not found in Weather LocationEnum and no weather_direct_filepath was provided."
                )
            if not os.path.isfile(weather_direct_filepath):
                raise ValueError(
                    f"Weather data file not found: {weather_direct_filepath}")
            if weather_direct_data_source is None:
                raise ValueError(
                    f"No data source (data type) provided for weather_direct_filepath {weather_direct_filepath}."
                )
            if weather_direct_filepath.lower().endswith(".dat"):
                weather_direct_filepath = weather_direct_filepath[:-4]
            elif weather_direct_filepath.lower().endswith(".csv"):
                weather_direct_filepath = weather_direct_filepath[:-4]

            location = str(location_entry)
            path = weather_direct_filepath
            data_source = weather_direct_data_source

        config = WeatherConfig(
            component_id=component_id,
            location=location,
            source_path=path,
            data_source=data_source,
            predictive_control=False,
        )
        return config

    def identity(self) -> str:
        """Return a short string that says which weather this configuration reads: station, data set, file.

        Example: ``"Aachen/DWD_TRY/weather/test-reference-years_1995-2012_1-location/data_processed/aachen_center"``.
        Components whose cached results depend on the weather (PV, building) store this string in their
        own configuration, sized from the weather through the sizing engine, so that their cache keys
        include which weather they were computed with. See ``roadmap/pylpg_flakiness.md`` F7.

        It is a readable string rather than a hash because it is written into every recorded energy-system
        file. For a file under the repository's inputs directory the path relative to that directory is
        kept -- the machine-specific prefix says nothing about the data, but the directories below the
        inputs root are where the dataset families live (two of the shipped families could hold files of
        the same name), so dropping them would let two different datasets share one identity. A file
        outside the inputs directory contributes only its basename.

        Returns:
            str: ``<location>/<data source>/<inputs-relative path or basename>``.
        """
        inputs_directory = os.path.abspath(utils.get_input_directory())
        source = os.path.abspath(str(self.source_path))
        if self._is_under(inputs_directory, source):
            relative = os.path.relpath(source, inputs_directory)
        else:
            relative = os.path.basename(source)
        return f"{self.location}/{self.data_source.value}/{PurePath(relative).as_posix()}"

    @staticmethod
    def identity_facts(config: "WeatherConfig", ctx: Any) -> Dict[str, Any]:
        """Provide :meth:`identity` as the sizing fact ``weather_identity``.

        Registered in ``SIZING_CONTRIBUTIONS`` below; the sizing engine calls it with the resolved config.

        Args:
            config: this weather configuration.
            ctx: the sizing context; unused.

        Returns:
            Dict[str, Any]: ``{"weather_identity": config.identity()}``.
        """
        del ctx
        return {"weather_identity": config.identity()}

    @staticmethod
    def _is_under(directory: str, path: str) -> bool:
        """Whether ``path`` lies inside ``directory``, both absolute.

        Args:
            directory: the absolute directory.
            path: the absolute path to test.

        Returns:
            bool: True if ``path`` is ``directory`` or below it. False when the two are on different
            drives, where ``os.path.commonpath`` raises instead of answering.
        """
        try:
            return os.path.commonpath([directory, path]) == directory
        except ValueError:
            return False

    @preset(note="the repository's reference climate")
    @classmethod
    def preset_standard(cls, name: str) -> "WeatherConfig":
        """The reference weather of this repository: the DWD test reference year for Aachen.

        Aachen is the location every example household and almost every test in HiSim runs on,
        so it is the one climate a file may reference without saying anything further. Any other
        station is an open identifier space rather than a variant, which is why this class ships
        exactly this one preset and :meth:`for_location` for everything else.
        """
        return cls.for_location(name, location=LocationEnum.AACHEN)

    @constructor(note="the stations of LocationEnum and their shipped data sets")
    @classmethod
    def for_location(
        cls,
        name: str,
        location: LocationEnum,
        data_source: Optional[WeatherDataSourceEnum] = None,
    ) -> "WeatherConfig":
        """Builds the weather of one catalogue station, with its shipped data set and reader.

        A weather station is an identifier rather than a variant: there are dozens of them and
        the set grows whenever a data set is added, so minting one preset per station would turn
        arbitrary station names into permanent wire format. The catalogue entry carries
        everything the configuration needs — the display name, the directory the shipped time
        series lives in and the reader that understands its format — so naming the station is
        the whole of the call.

        Args:
            name: Instance name of the component being configured; it becomes its identity.
            location: The catalogue station to read the weather of.
            data_source: Reader to use instead of the one the catalogue entry names, for a data
                set that was re-exported in another format; the catalogue's own reader when
                omitted.

        Returns:
            A configuration reading that station's shipped time series.
        """
        display_name, directory, subdirectory, file_stem, catalogue_source = location.value
        return cls(
            component_id=ComponentID(name=name),
            location=display_name,
            source_path=os.path.join(
                utils.get_input_directory(), "weather", directory, subdirectory, file_stem
            ),
            data_source=data_source if data_source is not None else catalogue_source,
            predictive_control=False,
        )


# Declared after the class because it refers to it. Every scenario has exactly one weather, so the
# bare fact ``weather_identity`` binds to this contribution without any consumer naming a source.
WeatherConfig.SIZING_CONTRIBUTIONS = (
    FactContribution(facts=("weather_identity",), compute=WeatherConfig.identity_facts),
)


class Weather(Component):
    """Provide thermal and solar conditions of local weather."""

    cost_relevance = CostRelevance.FREE_OF_COST

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
        self.dry_bulb_list: List[float]
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
        entry = self.cache_entry(calculation_inputs)
        if entry.exists:
            log.information(f"Weather series cache hit: {entry.path}")
            # float_precision="round_trip": pandas' default CSV reader uses a fast, inexact float
            # parser, so without this a cached run and an uncached one are not the same run. See the
            # measurement in the commit that introduced this across the caches.
            weather_series = pd.read_csv(
                entry.path, sep=",", decimal=".", encoding="cp1252", float_precision="round_trip"
            )
        else:
            log.information(f"Weather series cache miss: computing it and filing it at {entry.path}")
            weather_series = produce_weather_series(calculation_inputs)
            with entry.writing() as temporary_cache_filepath:
                weather_series.to_csv(temporary_cache_filepath)
        self.read_series(weather_series)

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

    def cache_entry(self, calculation_inputs: WeatherSeriesInputs) -> CacheEntry:
        """Look the produced series up in the cache under the key of the producer that makes it.

        The key is ``sha256(artifact kind : code fingerprint : third-party fingerprint : DTO JSON)``
        (``roadmap/cache_service_spec.md`` §3). The two fingerprints are read from the producer
        module's import closure, so an edit to the calculation invalidates the entry by itself -- the
        thing the weather cache did not do when #628 fixed the direct normal irradiance and CI, running
        on a restored cache, reported all 24 golden pairs unchanged.

        Args:
            calculation_inputs: the DTO from :meth:`build_calculation_inputs`.

        Returns:
            CacheEntry: where the entry is or will be, and whether it is there.
        """
        key = CacheKey.for_producer(ARTIFACT_KIND, weather_calculation, calculation_inputs)
        return CacheClient.from_environment().lookup_producer(key, self.my_simulation_parameters.cache_dir_path)

    def read_series(self, weather_series: pd.DataFrame) -> None:
        """Take the component's per-timestep lists out of the produced frame.

        The same reading serves a cache hit and a fresh computation, so the two cannot drift apart.

        Args:
            weather_series: the frame from the cache or from the producer.
        """
        self.temperature_list = weather_series["t_out"].tolist()
        self.daily_average_outside_temperature_list_in_celsius = weather_series["t_out_daily_average"].tolist()
        self.dry_bulb_list = weather_series["DryBulb"].tolist()
        self.dhi_list = weather_series["DHI"].tolist()
        self.dni_list = weather_series["DNI"].tolist()
        self.dniextra_list = weather_series["DNIextra"].tolist()
        self.ghi_list = weather_series["GHI"].tolist()
        self.altitude_list = weather_series["altitude"].tolist()
        self.azimuth_list = weather_series["azimuth"].tolist()
        self.apparent_zenith_list = weather_series["apparent_zenith"].tolist()
        self.wind_speed_list = weather_series["Wspd"].tolist()
        try:
            self.pressure_list = weather_series["Pressure"].tolist()
        except KeyError:
            log.warning("Weather key 'Pressure' not found in cache; falling back to zeros.")
            self.pressure_list = [0] * len(self.wind_speed_list)

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

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
