"""The weather configuration: which station, which data set, which reader.

Holds :class:`WeatherConfig`, the catalogue of stations :class:`LocationEnum` names, the sizing
fact ``weather_identity`` that every component whose result depends on the weather copies into its
own configuration, and the sizing fact ``heating_reference_temperature_in_celsius``, the outside
design condition the place is heated for, which the building and the two generator-side readers
size from. :class:`WeatherDataSourceEnum`, which names the reader, lives in
:mod:`hisim.components.weather.calculation` and is imported from there; the package ``__init__``
explains why.
"""

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath
from typing import Any, ClassVar, Dict, Optional, Tuple

from dataclasses_json import dataclass_json

from hisim import utils
from hisim.components.weather.calculation import WeatherDataSourceEnum, WeatherSourceFiles
from hisim.config import ConfigBase, ComponentID, FactContribution, constructor, preset


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
    """Configuration class for Weather: the station, the file it is read from and the reader.

    The one preset is :meth:`preset_aachen`, the repository's reference climate; any other station
    comes from :meth:`for_location` and any file outside the catalogue from :meth:`for_data_file`.
    """

    MAIN_CLASS = "hisim.components.weather.Weather"

    component_id: ComponentID
    #: The station's display name, which labels the region a run is reported under.
    location: str
    #: The weather data on this machine, as the reader named by ``data_source`` expects it: a path
    #: with the extension for the sub-hourly readers, the stem for the ones that append their own.
    source_path: str
    data_source: WeatherDataSourceEnum
    #: The outside design condition a heating system at this place is sized for, in degrees
    #: Celsius. It is a property of the place rather than of a weather year -- it is the
    #: condition a heating load is computed against and it does not move from one year's time
    #: series to the next -- so it is stated and never computed: no table lookup, no law, and
    #: no default, which is why a weather nobody gave a design temperature cannot be built.
    heating_reference_temperature_in_celsius: float
    #: Whether the component publishes its 24 h forecast for predictive controllers to read.
    predictive_control: bool = False

    def identity(self) -> str:
        """Return a short string that says which weather this configuration reads: station, data set, file.

        Example: ``"Aachen/DWD_TRY/weather/test-reference-years_1995-2012_1-location/data_processed/aachen_center"``.
        Components whose cached results depend on the weather (PV, building) store this string in their
        own configuration, sized from the weather through the sizing engine, so that their cache keys
        include which weather they were computed with.

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

        Registered in :attr:`SIZING_CONTRIBUTIONS`; the sizing engine calls it with the resolved config.

        Args:
            config: this weather configuration.
            ctx: the sizing context; unused.

        Returns:
            Dict[str, Any]: ``{"weather_identity": config.identity()}``.
        """
        del ctx
        return {"weather_identity": config.identity()}

    @staticmethod
    def design_temperature_facts(config: "WeatherConfig", ctx: Any) -> Dict[str, Any]:
        """Provide :attr:`heating_reference_temperature_in_celsius` as the sizing fact of that name.

        Registered in :attr:`SIZING_CONTRIBUTIONS`; the sizing engine calls it with the resolved
        config and hands the number on to the building, the heat-distribution controller and the
        hplib heat pump, each of which reads it as a sized field.

        The value is the field's own, verbatim. It is a separate contribution from
        :meth:`identity_facts` rather than a second fact of that one because the two answer
        different questions -- which weather data a result was computed with, and what design
        condition the place is heated for -- and a compute function named after identity that also
        returned a temperature would be misnamed.

        Args:
            config: this weather configuration.
            ctx: the sizing context; unused, the number is stated on the field.

        Returns:
            Dict[str, Any]: ``{"heating_reference_temperature_in_celsius":
            config.heating_reference_temperature_in_celsius}``.
        """
        del ctx
        return {
            "heating_reference_temperature_in_celsius": config.heating_reference_temperature_in_celsius
        }

    #: Every scenario has exactly one weather, so the bare facts ``weather_identity`` and
    #: ``heating_reference_temperature_in_celsius`` bind to these contributions without any
    #: consumer naming a source.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(facts=("weather_identity",), compute=identity_facts),
        FactContribution(
            facts=("heating_reference_temperature_in_celsius",), compute=design_temperature_facts
        ),
    )

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
    def preset_aachen(cls, name: str) -> "WeatherConfig":
        """The reference weather of this repository: the DWD test reference year for Aachen.

        Aachen is the location every example household and almost every test in HiSim runs on,
        so it is the one climate a file may reference without saying anything further. Any other
        station is an open identifier space rather than a variant, which is why this class ships
        exactly this one preset and :meth:`for_location` for everything else.

        The design temperature it states is -7.0 °C, the conventional DIN 12831 figure for the
        Rhineland. It belongs to the station and not to the preset: another station states its
        own number at the :meth:`for_location` call that builds it.
        """
        return cls.for_location(
            name, location=LocationEnum.AACHEN, heating_reference_temperature_in_celsius=-7.0
        )

    @constructor(note="the stations of LocationEnum and their shipped data sets")
    @classmethod
    def for_location(
        cls,
        name: str,
        location: LocationEnum,
        heating_reference_temperature_in_celsius: float,
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
            heating_reference_temperature_in_celsius: The outside design condition a heating
                system at this station is sized for; stated by the caller, because the
                catalogue carries time series and not design conditions.
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
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
        )

    @constructor(note="a weather file this repository does not ship, named by path and reader")
    @classmethod
    def for_data_file(
        cls,
        name: str,
        path: str,
        data_source: WeatherDataSourceEnum,
        heating_reference_temperature_in_celsius: float,
    ) -> "WeatherConfig":
        """Builds the weather of a data file that is not one of the catalogue's shipped sets.

        :meth:`for_location` covers the stations of :class:`LocationEnum` and the time series that
        come with them. A file obtained anywhere else — a re-export for a country the catalogue does
        not carry, a measured year, a scenario data set — has no catalogue entry, and minting one per
        file would turn every path anyone ever reads into a permanent enum member. So here the file
        *is* the identifier: its path and the reader that understands its format, which are exactly
        the two things a catalogue entry would otherwise have supplied.

        The file has to exist, and it is named with its extension: a path that is there now but spelt
        wrong fails at the first timestep with a reader's own error rather than here, where the
        configuration can still say which file it looked for.

        What is *stored* is not always what was named, because the readers disagree about it.
        ``DWD_TRY`` and ``NSRDB`` open ``<source_path>.dat`` themselves — and a ``DWD_TRY`` station
        may have a ``.csv`` beside the ``.dat`` — so for those two the stored path is the stem and a
        trailing ``.dat`` or ``.csv`` comes off. The four sub-hourly readers (``NSRDB_15MIN``,
        ``DWD_10MIN``, ``DWD_15MIN``, ``ERA5``) open the stored path as it stands and need the
        extension kept. Which source does which is written down once, in
        :attr:`hisim.components.weather.calculation.WeatherSourceFiles.SUFFIXES`, and read from there
        rather than restated here.

        The ``location`` of the result is the file's own stem. It is a label — it names the region a
        run is reported under and it goes into :meth:`identity`, hence into the cache keys of every
        component sized from the weather — and for a file outside the catalogue the only honest label
        the call carries is the file's name.

        Args:
            name: Instance name of the component being configured; it becomes its identity.
            path: The weather file as it lies on this machine, extension included.
            data_source: The reader that understands the file's format.
            heating_reference_temperature_in_celsius: The outside design condition a heating
                system at the place this file describes is sized for; stated by the caller,
                because a data file carries a weather year and not a design condition.

        Returns:
            A configuration reading that file.

        Raises:
            ValueError: If ``path`` names no file.
        """
        if not os.path.isfile(path):
            raise ValueError(f"Weather data file not found: {path}")
        appended_suffixes = WeatherSourceFiles.SUFFIXES.get(data_source, ())
        reader_appends_an_extension = any(suffix for suffix in appended_suffixes)
        source_path = path
        if reader_appends_an_extension and source_path.lower().endswith((".dat", ".csv")):
            source_path = source_path[: -len(".dat")]
        return cls(
            component_id=ComponentID(name=name),
            location=os.path.splitext(os.path.basename(path))[0],
            source_path=source_path,
            data_source=data_source,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
        )
