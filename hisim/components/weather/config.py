"""The weather configuration: which station, which data set, which reader.

Part of the ``hisim.components.weather`` package split (see the package ``__init__`` for the layout).
Holds :class:`WeatherConfig`, the catalogue of stations :class:`LocationEnum` they are named by, and the
sizing fact ``weather_identity`` that every component whose result depends on the weather copies into
its own configuration.

:class:`WeatherDataSourceEnum` is not here but in :mod:`hisim.components.weather.calculation`, and this
module imports it from there. It names the reader, so it is key material for the cached weather series,
and the producer must be able to import it without importing this module. The reason is what the import
closure costs: a configuration module has to import ``hisim.config``, and a closure is computed by
reading source, so the lazy imports inside ``hisim.config``'s function bodies count as much as the ones
at the top of a file. ``ImportClosure.of(hisim.config)`` is 45 modules, ``hisim.component``, the
post-processing and the repository among them -- all of that would be hashed into every weather cache
key (``roadmap/cache_service_spec.md`` §3) and would throw the cached series away on edits that cannot
change a number in them. The layering rule of §12 names those three modules for the same reason.
"""

# clean

# pylint: disable=cyclic-import
# (the only backward edge is a runtime-local import of Weather inside get_main_classname;
# module import order is acyclic)

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath
from typing import Any, Dict, Optional, Union

from dataclasses_json import dataclass_json

from hisim import utils
from hisim.components.weather.calculation import WeatherDataSourceEnum
from hisim.config import ConfigBase, ComponentID, FactContribution, constructor, preset

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Noah Pflugradt"


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
        from hisim.components.weather.weather import Weather  # pylint: disable=import-outside-toplevel  # avoids config->component import cycle

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
