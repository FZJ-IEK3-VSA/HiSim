"""The resolved-input audit as data: which price each declared fact resolved to, from where (cost_spec.md §2.4, §9.5).

Both `cost_audit.csv` (written by `audit.py`) and the input section of the HTML report render these types, so they
agree on override precedence. A wrong unit price makes everything downstream wrong yet arithmetically correct, so this
table is where a mis-sized component, a kW/m² mix-up or an uncited override is spotted. The module holds no evaluator
and no database, so the report layer can import it; it is persisted as `cost_audit.json` so a report can be rebuilt
without the cost database.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Dict, List, Optional

from hisim.economics.provenance import ResolvedSource
from hisim.economics.uncertainty import UncertainValue


class OriginKind(str, Enum):
    """How a row's unit price was resolved.

    OVERRIDE: a per-field config override, which wins whether or not a database entry exists. DATABASE: a cost-database
    entry. UNRESOLVED: the engine could not price the component, which must be visible rather than silently zero.
    Values are the strings stored in `cost_audit.json`, and an unknown spelling fails on load.
    """

    ORIGIN_OVERRIDE = "OVERRIDE"
    ORIGIN_DATABASE = "DATABASE"
    ORIGIN_UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class ResolvedInputRow:
    """One declared cost subject with everything resolving it produced (§9.5).

    It carries the chain from declaration to money: what was declared (subject, asset class, size and unit), how it was
    priced (origin, override source, database entry, sources, unit price, lifetime) and what that produced (gross
    investment, subsidies, binding caps). `entry_key` and `source_ids` are filled whenever a database entry was found,
    also when an override won, so a reader sees what the override replaced.

    Units: `unit_price_in_euro` is the winning origin's price, euro per size unit for DATABASE or the absolute override
    amount for OVERRIDE; `investment_gross_in_euro` is the year-0 cost before support; `subsidies_nominal_in_euro` is
    positive, undiscounted support.
    """

    subject: str
    asset_class: str
    size: float
    size_unit: str
    origin_kind: OriginKind
    #: The `override_source` the facts declared, when `origin_kind` is OVERRIDE. Empty means the
    #: override cites nothing — a flagged condition, not a formatting question.
    override_source: Optional[str] = None
    #: Database entry the price came from, when one was found (also when an override won).
    entry_key: Optional[str] = None
    source_ids: List[str] = field(default_factory=list)
    unit_price_in_euro: Optional[UncertainValue] = None
    lifetime_in_years: Optional[float] = None
    investment_gross_in_euro: Optional[UncertainValue] = None
    subsidies_nominal_in_euro: Optional[UncertainValue] = None
    subsidy_scheme_ids: List[str] = field(default_factory=list)
    #: scheme id -> the slots whose cap bound, only for awards where at least one did.
    caps_binding_by_scheme: Dict[str, List[str]] = field(default_factory=dict)
    #: The Sowieso share the subject's anyway credit was computed at, or None when the subject
    #: earned no such credit. A credit is `share x like-for-like cost`, so this is the factor
    #: that turns the counterfactual's price into what is actually credited; without it a reader
    #: cannot see whether a full like-for-like measure was credited.
    anyway_share: Optional[float] = None
    #: The like-for-like cost that share was applied to, in euro. The share alone states a
    #: factor without its base, so the audited credit could not be reproduced from the row; with
    #: both, `share x basis` is the credit the timeline booked.
    anyway_basis_in_euro: Optional[float] = None
    #: Review flags, in the order they are raised. Rendered by the HTML report only.
    flags: List[str] = field(default_factory=list)

    def to_json(self) -> dict:
        """Return the row as JSON for `cost_audit.json`.

        Every field is written, including those only the HTML report shows, so either rendering can be rebuilt from the
        file. None stays None, so an unresolved row differs from a zero-priced one.
        """
        return {
            "subject": self.subject,
            "asset_class": self.asset_class,
            "size": self.size,
            "size_unit": self.size_unit,
            "origin_kind": self.origin_kind.value,
            "override_source": self.override_source,
            "entry_key": self.entry_key,
            "source_ids": self.source_ids,
            "unit_price_in_euro": UncertainValue.optional_to_json(self.unit_price_in_euro),
            "lifetime_in_years": self.lifetime_in_years,
            "investment_gross_in_euro": UncertainValue.optional_to_json(self.investment_gross_in_euro),
            "subsidies_nominal_in_euro": UncertainValue.optional_to_json(self.subsidies_nominal_in_euro),
            "subsidy_scheme_ids": self.subsidy_scheme_ids,
            "caps_binding_by_scheme": self.caps_binding_by_scheme,
            "anyway_share": self.anyway_share,
            "anyway_basis_in_euro": self.anyway_basis_in_euro,
            "flags": self.flags,
        }

    @staticmethod
    def from_json(raw: dict) -> "ResolvedInputRow":
        """Rebuild a row from the JSON :meth:`to_json` wrote.

        Collection fields default to empty; the identity and origin fields are mandatory.

        Raises:
            KeyError: If a mandatory key is missing.
            ValueError: If `origin_kind` names no `OriginKind` member.
        """
        return ResolvedInputRow(
            subject=raw["subject"],
            asset_class=raw["asset_class"],
            size=raw["size"],
            size_unit=raw["size_unit"],
            origin_kind=OriginKind(raw["origin_kind"]),
            override_source=raw.get("override_source"),
            entry_key=raw.get("entry_key"),
            source_ids=list(raw.get("source_ids", [])),
            unit_price_in_euro=UncertainValue.optional_from_json(raw.get("unit_price_in_euro")),
            lifetime_in_years=raw.get("lifetime_in_years"),
            investment_gross_in_euro=UncertainValue.optional_from_json(raw.get("investment_gross_in_euro")),
            subsidies_nominal_in_euro=UncertainValue.optional_from_json(raw.get("subsidies_nominal_in_euro")),
            subsidy_scheme_ids=list(raw.get("subsidy_scheme_ids", [])),
            caps_binding_by_scheme={
                scheme: list(slots) for scheme, slots in raw.get("caps_binding_by_scheme", {}).items()
            },
            anyway_share=raw.get("anyway_share"),
            anyway_basis_in_euro=raw.get("anyway_basis_in_euro"),
            flags=list(raw.get("flags", [])),
        )


def price_basis(row: ResolvedInputRow) -> str:
    """Return the label saying what a row's unit price is measured in.

    Example: a DATABASE row priced in EUR/kW gets a per-kW label, while an OVERRIDE row states an absolute amount for
    the whole subject. Without the label, a per-kW figure and a total look like the same quantity in one column. Both
    the CSV "Price basis" column and the HTML report use this.

    Args:
        row: The resolved row whose price is printed.

    Returns:
        The label, or the empty string for an UNRESOLVED row.
    """
    if row.origin_kind == OriginKind.ORIGIN_OVERRIDE:
        return "EUR absolute (override)"
    if row.origin_kind == OriginKind.ORIGIN_DATABASE:
        return f"EUR/{row.size_unit} (database)"
    return ""


@dataclass(frozen=True)
class InputAuditReport:
    """The resolved-input audit: one row per cost subject, the price basis year, and the sources the run cited.

    The price basis year is one decision for the whole evaluation; the database falls back to its earliest covered
    year, with a warning, when it has no data for the simulated year (see `evaluator.effective_price_basis_year`).
    Built by `audit.build_input_audit`, rendered to `cost_audit.csv` and the HTML report, and stored as
    `cost_audit.json`.
    """

    #: Name of the JSON file this report is written to.
    FILE_NAME: ClassVar[str] = "cost_audit.json"

    price_basis_year: int
    rows: List[ResolvedInputRow] = field(default_factory=list)
    #: The §3.10 registry entries this evaluation cited, sorted by id — from the cost database's
    #: registry and, through the result's ledger, the subsidy catalog's.
    sources: List[ResolvedSource] = field(default_factory=list)

    def to_json(self) -> dict:
        """Return the report as JSON for `cost_audit.json`, the reload twin of the diffable `cost_audit.csv`.

        Sources are written out in full, since a reader of the archived file has no registry to resolve ids against.
        """
        return {
            "price_basis_year": self.price_basis_year,
            "rows": [row.to_json() for row in self.rows],
            "sources": [
                {
                    "source_id": source.source_id,
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

    @staticmethod
    def from_json(raw: dict) -> "InputAuditReport":
        """Rebuild the audit from the JSON :meth:`to_json` wrote, without the cost database.

        This lets `python -m hisim.economics report <results_dir>` render an archived run. Only `price_basis_year` is
        mandatory; rows and sources default to empty.
        """
        return InputAuditReport(
            price_basis_year=raw["price_basis_year"],
            rows=[ResolvedInputRow.from_json(item) for item in raw.get("rows", [])],
            sources=[
                ResolvedSource(
                    source_id=item["source_id"],
                    citation=item["citation"],
                    url=item.get("url"),
                    publication_year=item.get("publication_year"),
                    retrieved=item.get("retrieved"),
                    kind=item.get("kind"),
                    notes=item.get("notes"),
                )
                for item in raw.get("sources", [])
            ],
        )


def write_input_audit(audit: InputAuditReport, result_directory: str) -> str:
    """Write `cost_audit.json` into the result directory, so a report can be rebuilt without the database.

    Called by `bridge.py` and the `evaluate` CLI command alongside `audit.write_cost_audit`, which writes the CSV from
    the same object.

    Args:
        audit: The report to store.
        result_directory: Directory the run's other cost outputs are written to.

    Returns:
        The path written.
    """
    path = os.path.join(result_directory, InputAuditReport.FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(audit.to_json(), file, indent=2)
    return path


def read_input_audit(result_directory: str) -> Optional[InputAuditReport]:
    """Read `cost_audit.json` from a result directory.

    Used by the `report` CLI command. A missing file is not an error: the caller omits the input-audit section.

    Args:
        result_directory: Directory to look in.

    Returns:
        The stored audit, or None if the file is absent.
    """
    path = os.path.join(result_directory, InputAuditReport.FILE_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as file:
        return InputAuditReport.from_json(json.load(file))
