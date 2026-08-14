"""Provenance ledger: from any result value back to its sources (cost_spec.md §3.10).

Scientific use of a cost result demands that a reviewer asking "where does this equivalent annual
cost come from" gets an answer down to individual datapoints and their citations, without
reverse-engineering the engine. Two mechanisms provide that: mandatory source metadata on the data
side (`sources.py` and the `source_ids` of every data entry) and this ledger on the engine side.

**How it works.** During parameter resolution — the same pass that dry-resolves every declared fact
against the database, so the lookups happen anyway — each resolved input is recorded once as an
immutable `ParameterProvenance`, and the ledger returns a small integer id for it. Every
`CashFlowEntry` then carries the ids of the records that entered its amount. Because every published
figure is by construction a filter, pivot or discounting of timeline entries (§3.1 principle 3),
explaining a value is a *set union* over the contributing entries' ids — not a second mechanism that
could disagree with the first.

**Why interning.** The same device price feeds the year-0 investment, three replacements and the
residual value; the same electricity price feeds twenty annual bills. Storing a full record on each
entry would make the ledger far larger than the results themselves, and an entry-to-record
comparison would depend on object identity. Interning gives value equality, deduplication and a
compact `cost_provenance.json` in one step, and the integer ids survive serialization so a CSV
archived years ago is still explainable offline.

**What downstream needs from this module** to "explain any value": stable ids on entries, records
that carry the origin *kind* as well as the value (a scenario overlay and a database entry must be
distinguishable — that is the whole point of a counterfactual sweep), resolvable source ids on every
record whose origin admits them, and a report type that renders as both text and JSON. The last is
`ProvenanceReport`, assembled by `LifecycleCostResult.explain` and by the `explain` CLI command; the
leaf citations it shows come from `sources.SourceEntry.to_resolved`.

This module owns the records, the ledger and the report shapes. It does not decide *what* to record
— the database, subsidy and scenario loaders do — and it holds no source registry of its own.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from hisim.economics.uncertainty import UncertainValue


class ParameterOrigin(str, enum.Enum):
    """Everything that can feed a number into the evaluation.

    The closed list of ways a value can reach the engine: a versioned data-file entry, a per-field
    config override, a RenoVisor request field, a scenario overlay, an engine default, a simulation
    output, plus the two special cases documented below. Recording the origin alongside the value is
    what lets an explained result distinguish "this is the published German 2026 price" from "this
    was overridden by an installer quote" or "this cell of the sweep was counterfactual" — a
    distinction no amount of citation text would convey.

    Membership also carries an obligation: `requires_sources` derives from the origin whether a
    record must cite the registry, so the "no unsourced numbers" rule is enforced by origin rather
    than by remembering to pass ids at each call site.
    """

    DATABASE_ENTRY = "DATABASE_ENTRY"
    CONFIG_OVERRIDE = "CONFIG_OVERRIDE"
    REQUEST = "REQUEST"
    SCENARIO_OVERLAY = "SCENARIO_OVERLAY"
    ENGINE_DEFAULT = "ENGINE_DEFAULT"
    SIMULATION_OUTPUT = "SIMULATION_OUTPUT"
    #: Data defined in Python instead of loaded from a catalog file — tests, worked examples and
    #: synthetic fixtures (W2.4). There is no source registry behind such data, so the record
    #: carries none; `detail` names the in-memory definition (e.g. a scheme's legal basis). Never
    #: legitimate for shipped catalog data: catalog loaders resolve real registry ids instead.
    IN_MEMORY_DEFINITION = "IN_MEMORY_DEFINITION"
    #: A value carried over from the pre-catalog implementation and kept alive by a migration
    #: shim (§10.1) — today only `DeviceEntry.legacy_flat_subsidy_share`, subsidy data stranded
    #: in the device catalog. It ships in a data file but its file's sources document the *device
    #: price*, not the subsidy, so it carries none unless a `field_sources` entry supplies one:
    #: the record is explicit that the number is a leftover awaiting the country's catalog (W2.6).
    LEGACY_MIGRATION_SHIM = "LEGACY_MIGRATION_SHIM"

    # Origins that legitimately carry no source ids:
    @property
    def requires_sources(self) -> bool:
        """Every record except simulation outputs, engine defaults, in-memory and shim data needs sources.

        The predicate `ProvenanceLedger.record` enforces, and therefore the mechanical form of the
        §3.10 rule that an unsourced datapoint cannot enter a calculation. The four exemptions are
        each principled rather than convenient: a simulated kWh is provenanced by the simulation
        itself, an engine default is documented in the spec, in-memory test data has no registry
        behind it, and the legacy shim's file cites the device price rather than the subsidy it
        carries — all four say so in `detail` or in their own comments instead.
        """
        return self not in (
            ParameterOrigin.SIMULATION_OUTPUT,
            ParameterOrigin.ENGINE_DEFAULT,
            ParameterOrigin.IN_MEMORY_DEFINITION,
            ParameterOrigin.LEGACY_MIGRATION_SHIM,
        )


@dataclass(frozen=True)
class ParameterProvenance:
    """One interned, immutable record of a resolved input.

    Answers, for one number that entered the evaluation: which parameter it is (a dotted path
    mirroring the data-file structure, so a reviewer can go straight to the entry), what value it
    took, where it came from, and which registry sources back it. Frozen — and therefore hashable —
    because interning is implemented as a dictionary keyed on the record itself: two resolutions of
    the same parameter to the same value from the same origin *are* the same record and must share
    one id.

    Note `value` is a union: monetary parameters arrive as `UncertainValue` bands, rates and
    lifetimes as floats, and categorical resolutions (a chosen scheme, a price basis) as strings.
    `data_file` locates a `DATABASE_ENTRY` down to the file, entry key and valid-from year, while
    `detail` carries the free-form explanation the other origins need — an `override_source` text, a
    request field name, a scenario id.
    """

    parameter: str  # dotted path, e.g. "devices_DE.HEAT_PUMP@2024.specific_investment"
    value: Union[UncertainValue, float, str]
    origin: ParameterOrigin
    data_file: Optional[str] = None  # file / entry key / valid_from_year for DATABASE_ENTRY
    source_ids: Tuple[str, ...] = ()
    detail: Optional[str] = None  # override_source text, request field name, scenario id, ...

    def to_json(self) -> dict:
        """Serializes for cost_provenance.json.

        Emits all six fields with no omissions, since the file is the only thing an offline
        `explain` on an archived result directory has to work with. Bands are written through
        `UncertainValue.to_json` (degenerate ones collapse to a bare number), while floats and
        strings pass through unchanged; `from_json` on the ledger reverses this.
        """
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
    """Interns :class:`ParameterProvenance` records and assigns stable integer ids.

    The append-only store built once per variant during the structure pass, and the thing every
    `CashFlowEntry.provenance_ids` points into. An id is simply the record's index, so ids are
    stable within a ledger, survive serialization, and let entries reference their inputs at the
    cost of a few integers instead of embedding whole records — which is what keeps
    `cost_provenance.json` and the CSV exports proportionate to the results.

    It is also the enforcement point for §3.10: `record` refuses a source-requiring record with no
    source ids, so an unsourced datapoint cannot silently enter a calculation. Scenario runs add
    only the records for the fields they override, which is why a full sweep costs almost nothing
    in provenance terms.
    """

    def __init__(self) -> None:
        """Empty ledger, ready to be filled by the resolution pass."""
        self._records: List[ParameterProvenance] = []
        self._index: Dict[ParameterProvenance, int] = {}

    def record(self, record: ParameterProvenance) -> int:
        """Interns a record and returns its id; identical records share one id.

        The single entry point for everything that resolves a value — the cost database, the subsidy
        catalog, the scenario overlays. Deduplication is by value equality on the frozen record, so
        the device price read for the investment and again for the third replacement yields the same
        id, and the ledger stays proportional to the number of *distinct* datapoints rather than to
        the number of cash flows.

        Args:
            record: The resolved input to intern.

        Returns:
            The record's stable id, to be stored in `CashFlowEntry.provenance_ids`.

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
        """Record by id — the dereference `explain` performs on every contributing entry."""
        return self._records[record_id]

    def __len__(self) -> int:
        """Number of interned records, i.e. of distinct datapoints this evaluation used."""
        return len(self._records)

    @property
    def records(self) -> List[ParameterProvenance]:
        """All records in id order; a copy, so a consumer cannot append to the ledger."""
        return list(self._records)

    def to_json(self) -> dict:
        """Full ledger for cost_provenance.json.

        Records are written in id order, which makes the id an implicit array index on the way back
        in and keeps the file diffable between runs. This is the archival half of the §3.10
        reproducibility promise: with this file plus the exported CSVs, any number can still be
        explained years later with no re-run.
        """
        return {"records": [record.to_json() for record in self._records]}

    @classmethod
    def from_json(cls, data: dict) -> "ProvenanceLedger":
        """Rehydrates a stored ledger (for offline `explain` on archived results).

        Reads records back in file order so that stored ids keep pointing at the same records, and
        restores values written as `{"min", "avg", "max"}` objects to `UncertainValue`; anything
        stored as a bare number comes back as a plain float, which is enough for rendering a report
        but is not literally the type it was written from if it was a degenerate band. It appends
        directly to the internal list rather than going through `record`, because the interning and
        source checks already ran when the ledger was built and re-running them could renumber ids
        that exported files still reference.

        The interning index is rebuilt alongside the list, exactly as `record` would have built it:
        first occurrence wins, later duplicates keep pointing at the earlier id. Without it a
        rehydrated ledger would silently stop deduplicating — the next `record` call would append a
        second copy of a datapoint already in the file under a new id — so a ledger read back for
        an offline `explain` behaves like the one it was written from.
        """
        ledger = cls()
        for raw in data.get("records", []):
            value = raw.get("value")
            if isinstance(value, dict) and {"min", "avg", "max"} <= set(value.keys()):
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
    """A fully resolved source registry entry, as it appears at a report leaf.

    The presentation-side twin of `sources.SourceEntry`: the same citation data, but defined here so
    that report and audit code can consume citations without importing the data layer (the seam the
    import lint pins). Every field except the id and citation is optional, because a few sources are
    synthesized rather than registry-backed — an `inline:` scheme definition, for instance, is
    rendered with `kind="INLINE"` and no url or retrieval date.
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
    """One contributing timeline entry inside a :class:`ProvenanceReport`.

    The middle level of the explanation tree: a flattened `CashFlowEntry` (year, category, subject,
    amount) together with the `ParameterProvenance` records its `provenance_ids` dereference to. It
    is a copy of the facts a reader needs rather than a reference to the entry, so a report can be
    serialized and read back without the timeline being present.
    """

    year: int
    category: str
    subject: str
    amount: UncertainValue
    parameters: List[ParameterProvenance] = field(default_factory=list)


@dataclass
class ProvenanceReport:
    """Tree answering "where does this value come from" (§3.10).

    Four levels: the addressed value at the root (`perspective/field`, the same names the exports
    use — there is no separate query language), the timeline entries that make it up, each entry's
    resolved parameters, and at the leaves the full citations. Assembled by
    `LifecycleCostResult.explain` and by `python -m hisim.economics explain`, so a reviewer can
    challenge a headline number and follow it to a url and a retrieval date in one step.

    `discounting_parameters` is the slot for the rates and horizon that turn the nominal entries
    into the reported present value — parameters that belong to the value as a whole rather than to
    any single entry. Both renderings, `render_text` and `to_json`, are of the same object, because a
    CLI reader and an archived machine-readable trace must not be able to disagree.
    """

    value_path: str
    value: Optional[UncertainValue]
    entries: List[ProvenanceReportEntry] = field(default_factory=list)
    discounting_parameters: List[ParameterProvenance] = field(default_factory=list)
    sources: List[ResolvedSource] = field(default_factory=list)

    def render_text(self) -> str:
        """Human-readable rendering.

        The indented, column-aligned form the `explain` CLI prints: value, then one line per
        contributing entry, then its parameters with the source ids they cite (falling back to the
        origin name when an exempt origin cites none), then the discounting parameters and the full
        citations. Read top-down it is the audit trail a reviewer asked for; the identical content
        is available as JSON via `to_json`.
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
        """JSON rendering.

        The machine-readable twin of `render_text`, with the same four levels expanded in full so a
        frontend or a downstream script can present the lineage itself. Note the source objects use
        the key `"id"` here while `input_audit` writes `"source_id"` for the same field; both are
        read by their own consumers.
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
