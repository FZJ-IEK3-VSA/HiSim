"""Every way the calculator refuses a run, each with a stable id and one message template.

The calculator guesses nothing: a run it cannot compute exactly is refused, naming the offending
party (the country, the file, the building, the field) and, where the set of admissible values is
known, that set. Every refusal is an :class:`EpcError`, so ``run`` can stop at the first one and
``batch`` can record it and continue with the next run (D18), without either knowing which step
raised it.

The ids ``E-01`` … ``E-17`` and their templates are those of ``roadmap/epc/design.md`` §8, held here
once so that a message cannot drift between the places that raise it. ``E-18`` adds the value checks
of the building description, which §5.2 states without an id of its own, and ``E-19`` a data table
that is present but malformed (a cell, a key, a band), beside ``E-12`` for a wrong row count. The
engine ids (``E-09`` … ``E-14``, ``E-19``) are listed although no registered engine raises them yet,
so that the catalogue lives in one place.
"""

from __future__ import annotations

import enum
from typing import Any, Dict, Optional


@enum.unique
class EpcErrorKind(enum.Enum):
    """The stable id of every refusal, in the order of design §8."""

    NO_ENGINE = "E-01"
    NO_EXPANDED_RECORD = "E-02"
    NO_BUILDING = "E-03"
    SCHEMA_VERSION = "E-04"
    MISSING_FIELD = "E-05"
    UNKNOWN_ADJUSTMENT_FACTOR = "E-06"
    NO_DWELLING_UNITS = "E-07"
    WINDOWS_WITHOUT_DIRECTION = "E-08"
    SITE_FIELD_MISSING = "E-09"
    SITE_VALUE_NOT_ADMISSIBLE = "E-10"
    TABLE_FILE_MISSING = "E-11"
    TABLE_INCOMPLETE = "E-12"
    WINDOW_ANGLE_OFF_GRID = "E-13"
    NON_POSITIVE_LOSSES = "E-14"
    NO_RECORD_UNDER_ROOT = "E-15"
    OUTPUT_DIRECTORY_NOT_EMPTY = "E-16"
    NO_COMMON_CORE = "E-17"
    VALUE_NOT_ADMISSIBLE = "E-18"
    TABLE_MALFORMED = "E-19"


#: The message template of every id; the placeholders are filled by the raising call.
MESSAGES: Dict[EpcErrorKind, str] = {
    EpcErrorKind.NO_ENGINE: (
        "EPC calculation is not possible: no EPC engine is implemented for country '{cc}' yet (implemented: {codes})."
    ),
    EpcErrorKind.NO_EXPANDED_RECORD: (
        "No building.expanded.yaml in '{dir}'; the run was recorded before HiSim wrote it and is re-simulated "
        "(input_contract.md)."
    ),
    EpcErrorKind.NO_BUILDING: "The record '{path}' holds no building; the EPC is computed per building.",
    EpcErrorKind.SCHEMA_VERSION: "'{path}' is {found}; this calculator reads building-expanded/1 (input_contract.md).",
    EpcErrorKind.MISSING_FIELD: (
        "Building '{name}': the expanded record states no valid value for '{field}' (contract §2.3)."
    ),
    EpcErrorKind.UNKNOWN_ADJUSTMENT_FACTOR: (
        "Building '{name}', element '{element}': HiSim's adjustment factor {b} has no situation; the calculator "
        "knows 1, 0.5 and 0 (design.md §7.2)."
    ),
    EpcErrorKind.NO_DWELLING_UNITS: (
        "Building '{name}': dwelling units are 0 (HiSim's estimate); state 'number_of_apartments' in the building "
        "configuration."
    ),
    EpcErrorKind.WINDOWS_WITHOUT_DIRECTION: (
        "Building '{name}': {area} m² of windows but no direction with an area; the solar gains need orientations "
        "(D27)."
    ),
    EpcErrorKind.SITE_FIELD_MISSING: (
        "Building '{name}': the site block states no '{field}'; the {cc} engine needs {fields} (from HiSim's weather "
        "configuration)."
    ),
    EpcErrorKind.SITE_VALUE_NOT_ADMISSIBLE: (
        "Building '{name}': site field '{field}' = '{value}' is not admissible for {cc}; use {admissible}."
    ),
    EpcErrorKind.TABLE_FILE_MISSING: (
        "The {country} engine needs its tables under '{folder}' ({file} is missing); see epc/data/README.md."
    ),
    EpcErrorKind.TABLE_INCOMPLETE: "{file}: expected {n} rows for {dimension}, found {m}.",
    EpcErrorKind.WINDOW_ANGLE_OFF_GRID: (
        "Window '{name}': azimuth {a}° / tilt {t}° is not a tabulated angle of OENORM B 8110-5 Annex A/E "
        "(interpolation: P10)."
    ),
    EpcErrorKind.NON_POSITIVE_LOSSES: (
        "Month {m}: the reference-climate balance has losses ≤ 0 (γ = {value}); H 5050-1:2024 Eq. (9) states no rule "
        "(Q-D1)."
    ),
    EpcErrorKind.NO_RECORD_UNDER_ROOT: (
        "No building.expanded.yaml found under '{root}'; the runs were recorded before HiSim wrote it."
    ),
    EpcErrorKind.OUTPUT_DIRECTORY_NOT_EMPTY: "Output directory '{dir}' is not empty; give a new or empty one (D28).",
    EpcErrorKind.NO_COMMON_CORE: "Engine '{cc}' returned no '{key}'; every engine fills {COMMON_CORE}.",
    EpcErrorKind.VALUE_NOT_ADMISSIBLE: "Building '{name}': '{field}' = {value} is not admissible ({rule}).",
    EpcErrorKind.TABLE_MALFORMED: "{file}, line {line}: {problem}.",
}


class EpcError(Exception):
    """A refusal of the calculator: one id, one message built from its template.

    Attributes:
        kind: The id of the refusal.
        message: The filled template, without the id.
        building: The building the refusal concerns, when the caller knows it; the batch summary
            names it. ``None`` for a refusal of the whole run or of the arguments.
    """

    def __init__(self, kind: EpcErrorKind, **fields: Any) -> None:
        """Builds the refusal from its id and the placeholders of its template.

        Args:
            kind: The id of the refusal.
            **fields: One value per placeholder of the id's template in :data:`MESSAGES`.
        """
        self.kind = kind
        self.message = MESSAGES[kind].format(**fields)
        self.building: Optional[str] = None
        super().__init__(f"{kind.value}: {self.message}")

    @property
    def error_id(self) -> str:
        """The stable id, ``E-nn``."""
        return self.kind.value
