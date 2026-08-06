"""TABULA building-code lookup for the RenoVisor translator (spec section 4.2).

Builds an in-memory index of the generic example codes
(``<CC>.N.<TYPE>.<band>.Gen.ReEx.001.<variant>``) from the processed TABULA CSV that the
``Building`` component reads, and selects the code matching a country, building type,
construction year and refurbishment variant — with nearest-neighbour fallbacks for the gaps
in the table (e.g. the Irish apartment archetypes lack the early age bands).
"""

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Set, Tuple

from hisim import utils

# Matches only the generic example variants; special sub-typologies (e.g. IE.N.SFH.01.325SB...)
# are excluded on purpose.
_CODE_PATTERN = re.compile(
    r"^(?P<country>[A-Z]{2})\.N\.(?P<building_type>[A-Z]+)\.(?P<band>\d{2})\.Gen\.ReEx\.001\.0*(?P<variant>\d+)$"
)


class TabulaLookupError(Exception):
    """Raised when no TABULA code exists for the requested country/building type."""


@dataclass
class AgeBand:
    """One TABULA construction-year class of a (country, building type) pair.

    ``variants`` only contains rows the HiSim ``Building`` component can consume: rows without
    door or window geometry (``A_Door_1``/``A_Window_*`` = 0, common in the Irish data) make it
    crash and are therefore excluded from selection.
    """

    band: str
    year_start: int
    year_end: int
    variants: Set[int] = field(default_factory=set)
    excluded_variants: Set[int] = field(default_factory=set)


@dataclass
class BuildingCodeSelection:
    """A selected TABULA code plus notes about any fallbacks that were applied."""

    building_code: str
    notes: List[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def _load_index() -> Dict[Tuple[str, str], List[AgeBand]]:
    """Parse the processed TABULA CSV into ``(country, type) -> age bands`` (cached)."""
    index: Dict[Tuple[str, str], Dict[str, AgeBand]] = {}
    # The processed TABULA CSV contains Latin-1 umlauts; the columns used here are plain ASCII.
    with open(utils.HISIMPATH["housing"], encoding="latin-1") as csv_file:
        for row in csv.DictReader(csv_file, delimiter=";"):
            match = _CODE_PATTERN.match(row.get("Code_BuildingVariant", "") or "")
            if match is None:
                continue
            try:
                year_start = int(row["Year1_Building"])
                year_end = int(row["Year2_Building"])
            except (KeyError, ValueError):
                continue
            key = (match.group("country"), match.group("building_type"))
            bands = index.setdefault(key, {})
            band = bands.setdefault(
                match.group("band"), AgeBand(band=match.group("band"), year_start=year_start, year_end=year_end)
            )
            if _is_usable_by_building_component(row):
                band.variants.add(int(match.group("variant")))
            else:
                band.excluded_variants.add(int(match.group("variant")))
    return {key: sorted(bands.values(), key=lambda entry: entry.band) for key, bands in index.items()}


def _is_usable_by_building_component(row: Dict[str, str]) -> bool:
    """Check that a TABULA row has the geometry the ``Building`` component divides by.

    ``Building`` computes area-weighted door/window U-values and crashes with a
    ``ZeroDivisionError`` on rows without a door (``A_Door_1`` = 0) or without windows.
    """
    door_area = _parse_decimal(row.get("A_Door_1"))
    window_area = _parse_decimal(row.get("A_Window_1")) + _parse_decimal(row.get("A_Window_2"))
    return door_area > 0 and window_area > 0


def _parse_decimal(raw: object) -> float:
    """Parse a TABULA numeric cell, accepting the German decimal comma; blanks become 0."""
    try:
        return float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def available_countries() -> Set[str]:
    """Return the country codes that have generic example codes in the TABULA table."""
    return {country for country, _ in _load_index()}


def select_building_code(
    country: str, building_type: str, construction_year: int, refurbishment_variant: int
) -> BuildingCodeSelection:
    """Select the TABULA code for the given parameters, applying documented fallbacks.

    Fallbacks (each is recorded as a note for the mapping report): the nearest age band when
    the construction year falls outside every band or the exact band is missing, and the
    nearest available refurbishment variant when the requested one does not exist
    (ties resolve to the *less* refurbished variant).
    """
    all_bands = _load_index().get((country, building_type))
    if not all_bands:
        raise TabulaLookupError(
            f"No TABULA codes for country '{country}' and building type '{building_type}' in the processed table."
        )
    bands = [entry for entry in all_bands if entry.variants]
    if not bands:
        raise TabulaLookupError(
            f"All TABULA rows for '{country}.{building_type}' lack door/window geometry and cannot be simulated."
        )
    selection_notes: List[str] = []

    band = next((entry for entry in bands if entry.year_start <= construction_year <= entry.year_end), None)
    if band is None:
        band = min(bands, key=lambda entry: _distance_to_range(construction_year, entry))
        exact_band = next(
            (entry for entry in all_bands if entry.year_start <= construction_year <= entry.year_end), None
        )
        if exact_band is not None:
            selection_notes.append(
                f"band {exact_band.band} ({exact_band.year_start}-{exact_band.year_end}) matches construction year "
                f"{construction_year} but its TABULA rows lack door/window geometry (unusable with the Building "
                f"component); using nearest usable band {band.band} ({band.year_start}-{band.year_end})"
            )
        else:
            selection_notes.append(
                f"no {country}.{building_type} age band covers construction year {construction_year}; "
                f"using nearest band {band.band} ({band.year_start}-{band.year_end})"
            )

    variant = refurbishment_variant
    if variant not in band.variants:
        variant = min(sorted(band.variants), key=lambda candidate: abs(candidate - refurbishment_variant))
        selection_notes.append(
            f"refurbishment variant .00{refurbishment_variant} not available for band {band.band}; "
            f"using nearest available .00{variant}"
        )

    code = f"{country}.N.{building_type}.{band.band}.Gen.ReEx.001.{variant:03d}"
    return BuildingCodeSelection(building_code=code, notes=selection_notes)


def _distance_to_range(year: int, band: AgeBand) -> int:
    """Distance in years between *year* and the band's year range (0 if inside)."""
    if year < band.year_start:
        return band.year_start - year
    if year > band.year_end:
        return year - band.year_end
    return 0
