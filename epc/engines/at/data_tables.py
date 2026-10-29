"""Every local OENORM table of the Austrian engine, loaded complete or refused.

The tables are licensed content of Austrian Standards: they live under ``epc/data/at/``, which git
ignores, and the repository carries only their data contract, ``epc/data/README.md`` (D7, R8.3). The
files, columns and row counts checked here and in :mod:`epc.engines.at.climate_tables` are the single
source of that contract (I-6). A missing file is refused with ``E-11``, a wrong row count with
``E-12``, a malformed header, cell, key or band with ``E-19``, each naming the file.

This module reads the non-climate tables itself, the usage profiles of B 8110-5 Tab. 7 and 9, the
temperature-correction factors of B 8110-6-1 Tab. 3–5 and the single constants of the prose clauses the
engine computes with (D7), and assembles :class:`AustrianTables` with the SHA-256 of every file, which a
result repeats under ``provenance.tables``. The dwelling-unit classes are read from their labels; nothing
here knows a value of a table or of a constant.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Dict, Mapping, Optional, Tuple

from epc.engines.at import climate_tables
from epc.engines.at import table_reading as reading
from epc.engines.at.climate_tables import (
    RADIATION_COEFFICIENTS_FILE,
    REFERENCE_CLIMATE_FILE,
    TEMPERATURE_COEFFICIENTS_FILE,
    TRANSPOSITION_FACTORS_FILE,
    RadiationCoefficients,
    ReferenceClimateTable,
    TemperatureCoefficients,
    TranspositionBand,
)

#: The folder the engine reads by default: ``epc/data/at``.
DEFAULT_DATA_FOLDER = Path(__file__).resolve().parents[2] / "data" / "at"

USAGE_PROFILES_FILE = "usage_profiles_b8110_5_2024_tab7_tab9.csv"
TEMPERATURE_CORRECTION_FACTORS_FILE = "temperature_correction_factors_b8110_6_1_2024_tab3_5.csv"
PROSE_CONSTANTS_FILE = "prose_constants_b8110_5_6_1_2024.csv"

#: The inputs and expected values of the B 8110-6-2 worked examples, read by the local validation only.
VALIDATION_EXAMPLES_FILE = "validation_examples_b8110_6_2_2024.csv"

#: Every table the engine computes with.
TABLE_FILES: Tuple[str, ...] = (
    REFERENCE_CLIMATE_FILE,
    TEMPERATURE_COEFFICIENTS_FILE,
    RADIATION_COEFFICIENTS_FILE,
    TRANSPOSITION_FACTORS_FILE,
    USAGE_PROFILES_FILE,
    TEMPERATURE_CORRECTION_FACTORS_FILE,
    PROSE_CONSTANTS_FILE,
)

USAGE_CLASSES = 3

#: The single constants of the prose clauses of B 8110-6-1 the engine computes with, by key; their values
#: are local like the tables (D7), the clause of each stands in the file.
PROSE_CONSTANT_KEYS: Tuple[str, ...] = (
    "reference_area_share",
    "gross_storey_height",
    "glass_share",
    "shading_factor_small",
    "shading_factor_medium",
    "shading_factor_large",
    "shading_small_up_to_units",
    "shading_medium_up_to_units",
    "thermal_bridge_factor",
    "thermal_bridge_u_offset",
    "thermal_bridge_minimum_share",
    "net_room_height",
    "air_heat_capacity",
    "glazing_non_perpendicular_factor",
    "glazing_soiling_factor",
)

#: The constants that count dwelling units and must be whole numbers.
_WHOLE_NUMBER_KEYS = ("shading_small_up_to_units", "shading_medium_up_to_units")

#: The rows of B 8110-6-1 Tab. 3 (outside air), Tab. 4 (unconditioned spaces) and Tab. 5 (ground) the
#: contract holds, by key; a conditioned neighbour takes no factor (Section 4, p. 8) and has no row.
CORRECTION_FACTOR_ROWS: Tuple[str, ...] = (
    "OUTSIDE_AIR",
    "WALL_TO_UNCONDITIONED_ATTIC",
    "CEILING_TO_UNCONDITIONED_ATTIC",
    "CEILING_TO_CLOSED_GARAGE",
    "WALL_TO_UNCONDITIONED_CELLAR_UNINSULATED",
    "CEILING_TO_UNCONDITIONED_CELLAR_UNINSULATED",
    "WALL_TO_UNCONDITIONED_CELLAR_INSULATED",
    "CEILING_TO_UNCONDITIONED_CELLAR_INSULATED",
    "WALL_TO_BUFFER_SPACE",
    "CEILING_TO_BUFFER_SPACE",
    "WALL_TO_GROUND_SHALLOW",
    "FLOOR_ON_GROUND_SHALLOW",
    "WALL_TO_GROUND_DEEP",
    "FLOOR_ON_GROUND_DEEP",
)

_CLASS_LABEL = re.compile(r"^(?P<first>\d+)(?:-(?P<last>\d+)|(?P<open>\+))$")


@dataclass(frozen=True)
class UsageProfileRow:
    """One residential row of B 8110-5 Tab. 7 and 9, for a range of dwelling units (``None`` is open above)."""

    label: str
    dwelling_units_from: int
    dwelling_units_up_to: Optional[int]
    theta_i_h_c: float
    n_l_hyg_per_h: float
    q_i_h_w_per_m2: float
    wwwb_wh_per_m2_d: float


@dataclass(frozen=True)
class TemperatureCorrectionFactor:
    """One row of B 8110-6-1 Tab. 3–5: the factor in the heating case and in the cooling case."""

    f_h: float
    f_c: float


@dataclass(frozen=True)
class AustrianTables:
    """Every table the Austrian engine computes with, complete and immutable, with their digests."""

    reference_climate: ReferenceClimateTable
    temperature_coefficients: Mapping[str, Tuple[TemperatureCoefficients, ...]]
    radiation_coefficients: Mapping[str, RadiationCoefficients]
    transposition_bands: Tuple[TranspositionBand, ...]
    usage_profiles: Tuple[UsageProfileRow, ...]
    temperature_correction_factors: Mapping[str, TemperatureCorrectionFactor]
    constants: Mapping[str, float]
    digests: Mapping[str, str]


def load_tables(data_folder: Path = DEFAULT_DATA_FOLDER) -> AustrianTables:
    """Loads every table of the Austrian engine.

    Args:
        data_folder: The folder holding the tables.

    Returns:
        The tables with the ``sha256:…`` digest of every file.

    Raises:
        EpcError: ``E-11`` for a missing file, ``E-12`` for a wrong row count, ``E-19`` for anything malformed.
    """
    folder = Path(data_folder)
    tables = AustrianTables(
        reference_climate=climate_tables.load_reference_climate(folder),
        temperature_coefficients=climate_tables.load_temperature_coefficients(folder),
        radiation_coefficients=climate_tables.load_radiation_coefficients(folder),
        transposition_bands=climate_tables.load_transposition_factors(folder),
        usage_profiles=_load_usage_profiles(folder),
        temperature_correction_factors=_load_temperature_correction_factors(folder),
        constants=_load_prose_constants(folder),
        digests=MappingProxyType({}),
    )
    digests = {file: f"sha256:{hashlib.sha256((folder / file).read_bytes()).hexdigest()}" for file in TABLE_FILES}
    return dataclasses.replace(tables, digests=MappingProxyType(digests))


def temperature_coefficients_for(
    tables: AustrianTables, region: str, elevation_in_m: float
) -> TemperatureCoefficients:
    """The Annex B band of a climate region whose elevation range contains the elevation.

    Raises:
        KeyError: For a region outside Bild 1, which the engine refuses before (``E-10``).
    """
    return climate_tables.band_containing(tables.temperature_coefficients[region], elevation_in_m)


def transposition_band_for(tables: AustrianTables, elevation_in_m: float) -> TranspositionBand:
    """The Annex E table whose elevation range contains the elevation."""
    return climate_tables.band_containing(tables.transposition_bands, elevation_in_m)


def usage_profile_for(tables: AustrianTables, dwelling_units: int) -> UsageProfileRow:
    """The usage row whose class contains the dwelling units (at least one, as the description guarantees)."""
    for profile in tables.usage_profiles:
        if profile.dwelling_units_from <= dwelling_units and (
            profile.dwelling_units_up_to is None or dwelling_units <= profile.dwelling_units_up_to
        ):
            return profile
    raise ValueError(f"No usage class contains {dwelling_units} dwelling units; the classes start at 1.")


def _load_usage_profiles(folder: Path) -> Tuple[UsageProfileRow, ...]:
    """Tab. 7 and 9: three residential classes that cover one dwelling unit and up without gap or overlap."""
    file = USAGE_PROFILES_FILE
    columns = ("class", "theta_i_h_c", "n_l_hyg_per_h", "q_i_h_w_per_m2", "wwwb_wh_per_m2_d")
    profiles: Dict[Tuple[str], Tuple[int, UsageProfileRow]] = {}
    for row in reading.read_rows(folder, file, columns):
        label = row[1]["class"]
        match = _CLASS_LABEL.match(label)
        if match is None:
            raise reading.malformed(file, row[0], f"class '{label}' is not of the form 'N-M' or 'N+'")
        last = match.group("last")
        profile = UsageProfileRow(
            label,
            int(match.group("first")),
            None if last is None else int(last),
            *(reading.number(file, row, column) for column in columns[1:]),
        )
        reading.store(file, row, profiles, (label,), (row[0], profile))
    reading.require_count(file, "the residential dwelling-unit classes", USAGE_CLASSES, len(profiles))
    ordered = sorted(profiles.values(), key=lambda entry: entry[1].dwelling_units_from)
    expected_from = 1
    for line, profile in ordered:
        if profile.dwelling_units_from != expected_from:
            raise reading.malformed(file, line, "the classes must cover 1 dwelling unit and up without gap or overlap")
        expected_from = profile.dwelling_units_up_to + 1 if profile.dwelling_units_up_to is not None else 0
    if ordered[-1][1].dwelling_units_up_to is not None:
        raise reading.malformed(file, ordered[-1][0], "the last class must be open above ('N+')")
    return tuple(profile for _, profile in ordered)


def _load_temperature_correction_factors(folder: Path) -> Mapping[str, TemperatureCorrectionFactor]:
    """Tab. 3–5: one row per key of :data:`CORRECTION_FACTOR_ROWS`, factors between 0 and 1."""
    file = TEMPERATURE_CORRECTION_FACTORS_FILE
    factors: Dict[str, TemperatureCorrectionFactor] = {}
    for row in reading.read_rows(folder, file, ("boundary", "f_h", "f_c", "table", "row_text")):
        key = row[1]["boundary"]
        if key not in CORRECTION_FACTOR_ROWS:
            raise reading.malformed(file, row[0], f"row '{key}' is not one of the rows of the contract")
        pair = (reading.number(file, row, "f_h"), reading.number(file, row, "f_c"))
        if not all(0 <= factor <= 1 for factor in pair):
            raise reading.malformed(file, row[0], "a temperature-correction factor lies outside 0 … 1")
        reading.store(file, row, factors, key, TemperatureCorrectionFactor(*pair))
    reading.require_count(file, "the rows of the contract", len(CORRECTION_FACTOR_ROWS), len(factors))
    return MappingProxyType(factors)


def _load_prose_constants(folder: Path) -> Mapping[str, float]:
    """The prose constants: one positive value per key of :data:`PROSE_CONSTANT_KEYS`, unit counts whole."""
    file = PROSE_CONSTANTS_FILE
    values: Dict[str, float] = {}
    for row in reading.read_rows(folder, file, ("key", "value", "unit", "norm", "clause")):
        key = row[1]["key"]
        if key not in PROSE_CONSTANT_KEYS:
            raise reading.malformed(file, row[0], f"key '{key}' is not one of the keys of the contract")
        value = reading.number(file, row, "value")
        if value <= 0:
            raise reading.malformed(file, row[0], f"{key} must be positive")
        if key in _WHOLE_NUMBER_KEYS and not value.is_integer():
            raise reading.malformed(file, row[0], f"{key} counts dwelling units and must be a whole number")
        reading.store(file, row, values, key, value)
    reading.require_count(file, "the keys of the contract", len(PROSE_CONSTANT_KEYS), len(values))
    return MappingProxyType(values)
