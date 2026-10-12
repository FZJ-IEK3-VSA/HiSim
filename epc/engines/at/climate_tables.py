"""The climate tables of OENORM B 8110-5:2024-03: reference climate and the site-climate coefficients.

Four files of ``epc/data/at/`` (``epc/data/README.md``): Annex A, the reference climate; Annex B, the
coefficients of the monthly temperature, Eq. (1); Annex D, the coefficients of the monthly global
radiation, Eq. (2); Annex E, the transposition factors, Eq. (3). Each is read into an immutable
structure and checked complete (I-6). The elevation bands of Annex B and E are read from the files and
must follow one another without gap or overlap, open at both ends, so that every elevation falls into
exactly one band.

Nothing here knows a value of a table: the grids (months, regions, azimuths, tilts, number of bands)
are the shape the contract prescribes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Dict, Mapping, Optional, Sequence, Tuple, TypeVar, Union

from epc.engines.at import table_reading as reading
from epc.engines.at.table_reading import MONTHS, Monthly, Row

REFERENCE_CLIMATE_FILE = "reference_climate_b8110_5_2024_annex_a.csv"
TEMPERATURE_COEFFICIENTS_FILE = "temperature_coefficients_b8110_5_2024_annex_b.csv"
RADIATION_COEFFICIENTS_FILE = "radiation_coefficients_b8110_5_2024_annex_d.csv"
TRANSPOSITION_FACTORS_FILE = "transposition_factors_b8110_5_2024_annex_e.csv"

#: The seven climate regions of OENORM B 8110-5:2024-03, Bild 1.
CLIMATE_REGIONS: Tuple[str, ...] = ("W", "NF", "N", "ZA", "SB", "S/SO", "N/SO")

#: Grid of Tab. A.2–A.7: sixteen orientations as azimuths from south (east negative), tilts 15° … 90°;
#: the horizontal surface of Tab. A.1 has no azimuth.
REFERENCE_AZIMUTHS: Tuple[float, ...] = tuple(22.5 * step for step in range(-7, 9))
REFERENCE_TILTS: Tuple[float, ...] = tuple(15.0 * step for step in range(1, 7))

#: Grid of Tab. E.1–E.7: azimuth 0° … 180° (printed with ±, east and west share a value), tilt 0° … 90°.
TRANSPOSITION_AZIMUTHS: Tuple[float, ...] = tuple(22.5 * step for step in range(9))
TRANSPOSITION_TILTS: Tuple[float, ...] = tuple(15.0 * step for step in range(7))

TEMPERATURE_BANDS_PER_REGION = 3
TRANSPOSITION_BANDS = 7

_NUMBER = r"\d+(?:\.\d+)?"
_BAND_LABEL = re.compile(
    rf"^(?:<=(?P<only_up_to>{_NUMBER})|(?P<above>{_NUMBER})<h<=(?P<up_to>{_NUMBER})|>(?P<only_above>{_NUMBER}))$"
)


@dataclass(frozen=True)
class ElevationRange:
    """An elevation band: above ``above_m`` (exclusive) up to ``up_to_m`` (inclusive); ``None`` is open."""

    above_m: Optional[float]
    up_to_m: Optional[float]

    def contains(self, elevation_in_m: float) -> bool:
        """Whether the elevation lies in the band."""
        return (self.above_m is None or elevation_in_m > self.above_m) and (
            self.up_to_m is None or elevation_in_m <= self.up_to_m
        )


@dataclass(frozen=True)
class ReferenceClimateTable:
    """Tab. A.1–A.7: monthly temperature, horizontal radiation and radiation by (tilt, azimuth from south)."""

    temperatures_c: Monthly
    horizontal_radiation_kwh_per_m2: Monthly
    radiation_kwh_per_m2: Mapping[Tuple[float, float], Monthly]


@dataclass(frozen=True)
class TemperatureCoefficients:
    """One elevation band of Tab. B.1–B.7: the monthly coefficients a and b of Eq. (1)."""

    band: str
    elevation_range: ElevationRange
    a: Monthly
    b: Monthly


@dataclass(frozen=True)
class RadiationCoefficients:
    """One region of Tab. D.1–D.14: the monthly coefficients a2, a1, a0 of Eq. (2)."""

    a2: Monthly
    a1: Monthly
    a0: Monthly


@dataclass(frozen=True)
class TranspositionBand:
    """One table of E.1–E.7: the monthly factors F_T by (azimuth 0 … 180, tilt)."""

    band: str
    elevation_range: ElevationRange
    factors: Mapping[Tuple[float, float], Monthly]


def load_reference_climate(folder: Path) -> ReferenceClimateTable:
    """Annex A: 12 months × (1 horizontal surface + 16 orientations × 6 tilts) rows."""
    file = REFERENCE_CLIMATE_FILE
    temperatures: Dict[Tuple[int], float] = {}
    horizontal: Dict[int, float] = {}
    tilted: Dict[Tuple[float, float, int], float] = {}
    for row in reading.read_rows(folder, file, ("month", "tilt_deg", "azimuth_deg", "value_kwh_per_m2", "theta_e_c")):
        month = reading.month(file, row)
        tilt = reading.number(file, row, "tilt_deg")
        if tilt == 0:
            if row[1]["azimuth_deg"] != "":
                raise reading.malformed(file, row[0], "the horizontal surface has no azimuth")
            reading.store(file, row, temperatures, (month,), reading.number(file, row, "theta_e_c"))
            horizontal[month] = reading.number(file, row, "value_kwh_per_m2")
            continue
        if row[1]["theta_e_c"] != "":
            raise reading.malformed(file, row[0], "theta_e_c belongs to the horizontal rows only")
        reading.member(file, row, "tilt_deg", REFERENCE_TILTS)
        azimuth = reading.member(file, row, "azimuth_deg", REFERENCE_AZIMUTHS)
        reading.store(file, row, tilted, (tilt, azimuth, month), reading.number(file, row, "value_kwh_per_m2"))
    expected = len(MONTHS) * (1 + len(REFERENCE_AZIMUTHS) * len(REFERENCE_TILTS))
    dimension = "12 months × (1 horizontal surface + 16 orientations × 6 tilts)"
    reading.require_count(file, dimension, expected, len(temperatures) + len(tilted))
    return ReferenceClimateTable(
        temperatures_c=reading.monthly({month: temperatures[(month,)] for month in MONTHS}),
        horizontal_radiation_kwh_per_m2=reading.monthly(horizontal),
        radiation_kwh_per_m2=MappingProxyType(
            {
                (tilt, azimuth): reading.monthly({month: tilted[(tilt, azimuth, month)] for month in MONTHS})
                for tilt in REFERENCE_TILTS
                for azimuth in REFERENCE_AZIMUTHS
            }
        ),
    )


def load_temperature_coefficients(folder: Path) -> Mapping[str, Tuple[TemperatureCoefficients, ...]]:
    """Annex B: 7 regions × 3 elevation bands × 12 months rows; the bands of each region contiguous."""
    file = TEMPERATURE_COEFFICIENTS_FILE
    values: Dict[Tuple[str, str, int], Tuple[float, float]] = {}
    ranges: Dict[Tuple[str, str], Tuple[ElevationRange, int]] = {}
    for row in reading.read_rows(folder, file, ("region", "band", "month", "a", "b")):
        region, band = _region(file, row), row[1]["band"]
        ranges.setdefault((region, band), (_elevation_range_of_label(file, row, band), row[0]))
        pair = (reading.number(file, row, "a"), reading.number(file, row, "b"))
        reading.store(file, row, values, (region, band, reading.month(file, row)), pair)
    expected = len(CLIMATE_REGIONS) * TEMPERATURE_BANDS_PER_REGION * len(MONTHS)
    reading.require_count(file, "7 regions × 3 elevation bands × 12 months", expected, len(values))
    result = {}
    for region in CLIMATE_REGIONS:
        bands = {band: ranges[(owner, band)] for (owner, band) in ranges if owner == region}
        reading.require_count(file, f"elevation bands of region {region}", TEMPERATURE_BANDS_PER_REGION, len(bands))
        ordered = _contiguous(file, f"region {region}", bands)
        result[region] = tuple(
            TemperatureCoefficients(
                band=band,
                elevation_range=bands[band][0],
                a=reading.monthly({month: values[(region, band, month)][0] for month in MONTHS}),
                b=reading.monthly({month: values[(region, band, month)][1] for month in MONTHS}),
            )
            for band in ordered
        )
    return MappingProxyType(result)


def load_radiation_coefficients(folder: Path) -> Mapping[str, RadiationCoefficients]:
    """Annex D: 7 regions × 12 months rows."""
    file = RADIATION_COEFFICIENTS_FILE
    values: Dict[Tuple[str, int], Tuple[float, ...]] = {}
    for row in reading.read_rows(folder, file, ("region", "month", "a2", "a1", "a0")):
        coefficients = tuple(reading.number(file, row, column) for column in ("a2", "a1", "a0"))
        reading.store(file, row, values, (_region(file, row), reading.month(file, row)), coefficients)
    reading.require_count(file, "7 regions × 12 months", len(CLIMATE_REGIONS) * len(MONTHS), len(values))
    return MappingProxyType(
        {
            region: RadiationCoefficients(
                *(reading.monthly({month: values[(region, month)][index] for month in MONTHS}) for index in range(3))
            )
            for region in CLIMATE_REGIONS
        }
    )


def load_transposition_factors(folder: Path) -> Tuple[TranspositionBand, ...]:
    """Annex E: 7 elevation bands × 12 months × 9 azimuths × 7 tilts rows; the bands contiguous."""
    file = TRANSPOSITION_FACTORS_FILE
    columns = ("band", "elevation_above_m", "elevation_up_to_m", "month", "azimuth_deg", "tilt_deg", "f_t")
    values: Dict[Tuple[str, float, float, int], float] = {}
    ranges: Dict[str, Tuple[ElevationRange, int]] = {}
    for row in reading.read_rows(folder, file, columns):
        band = row[1]["band"]
        elevation_range = ElevationRange(
            reading.optional_number(file, row, "elevation_above_m"),
            reading.optional_number(file, row, "elevation_up_to_m"),
        )
        if ranges.setdefault(band, (elevation_range, row[0]))[0] != elevation_range:
            raise reading.malformed(file, row[0], f"band {band} states a second elevation range")
        azimuth = reading.member(file, row, "azimuth_deg", TRANSPOSITION_AZIMUTHS)
        tilt = reading.member(file, row, "tilt_deg", TRANSPOSITION_TILTS)
        key = (band, azimuth, tilt, reading.month(file, row))
        reading.store(file, row, values, key, reading.number(file, row, "f_t"))
    per_band = len(MONTHS) * len(TRANSPOSITION_AZIMUTHS) * len(TRANSPOSITION_TILTS)
    dimension = "7 elevation bands × 12 months × 9 azimuths × 7 tilts"
    reading.require_count(file, dimension, TRANSPOSITION_BANDS * per_band, len(values))
    reading.require_count(file, "elevation bands", TRANSPOSITION_BANDS, len(ranges))
    return tuple(
        TranspositionBand(
            band=band,
            elevation_range=ranges[band][0],
            factors=MappingProxyType(
                {
                    (azimuth, tilt): reading.monthly({month: values[(band, azimuth, tilt, month)] for month in MONTHS})
                    for azimuth in TRANSPOSITION_AZIMUTHS
                    for tilt in TRANSPOSITION_TILTS
                }
            ),
        )
        for band in _contiguous(file, "Annex E", ranges)
    )


Banded = TypeVar("Banded", bound=Union[TemperatureCoefficients, TranspositionBand])


def band_containing(bands: Sequence[Banded], elevation_in_m: float) -> Banded:
    """The band whose elevation range contains the elevation; the bands are contiguous and open at both ends."""
    for band in bands:
        if band.elevation_range.contains(elevation_in_m):
            return band
    raise ValueError(f"No elevation band contains {elevation_in_m} m; the bands were checked contiguous on loading.")


def _region(file: str, row: Row) -> str:
    """The climate region of a row; ``E-19`` for a code outside Bild 1."""
    region = row[1]["region"]
    if region not in CLIMATE_REGIONS:
        raise reading.malformed(file, row[0], f"region '{region}' is not one of {list(CLIMATE_REGIONS)}")
    return region


def _elevation_range_of_label(file: str, row: Row, label: str) -> ElevationRange:
    """A band label of Tab. B.1–B.7 as printed in the header: ``<=X``, ``X<h<=Y`` or ``>X`` (m)."""
    match = _BAND_LABEL.match(label)
    if match is None:
        raise reading.malformed(file, row[0], f"band '{label}' is not of the form '<=X', 'X<h<=Y' or '>X'")
    groups = {name: float(text) for name, text in match.groupdict().items() if text is not None}
    return ElevationRange(groups.get("above", groups.get("only_above")), groups.get("up_to", groups.get("only_up_to")))


def _lower_limit(elevation_range: ElevationRange) -> float:
    """The exclusive lower limit of a band, minus infinity when it is open below."""
    return -math.inf if elevation_range.above_m is None else elevation_range.above_m


def _contiguous(file: str, what: str, bands: Mapping[str, Tuple[ElevationRange, int]]) -> Tuple[str, ...]:
    """The band names from low to high; ``E-19`` unless they follow one another, open at both ends."""
    ordered = sorted(bands, key=lambda name: _lower_limit(bands[name][0]))
    for lower, upper in zip(ordered, ordered[1:]):
        if bands[lower][0].up_to_m != bands[upper][0].above_m:
            raise reading.malformed(file, bands[upper][1], f"the elevation bands of {what} leave a gap or overlap")
    for name in ordered:
        above, up_to = bands[name][0].above_m, bands[name][0].up_to_m
        if above is not None and up_to is not None and above >= up_to:
            raise reading.malformed(file, bands[name][1], f"the elevation band {name} of {what} is inverted")
    if bands[ordered[0]][0].above_m is not None or bands[ordered[-1]][0].up_to_m is not None:
        raise reading.malformed(file, bands[ordered[0]][1], f"the elevation bands of {what} are not open at both ends")
    return tuple(ordered)
