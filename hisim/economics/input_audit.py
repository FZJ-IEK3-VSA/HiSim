"""The resolved-input audit, as data (cost-spec-v2 §2.4, W4.6).

"Which price did each declared fact actually resolve to, from where, and what looks wrong about
it" is one question with two renderings: `cost_audit.csv` (written by `audit.py`) and section 1
of the HTML report (rendered by `reporting.py`). Before W4.6 each of them answered it for
itself, and the two override-precedence implementations disagreed — the report dropped an
override's unit price whenever the asset class had no database entry, which the CSV did not.

The types live here, apart from the function that fills them (`audit.build_input_audit`),
because the renderers sit on opposite sides of the seam-4 lint: `audit.py` is verification and
may reach into the cost database, `reporting.py` is presentation and may not. A module that
holds only the answer — no evaluator, no database, no pandas — is importable from both.

Why the audit matters to a reviewer: it is the fastest way to catch the errors that actually happen.
Everything downstream of a wrong unit price is arithmetically correct and completely wrong, so a
table of "this component, this asset class, this size, priced at this unit price from this entry,
citing these sources, with these subsidies and these caps binding" is where a mis-sized component, a
kW/m² mix-up or an uncited override is spotted — long before anyone questions an NPV. It is also
persisted (`cost_audit.json`, alongside the human-facing `cost_audit.csv`), so a report can be
rebuilt from an archived result directory with no cost database present at all (W4.5).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

from hisim.economics.provenance import ResolvedSource
from hisim.economics.uncertainty import UncertainValue


class OriginKind:
    """How a row's unit price was resolved. The vocabulary is shared by both renderers; each
    spells it its own way, which is formatting and therefore theirs to decide.

    Three outcomes are possible and the distinction is the audit's central question: the price came
    from a per-field config override (OVERRIDE, which wins over the database whether or not a
    database entry exists), from a cost-database entry (DATABASE), or from nowhere at all
    (UNRESOLVED — a component the engine could not price, which must be visible rather than silently
    contributing zero). Deciding this once here is what fixed the pre-W4.6 disagreement in which the
    HTML report dropped an override's unit price whenever the asset class had no database entry.
    """

    ORIGIN_OVERRIDE = "OVERRIDE"
    ORIGIN_DATABASE = "DATABASE"
    ORIGIN_UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class ResolvedInputRow:
    """One declared fact with everything resolving it produced (§9.5).

    Pure data: no string only one of the two renderers could want, no formatting. `origin_kind`
    settles the precedence once — a config override wins over the database entry whether or not
    that entry exists — so neither renderer decides it again.

    One row per declared cost subject, carrying the whole chain from declaration to money: what was
    declared (subject, asset class, size and its unit), how it was priced (origin kind, override
    source, database entry key, source ids, unit price and lifetime), and what that produced (gross
    investment, nominal subsidies, the scheme ids and which caps bound in which slots). `entry_key`
    and `source_ids` are filled whenever a database entry was found — including when an override
    won — so a reader can see what the declared price is being compared against.

    Units, since several of these fields are easy to misread: `unit_price_in_euro` is the price as
    the winning origin states it — the database entry's `specific_investment`, i.e. euro per size
    unit, or the declared `investment_cost_override_in_euro` when an override won;
    `investment_gross_in_euro` is the absolute year-0 figure before support; and
    `subsidies_nominal_in_euro` is nominal, undiscounted, positive support — not the negative
    timeline amount and not its NPV.
    """

    subject: str
    asset_class: str
    size: float
    size_unit: str
    origin_kind: str
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
    #: The Sowieso share the subject's anyway credit was computed at (owner decision Q22), or
    #: None when the subject earned no such credit. A credit is `share x like-for-like cost`, so
    #: this is the factor that turns the counterfactual's price into what is actually credited;
    #: printing the credit without it is what let a facade that was never insulated be credited
    #: with a full insulation measure.
    anyway_share: Optional[float] = None
    #: The like-for-like cost that share was applied to, in euro (owner decision Q26 F7). The
    #: share alone states a factor without its base, so the audited credit could not be
    #: reproduced from the row; with both, `share x basis` is the credit the timeline booked.
    anyway_basis_in_euro: Optional[float] = None
    #: Review flags, in the order they are raised. Rendered by the HTML report only.
    flags: List[str] = field(default_factory=list)

    def to_json(self) -> dict:
        """Serialization for cost_audit.json.

        Writes every field, including the ones only the HTML report renders (`flags`,
        `caps_binding_by_scheme`), because the stored audit has to be sufficient to rebuild either
        rendering later. Bands go through `UncertainValue.to_json` and `None` stays `None`, so an
        unresolved row is distinguishable from a zero-priced one.
        """
        return {
            "subject": self.subject,
            "asset_class": self.asset_class,
            "size": self.size,
            "size_unit": self.size_unit,
            "origin_kind": self.origin_kind,
            "override_source": self.override_source,
            "entry_key": self.entry_key,
            "source_ids": self.source_ids,
            "unit_price_in_euro": _band_json(self.unit_price_in_euro),
            "lifetime_in_years": self.lifetime_in_years,
            "investment_gross_in_euro": _band_json(self.investment_gross_in_euro),
            "subsidies_nominal_in_euro": _band_json(self.subsidies_nominal_in_euro),
            "subsidy_scheme_ids": self.subsidy_scheme_ids,
            "caps_binding_by_scheme": self.caps_binding_by_scheme,
            "anyway_share": self.anyway_share,
            "anyway_basis_in_euro": self.anyway_basis_in_euro,
            "flags": self.flags,
        }

    @staticmethod
    def from_json(raw: dict) -> "ResolvedInputRow":
        """Inverse of `to_json`.

        Rebuilds a row from a stored `cost_audit.json`, defaulting the collection fields to empty so
        an audit written by an older version still loads. The mandatory keys are the identity and
        origin fields; everything a run may legitimately not have produced is optional.
        """
        return ResolvedInputRow(
            subject=raw["subject"],
            asset_class=raw["asset_class"],
            size=raw["size"],
            size_unit=raw["size_unit"],
            origin_kind=raw["origin_kind"],
            override_source=raw.get("override_source"),
            entry_key=raw.get("entry_key"),
            source_ids=list(raw.get("source_ids", [])),
            unit_price_in_euro=_band_from(raw.get("unit_price_in_euro")),
            lifetime_in_years=raw.get("lifetime_in_years"),
            investment_gross_in_euro=_band_from(raw.get("investment_gross_in_euro")),
            subsidies_nominal_in_euro=_band_from(raw.get("subsidies_nominal_in_euro")),
            subsidy_scheme_ids=list(raw.get("subsidy_scheme_ids", [])),
            caps_binding_by_scheme={
                scheme: list(slots) for scheme, slots in raw.get("caps_binding_by_scheme", {}).items()
            },
            anyway_share=raw.get("anyway_share"),
            anyway_basis_in_euro=raw.get("anyway_basis_in_euro"),
            flags=list(raw.get("flags", [])),
        )


@dataclass(frozen=True)
class InputAuditReport:
    """The resolved-input audit: its rows, the price basis they resolved at, its sources.

    The complete answer to "what did this evaluation actually read": one row per cost subject, the
    price basis year everything resolved at, and the §3.10 registry entries the run cited. The basis
    year is on the report rather than on each row because it is a single decision for the whole
    evaluation — and a consequential one, since the database falls back to the earliest covered year
    with a warning when it has no data for the simulated year (see `evaluator.
    effective_price_basis_year`).

    Built by `audit.build_input_audit` from the inputs, the database and the first result; rendered
    to `cost_audit.csv` by `audit.py` and to section 1 of the HTML report by `reporting.py`; and
    persisted as `cost_audit.json` so either rendering can be reproduced offline.
    """

    #: Name of the JSON file this report is written to.
    FILE_NAME: ClassVar[str] = "cost_audit.json"

    price_basis_year: int
    rows: List[ResolvedInputRow] = field(default_factory=list)
    #: The §3.10 registry entries this evaluation cited, sorted by id — from the cost database's
    #: registry and, through the result's ledger, the subsidy catalog's.
    sources: List[ResolvedSource] = field(default_factory=list)

    def to_json(self) -> dict:
        """Serialization for cost_audit.json — the machine-readable twin of cost_audit.csv.

        The CSV is the review artifact — deliberately diffable, so a price-data PR shows up as a
        clean textual delta on the golden scenarios (§9.5) — while this JSON is the reload format,
        carrying the fields the CSV flattens away. Sources are expanded inline rather than left as
        ids, since a reader of the archived file has no registry to resolve them against.
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
        """Inverse of `to_json` — reloads the audit without touching the cost database (W4.5).

        That independence is the point: `python -m hisim.economics report <results_dir>` can render
        the input-audit section of an archived run even if the price files have since moved on, or
        are not installed at all. Only `price_basis_year` is mandatory; rows and sources default to
        empty.
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


def _band_json(value: Optional[UncertainValue]) -> Any:
    """Serializes an optional band, keeping `None` distinct from a zero band."""
    return value.to_json() if value is not None else None


def _band_from(value: Any) -> Optional[UncertainValue]:
    """Parses an optional band; the inverse of `_band_json`, `None` staying `None`."""
    return UncertainValue.from_json(value) if value is not None else None


def write_input_audit(audit: InputAuditReport, result_directory: str) -> str:
    """Writes cost_audit.json next to the CSV, so a report can be rebuilt without the database.

    Called by `bridge.py` at the end of a `COMPUTE_LIFECYCLE_COSTS` run and by the `evaluate` CLI
    command, always alongside `audit.write_cost_audit`, which writes the human-facing CSV from the
    same object — the two files can therefore never disagree.

    Args:
        audit: The report to store.
        result_directory: Directory the run's other cost outputs are written to.

    Returns:
        The path written, for logging.
    """
    path = os.path.join(result_directory, InputAuditReport.FILE_NAME)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(audit.to_json(), file, indent=2)
    return path


def read_input_audit(result_directory: str) -> Optional[InputAuditReport]:
    """Reads cost_audit.json, or None when the directory has none.

    The reload path used by the `report` CLI command on a stored result directory. Returning `None`
    rather than raising for a missing file is deliberate: result directories produced before the
    audit was persisted, or by a run that only computed KPIs, are still reportable — the caller
    simply omits the input-audit section.

    Args:
        result_directory: Directory to look in.

    Returns:
        The stored audit, or `None` if the file is absent.
    """
    path = os.path.join(result_directory, InputAuditReport.FILE_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as file:
        return InputAuditReport.from_json(json.load(file))
