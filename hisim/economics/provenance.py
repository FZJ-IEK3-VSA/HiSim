"""Provenance ledger: from any result value back to its sources (cost_spec.md §3.10)."""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from hisim.economics.uncertainty import UncertainValue


class ParameterOrigin(str, enum.Enum):
    """Everything that can feed a number into the evaluation."""

    DATABASE_ENTRY = "DATABASE_ENTRY"
    CONFIG_OVERRIDE = "CONFIG_OVERRIDE"
    REQUEST = "REQUEST"
    SCENARIO_OVERLAY = "SCENARIO_OVERLAY"
    ENGINE_DEFAULT = "ENGINE_DEFAULT"
    SIMULATION_OUTPUT = "SIMULATION_OUTPUT"

    # Origins that legitimately carry no source ids:
    @property
    def requires_sources(self) -> bool:
        """Every record except simulation outputs and enumerated engine defaults needs sources."""
        return self not in (ParameterOrigin.SIMULATION_OUTPUT, ParameterOrigin.ENGINE_DEFAULT)


@dataclass(frozen=True)
class ParameterProvenance:
    """One interned, immutable record of a resolved input."""

    parameter: str  # dotted path, e.g. "devices_DE.HEAT_PUMP@2024.specific_investment"
    value: Union[UncertainValue, float, str]
    origin: ParameterOrigin
    data_file: Optional[str] = None  # file / entry key / valid_from_year for DATABASE_ENTRY
    source_ids: Tuple[str, ...] = ()
    detail: Optional[str] = None  # override_source text, request field name, scenario id, ...

    def to_json(self) -> dict:
        """Serializes for cost_provenance.json."""
        value: Any = self.value
        if isinstance(value, UncertainValue):
            value = value.to_json()
        return {
            "parameter": self.parameter,
            "value": value,
            "origin": self.origin.value,
            "data_file": self.data_file,
            "source_ids": list(self.source_ids),
            "detail": self.detail,
        }


class ProvenanceLedger:
    """Interns :class:`ParameterProvenance` records and assigns stable integer ids."""

    def __init__(self) -> None:
        """Empty ledger."""
        self._records: List[ParameterProvenance] = []
        self._index: Dict[ParameterProvenance, int] = {}

    def record(self, record: ParameterProvenance) -> int:
        """Interns a record and returns its id; identical records share one id."""
        if record.origin.requires_sources and not record.source_ids:
            raise ValueError(
                f"Provenance record for {record.parameter!r} with origin {record.origin.value} "
                "has no source ids — a datapoint without a source cannot enter a calculation (§3.10)."
            )
        existing = self._index.get(record)
        if existing is not None:
            return existing
        record_id = len(self._records)
        self._records.append(record)
        self._index[record] = record_id
        return record_id

    def get(self, record_id: int) -> ParameterProvenance:
        """Record by id."""
        return self._records[record_id]

    def __len__(self) -> int:
        """Number of interned records."""
        return len(self._records)

    @property
    def records(self) -> List[ParameterProvenance]:
        """All records in id order."""
        return list(self._records)

    def to_json(self) -> dict:
        """Full ledger for cost_provenance.json."""
        return {"records": [record.to_json() for record in self._records]}

    @classmethod
    def from_json(cls, data: dict) -> "ProvenanceLedger":
        """Rehydrates a stored ledger (for offline `explain` on archived results)."""
        ledger = cls()
        for raw in data.get("records", []):
            value = raw.get("value")
            if isinstance(value, dict) and {"min", "avg", "max"} <= set(value.keys()):
                value = UncertainValue.from_json(value)
            ledger._records.append(  # noqa: SLF001 — controlled rehydration
                ParameterProvenance(
                    parameter=raw["parameter"],
                    value=value,
                    origin=ParameterOrigin(raw["origin"]),
                    data_file=raw.get("data_file"),
                    source_ids=tuple(raw.get("source_ids", [])),
                    detail=raw.get("detail"),
                )
            )
        return ledger


@dataclass
class ResolvedSource:
    """A fully resolved source registry entry, as it appears at a report leaf."""

    source_id: str
    citation: str
    url: Optional[str]
    publication_year: Optional[int]
    retrieved: Optional[str]
    kind: Optional[str]
    notes: Optional[str] = None


@dataclass
class ProvenanceReportEntry:
    """One contributing timeline entry inside a :class:`ProvenanceReport`."""

    year: int
    category: str
    subject: str
    amount: UncertainValue
    parameters: List[ParameterProvenance] = field(default_factory=list)


@dataclass
class ProvenanceReport:
    """Tree answering "where does this value come from" (§3.10)."""

    value_path: str
    value: Optional[UncertainValue]
    entries: List[ProvenanceReportEntry] = field(default_factory=list)
    discounting_parameters: List[ParameterProvenance] = field(default_factory=list)
    sources: List[ResolvedSource] = field(default_factory=list)

    def render_text(self) -> str:
        """Human-readable rendering."""
        lines = [f"{self.value_path} = {self.value.to_json() if self.value else 'n/a'}"]
        for entry in self.entries:
            lines.append(
                f"  year {entry.year:>3}  {entry.category:<22} {entry.subject:<30} "
                f"{json.dumps(entry.amount.to_json())}"
            )
            for parameter in entry.parameters:
                source_list = ", ".join(parameter.source_ids) or parameter.origin.value
                lines.append(f"      <- {parameter.parameter} = {parameter.to_json()['value']} [{source_list}]")
        if self.discounting_parameters:
            lines.append("  discounting/aggregation parameters:")
            for parameter in self.discounting_parameters:
                lines.append(f"      {parameter.parameter} = {parameter.to_json()['value']}")
        if self.sources:
            lines.append("  sources:")
            for source in self.sources:
                retrieved = f", retrieved {source.retrieved}" if source.retrieved else ""
                lines.append(f"      [{source.source_id}] {source.citation} ({source.url or 'no url'}{retrieved})")
        return "\n".join(lines)

    def to_json(self) -> dict:
        """JSON rendering."""
        return {
            "value_path": self.value_path,
            "value": self.value.to_json() if self.value else None,
            "entries": [
                {
                    "year": entry.year,
                    "category": entry.category,
                    "subject": entry.subject,
                    "amount": entry.amount.to_json(),
                    "parameters": [parameter.to_json() for parameter in entry.parameters],
                }
                for entry in self.entries
            ],
            "discounting_parameters": [parameter.to_json() for parameter in self.discounting_parameters],
            "sources": [
                {
                    "id": source.source_id,
                    "citation": source.citation,
                    "url": source.url,
                    "publication_year": source.publication_year,
                    "retrieved": source.retrieved,
                    "kind": source.kind,
                    "notes": source.notes,
                }
                for source in self.sources
            ],
        }
