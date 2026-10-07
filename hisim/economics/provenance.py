"""Provenance ledger: from any result value back to its data sources (cost_spec.md §3.10).

During parameter resolution each resolved input is recorded once as an immutable `ParameterProvenance` and gets a small
integer id; every `CashFlowEntry` carries the ids of the records behind its amount. Since every published figure is a
filter, pivot or discounting of entries, explaining a value is a union over the contributing entries' ids. Records are
interned (identical records share one id), which keeps `cost_provenance.json` compact and lets archived CSVs be
explained offline. This module holds the records, the ledger and the `ProvenanceReport` shape; the loaders decide what
to record.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from hisim.economics.uncertainty import UncertainValue


class ParameterOrigin(str, enum.Enum):
    """Everything that can feed a number into the evaluation.

    A data-file entry, a per-field config override, a RenoVisor request field, a scenario overlay, an engine default, a
    simulation output, in-memory data and the legacy shim. The origin lets an explained value distinguish a published
    database price from an installer quote or a counterfactual sweep cell, and `requires_sources` derives from it
    whether a record must cite the source registry.
    """

    DATABASE_ENTRY = "DATABASE_ENTRY"
    CONFIG_OVERRIDE = "CONFIG_OVERRIDE"
    REQUEST = "REQUEST"
    SCENARIO_OVERLAY = "SCENARIO_OVERLAY"
    ENGINE_DEFAULT = "ENGINE_DEFAULT"
    SIMULATION_OUTPUT = "SIMULATION_OUTPUT"
    #: Data defined in Python instead of a catalog file: tests, worked examples and synthetic
    #: fixtures. No source registry stands behind it, so the record cites none and `detail` names
    #: the definition. Never used for shipped catalog data.
    IN_MEMORY_DEFINITION = "IN_MEMORY_DEFINITION"
    #: A value from the pre-catalog implementation kept by a migration shim (§10.1): only
    #: `DeviceEntry.legacy_flat_subsidy_share`, whose file's sources document the device price
    #: rather than the subsidy. Nothing records it any more; the origin stays so archived ledgers
    #: still load.
    LEGACY_MIGRATION_SHIM = "LEGACY_MIGRATION_SHIM"

    # Origins that legitimately carry no source ids:
    @property
    def requires_sources(self) -> bool:
        """Return whether a record of this origin must cite sources; all do except four exempt origins.

        Simulation outputs, engine defaults, in-memory data and legacy-shim values are exempt; each explains itself in
        `detail` instead. `ProvenanceLedger.record` enforces this (§3.10).
        """
        return self not in (
            ParameterOrigin.SIMULATION_OUTPUT,
            ParameterOrigin.ENGINE_DEFAULT,
            ParameterOrigin.IN_MEMORY_DEFINITION,
            ParameterOrigin.LEGACY_MIGRATION_SHIM,
        )


@dataclass(frozen=True)
class ParameterProvenance:
    """One interned, immutable record of a resolved input: parameter, value, origin and sources.

    `parameter` is a dotted path mirroring the data-file structure. `value` is an `UncertainValue` for money, a float
    for rates and lifetimes, or a string for categorical choices. `data_file` locates a DATABASE_ENTRY (file, entry
    key, valid-from year); `detail` carries the free-form explanation other origins need (an `override_source`, a
    request field name, a scenario id). Frozen and hashable, because interning keys a dict on the record.
    """

    parameter: str  # dotted path, e.g. "devices_DE.HEAT_PUMP@2024.specific_investment"
    value: Union[UncertainValue, float, str]
    origin: ParameterOrigin
    data_file: Optional[str] = None  # file / entry key / valid_from_year for DATABASE_ENTRY
    source_ids: Tuple[str, ...] = ()
    detail: Optional[str] = None  # override_source text, request field name, scenario id, ...

    def to_json(self) -> dict:
        """Serialize all six fields for `cost_provenance.json`; bands go through `UncertainValue.to_json`."""
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
    """Interns `ParameterProvenance` records and assigns stable integer ids.

    Built once per variant; `CashFlowEntry.provenance_ids` point into it. An id is the record's index, so ids survive
    serialization. `record` refuses a record that needs sources and has none, so an unsourced datapoint cannot enter a
    calculation (§3.10).
    """

    def __init__(self) -> None:
        """Create an empty ledger."""
        self._records: List[ParameterProvenance] = []
        self._index: Dict[ParameterProvenance, int] = {}

    def record(self, record: ParameterProvenance) -> int:
        """Intern a record and return its id; identical records share one id.

        Deduplication is by value equality, so a device price read for the investment and again for a replacement gets
        the same id.

        Args:
            record: The resolved input to intern.

        Returns:
            The record's stable id, for `CashFlowEntry.provenance_ids`.

        Raises:
            ValueError: If the record's origin requires sources and it carries none (§3.10).
        """
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
        """Return the record with the given id."""
        return self._records[record_id]

    def __len__(self) -> int:
        """Return the number of interned records, i.e. of distinct datapoints used."""
        return len(self._records)

    @property
    def records(self) -> List[ParameterProvenance]:
        """All records in id order; a copy, so callers cannot append to the ledger."""
        return list(self._records)

    def to_json(self) -> dict:
        """Serialize the ledger for `cost_provenance.json`, records in id order so the id is the array index."""
        return {"records": [record.to_json() for record in self._records]}

    @classmethod
    def from_json(cls, data: dict) -> "ProvenanceLedger":
        """Rebuild a stored ledger, e.g. for an offline `explain` on archived results.

        Records are read in file order so stored ids keep pointing at the same records. Values stored as `{"min",
        "best_estimate", "max"}` objects become `UncertainValue`; bare numbers come back as floats, even if written
        from a degenerate band. Records are appended directly, without `record`'s checks, so ids cannot be renumbered.
        The interning index is rebuilt (first occurrence wins), so a later `record` of an existing datapoint still
        reuses its id.
        """
        ledger = cls()
        for raw in data.get("records", []):
            value = raw.get("value")
            if isinstance(value, dict) and {"min", "best_estimate", "max"} <= set(value.keys()):
                value = UncertainValue.from_json(value)
            record = ParameterProvenance(
                parameter=raw["parameter"],
                value=value,
                origin=ParameterOrigin(raw["origin"]),
                data_file=raw.get("data_file"),
                source_ids=tuple(raw.get("source_ids", [])),
                detail=raw.get("detail"),
            )
            ledger._index.setdefault(record, len(ledger._records))  # noqa: SLF001 — controlled rehydration
            ledger._records.append(record)  # noqa: SLF001 — controlled rehydration
        return ledger


@dataclass
class ResolvedSource:
    """A fully resolved source registry entry, as shown at a report leaf.

    The presentation-side twin of `sources.SourceEntry`, so report and audit code need not import the data layer. All
    fields but id and citation are optional, because some sources are synthesized (an `inline:` scheme definition is
    rendered with `kind="INLINE"` and no url).
    """

    source_id: str
    citation: str
    url: Optional[str]
    publication_year: Optional[int]
    retrieved: Optional[str]
    kind: Optional[str]
    notes: Optional[str] = None


@dataclass
class ProvenanceReportEntry:
    """One contributing timeline entry in a `ProvenanceReport`: its year, category, subject and amount plus its records.

    A copy rather than a reference, so a report can be serialized without the timeline.
    """

    year: int
    category: str
    subject: str
    amount: UncertainValue
    parameters: List[ParameterProvenance] = field(default_factory=list)


@dataclass
class ProvenanceReport:
    """A tree answering "where does this value come from" (§3.10).

    Levels: the addressed value (`perspective/field`, the names the exports use), the timeline entries that make it up,
    each entry's parameter records, and the full citations at the leaves. `discounting_parameters` holds the rates and
    horizon that turn the nominal entries into the present value. Built by `LifecycleCostResult.explain` and `python -m
    hisim.economics explain`; `render_text` and `to_json` render the same content.
    """

    value_path: str
    value: Optional[UncertainValue]
    entries: List[ProvenanceReportEntry] = field(default_factory=list)
    discounting_parameters: List[ParameterProvenance] = field(default_factory=list)
    sources: List[ResolvedSource] = field(default_factory=list)

    def render_text(self) -> str:
        """Render the report as indented text for the `explain` CLI.

        Value, then one line per contributing entry with its parameters and their source ids (or the origin name for
        exempt origins), then the discounting parameters and the citations.
        """
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
        """Render the report as JSON with all four levels expanded.

        Source objects use the key `"id"` here, while `input_audit` writes `"source_id"` for the same field.
        """
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
