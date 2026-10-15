"""The result dictionary: what an engine computed for one building, in a shape every report can read.

An :class:`EpcResult` has a small common core every engine fills, free national keys beside it, and
an assumptions list naming every norm default the engine applied (design §3.4, §5.3; D14, D22). The
engine adds its indicators, its annual and monthly terms and its assumptions; the shell states what
was run (:class:`RunInfo`); :meth:`EpcResult.to_dict` checks the core and returns the JSON of
requirements Mockup A.

Keys are added once per section (I-8) and a monthly series has exactly twelve values, January to
December; both are engine programming errors and raise :class:`ValueError`. A result without its
common core is refused on writing with ``E-17`` (I-7).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union

from epc.model.building import BuildingDescription
from epc.model.errors import EpcError, EpcErrorKind

ClimateBasis = Literal["reference+site", "national"]
EntryValue = Union[float, str, Tuple[float, ...]]

#: The indicator keys every engine fills.
COMMON_CORE: Tuple[str, ...] = ("heating_demand_reference_climate", "indicator_reference_area")

#: The indicator key present exactly when the engine's method has a site climate.
SITE_CLIMATE_KEY = "heating_demand_site_climate"

#: The climate bases an engine can state; the first is the one with a site climate.
CLIMATE_BASES: Tuple[ClimateBasis, ...] = ("reference+site", "national")

#: The format of the written result.
RESULT_SCHEMA = "epc-result/1"

#: The length of a monthly series.
MONTHS = 12


@dataclass(frozen=True)
class Entry:
    """One indicator or annual term: a value with its unit and the clause it comes from.

    Attributes:
        value: The value.
        unit: Its unit.
        cites: The clauses or equations it comes from.
        label: The national symbol, if any.
        note: A remark a reader needs next to the value, if any.
    """

    value: EntryValue
    unit: str
    cites: str = ""
    label: Optional[str] = None
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """The entry as Mockup A writes it: ``value, unit, cites, label?, note?``."""
        written: Dict[str, Any] = {
            "value": list(self.value) if isinstance(self.value, tuple) else self.value,
            "unit": self.unit,
            "cites": self.cites,
        }
        if self.label is not None:
            written["label"] = self.label
        if self.note is not None:
            written["note"] = self.note
        return written


@dataclass(frozen=True)
class Assumption:
    """A norm default the engine applied because the description does not state the value.

    Attributes:
        key: What was assumed.
        value: The assumption as a reader should see it.
        source: The clause that prescribes it.
    """

    key: str
    value: str
    source: str


@dataclass(frozen=True)
class RunInfo:
    """What was run on which record; stated by the shell, which alone knows the file.

    Attributes:
        record: Path of the expanded record.
        building: Name of the building in the record.
        country: ISO 3166-1 alpha-2 code of the engine's country.
        engine: Name of the engine.
        edition: The norm edition the engine implements.
        climate_basis: The engine's climate basis.
        calculator: Name and version of the calculator.
        computed_at: Local time of the computation, ISO 8601 to the second.
    """

    record: str
    building: str
    country: str
    engine: str
    edition: str
    climate_basis: str
    calculator: str
    computed_at: str


class EpcResult:
    """The result of one engine for one building, filled by the engine and completed by the shell.

    Attributes:
        building: The description the engine computed from.
        run: What was run, set by the shell before :meth:`to_dict`.
        tables: File name → ``sha256:…`` digest of every data table the engine read.
    """

    def __init__(self, building: BuildingDescription) -> None:
        """Starts an empty result for one building.

        Args:
            building: The description the engine computes from.
        """
        self.building = building
        self.run: Optional[RunInfo] = None
        self.tables: Dict[str, str] = {}
        self._indicators: Dict[str, Entry] = {}
        self._annual: Dict[str, Entry] = {}
        self._monthly: Dict[str, Tuple[Tuple[float, ...], str]] = {}
        self._assumptions: List[Assumption] = []

    def add_indicator(
        self,
        key: str,
        value: EntryValue,
        unit: str,
        *,
        cites: str = "",
        label: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        """Adds one indicator; a key already present is refused (I-8)."""
        self._add(self._indicators, "indicator", key, Entry(value, unit, cites, label, note))

    def add_term(self, key: str, value: EntryValue, unit: str, *, cites: str = "") -> None:
        """Adds one annual term; a key already present is refused (I-8)."""
        self._add(self._annual, "annual term", key, Entry(value, unit, cites))

    def add_monthly(self, key: str, values: Sequence[float], unit: str) -> None:
        """Adds one monthly series, January to December; a repeated key or a length other than 12 is refused (I-8)."""
        if len(values) != MONTHS:
            raise ValueError(f"Monthly term '{key}' has {len(values)} values; a monthly series has {MONTHS}.")
        self._add(self._monthly, "monthly term", key, (tuple(values), unit))

    def assume(self, key: str, value: str, source: str) -> None:
        """Records one norm default the engine applied (R4.4)."""
        self._assumptions.append(Assumption(key, value, source))

    def to_dict(self) -> Dict[str, Any]:
        """The result as JSON-ready dictionary, Mockup A of the requirements.

        Returns:
            ``schema, run, indicators, terms (annual, monthly), building, site, assumptions, provenance``.

        Raises:
            EpcError: ``E-17`` when an indicator of the common core is missing, or the site-climate
                indicator under a ``reference+site`` engine.
            ValueError: When the shell has not stated the run, or a ``national`` engine returned a
                site-climate indicator.
        """
        if self.run is None:
            raise ValueError("The result states no run; the shell sets EpcResult.run before writing.")
        required = COMMON_CORE + ((SITE_CLIMATE_KEY,) if self.run.climate_basis == CLIMATE_BASES[0] else ())
        for key in required:
            if key not in self._indicators:
                raise EpcError(
                    EpcErrorKind.NO_COMMON_CORE, cc=self.run.country, key=key, COMMON_CORE=", ".join(required)
                )
        if SITE_CLIMATE_KEY in self._indicators and SITE_CLIMATE_KEY not in required:
            raise ValueError(
                f"Engine '{self.run.country}' has climate basis '{self.run.climate_basis}' and returned "
                f"'{SITE_CLIMATE_KEY}', which is present exactly when the basis is '{CLIMATE_BASES[0]}'."
            )
        provenance = dataclasses.asdict(self.building.provenance)
        provenance["configured_fields"] = list(self.building.provenance.configured_fields)
        provenance["tables"] = dict(self.tables)
        return {
            "schema": RESULT_SCHEMA,
            "run": dataclasses.asdict(self.run),
            "indicators": {key: entry.to_dict() for key, entry in self._indicators.items()},
            "terms": {
                "annual": {key: entry.to_dict() for key, entry in self._annual.items()},
                "monthly": {
                    key: {"unit": unit, "values": list(values)} for key, (values, unit) in self._monthly.items()
                },
            },
            "building": building_to_dict(self.building),
            "site": dataclasses.asdict(self.building.site),
            "assumptions": [dataclasses.asdict(assumption) for assumption in self._assumptions],
            "provenance": provenance,
        }

    @staticmethod
    def _add(section: Dict[str, Any], kind: str, key: str, value: Any) -> None:
        """Adds one key to one section, refusing a key already present."""
        if key in section:
            raise ValueError(f"The {kind} '{key}' was added twice; a result key is added once.")
        section[key] = value


def building_to_dict(building: BuildingDescription) -> Dict[str, Any]:
    """The ``building`` block of the result, in the expanded record's vocabulary plus the situations.

    Args:
        building: The description.

    Returns:
        The block of Mockup A.
    """
    return {
        "building_type": building.building_type,
        "conditioned_floor_area_in_m2": building.conditioned_floor_area_in_m2,
        "gross_volume_in_m3": building.gross_volume_in_m3,
        "heat_capacity_class": building.heat_capacity_class,
        "dwelling_units": {"value": building.dwelling_units, "configured": building.dwelling_units_configured},
        "elements": [
            {
                "name": element.name,
                "area_in_m2": element.area_in_m2,
                "u_value_in_watt_per_m2_per_kelvin": element.u_value_in_watt_per_m2_per_kelvin,
                "adjustment_factor": element.adjustment_factor_used_by_hisim,
                "situation": element.situation.value,
                "u_value_origin": element.u_value_origin,
                **({} if element.stated_correction_factor is None
                   else {"stated_correction_factor": element.stated_correction_factor}),
            }
            for element in building.elements
        ],
        "windows": [dataclasses.asdict(window) for window in building.windows],
        "thermal_bridging_surcharge_in_watt_per_m2_per_kelvin": building.thermal_bridging_surcharge_used_by_hisim,
    }
