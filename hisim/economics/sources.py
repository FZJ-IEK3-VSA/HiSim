"""The structured source registry: `sources.json` and its validation (cost_spec.md §3.5, §9.6).

Every datapoint in the cost database must name at least one entry of this registry, so the
registry is loaded before any data file and outlives them: it also answers which ids were
actually referenced, which are orphaned, and which have gone stale.

The rule this module enforces is §3.10's "no unsourced numbers": an honestly labelled guess
(`kind: EXPERT_ESTIMATE`) is admissible, a number with no citation at all is not. Enforcing it at
*load* time rather than at review time is what makes the guarantee real — a data file referring to
an unknown id fails immediately, and `explain` can therefore always terminate at a full citation
with url and retrieval date rather than at a dead reference.

Because a `SourceRegistry` instance records which ids it actually resolved, it doubles as the
evidence base for the §9.6 data-file CI checks (orphans, staleness) and for the "sources used"
tables of the audit and the HTML report. Note that it owns metadata only: it never reads a price and
never decides whether a datapoint is *good*, only whether it is attributed. Each data directory
(`cost_database/`, `subsidy_catalog/`) has its own `sources.json` and hence its own registry.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from hisim.economics.catalog_entries import CostDataError
from hisim.economics.provenance import ResolvedSource


@dataclass
class SourceEntry:
    """One entry of the structured source registry (§3.5).

    The structured replacement for the reference lists that used to live in `configuration.py`
    docstrings: one record per publication, standard, statute or estimate that any datapoint may
    cite. `kind` classifies the evidence (see `SourceRegistry.SOURCE_KINDS`) so a reviewer can see at
    a glance whether a result rests on a market survey or on an expert guess, and `retrieved` is
    what the staleness check compares against — a url alone is not a claim about when it was true.

    `url` may be absent, but only when `notes` explains the absence (a printed standard, internal
    project data); the loader rejects an entry with neither.
    """

    source_id: str
    citation: str
    url: Optional[str]
    publication_year: int
    retrieved: str
    kind: str
    notes: Optional[str] = None

    def to_resolved(self) -> ResolvedSource:
        """Converts to the provenance-report representation.

        `ResolvedSource` is the shape the leaves of a `ProvenanceReport` and the audit's sources
        table expect, and it lives in `provenance.py` so those consumers never have to import the
        data layer. The conversion is a straight field copy; the two types are kept separate only to
        keep that import direction one-way.
        """
        return ResolvedSource(
            source_id=self.source_id,
            citation=self.citation,
            url=self.url,
            publication_year=self.publication_year,
            retrieved=self.retrieved,
            kind=self.kind,
            notes=self.notes,
        )


class SourceRegistry:
    """The `sources.json` registry with mandatory-field validation.

    Owns one loaded registry file: the id-to-entry index, the validation rules that admit an entry
    in the first place, and — statefully — the set of ids that were actually resolved during this
    process's lifetime. That last part is what turns the registry from a lookup table into an audit
    instrument: after a run it can name the sources a result rests on (`referenced_ids`), the
    registry entries nothing cites (`orphaned_ids`) and the ones whose retrieval date has aged out
    (`stale_ids`).

    Created by the cost database loader before any data file is parsed, since `resolve` is what
    every data entry's `source_ids` is checked against. Being mutable and per-load, it must not be
    shared across independently loaded databases, or the reference set would mix them.
    """

    #: Admissible source kinds (§3.5).
    SOURCE_KINDS = (
        "MARKET_SURVEY",
        "STANDARD",
        "STATUTE",
        "MANUFACTURER",
        "LITERATURE",
        "PROJECT_DATA",
        "EXPERT_ESTIMATE",
    )

    def __init__(self, entries: Dict[str, SourceEntry], file_name: str) -> None:
        """Constructed by :meth:`load`.

        `file_name` is kept purely so error messages can name the registry an unknown id was not
        found in, which matters once several registries (cost database, subsidy catalog) are in
        play. The reference set starts empty and fills as `resolve` is called.
        """
        self.entries = entries
        self.file_name = file_name
        self._referenced: set = set()

    @classmethod
    def load(cls, path: str) -> "SourceRegistry":
        """Loads and validates a sources.json file.

        Validates each entry against the §3.5 schema as it is read — id, citation, publication year,
        retrieval date and a known `kind` are mandatory, and an entry without a url must say in
        `notes` why there is none. Failing here rather than during evaluation means a malformed
        registry is caught by `python -m hisim.economics validate` and by CI, before any result can
        cite it.

        Args:
            path: Path to a `sources.json` file.

        Returns:
            A fresh registry with an empty reference set.

        Raises:
            CostDataError: If an entry lacks an id or another mandatory field, declares an unknown
                kind, or has neither a url nor notes.
        """
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
        entries: Dict[str, SourceEntry] = {}
        for item in raw.get("sources", []):
            for mandatory in ("id", "citation", "publication_year", "retrieved", "kind"):
                if mandatory not in item or item[mandatory] in (None, ""):
                    if mandatory == "id" or item.get("id") is None:
                        raise CostDataError(f"Source entry without id in {path}: {item!r}")
                    raise CostDataError(f"Source {item['id']!r} in {path} misses mandatory field {mandatory!r}.")
            if item["kind"] not in cls.SOURCE_KINDS:
                raise CostDataError(f"Source {item['id']!r} has unknown kind {item['kind']!r}.")
            if item.get("url") in (None, "") and not item.get("notes"):
                raise CostDataError(f"Source {item['id']!r} has neither url nor notes explaining its absence.")
            entries[item["id"]] = SourceEntry(
                source_id=item["id"],
                citation=item["citation"],
                url=item.get("url"),
                publication_year=int(item["publication_year"]),
                retrieved=item["retrieved"],
                kind=item["kind"],
                notes=item.get("notes"),
            )
        return cls(entries, os.path.basename(path))

    def resolve(self, source_ids: Tuple[str, ...], context: str) -> List[SourceEntry]:
        """Resolves ids, failing on unknown ones (§9.6). Tracks referenced ids for orphan checks.

        Called once per data entry as the cost database is parsed, which is what enforces the
        "unsourced datapoints are not admissible" rule end to end: `catalog_entries._require_sources`
        guarantees an entry *names* sources, and this guarantees the names exist. The bookkeeping
        side effect is deliberate — resolution and reference tracking are the same pass, so the
        orphan and "sources used" reports cannot drift from what was actually read.

        Args:
            source_ids: The ids a data entry declares; an empty tuple resolves to an empty list.
            context: Human-readable location of the citing entry, used in the error message.

        Returns:
            The resolved entries, in the order given.

        Raises:
            CostDataError: If any id is not in this registry.
        """
        resolved = []
        for source_id in source_ids:
            if source_id not in self.entries:
                raise CostDataError(f"{context}: unknown source id {source_id!r} (not in {self.file_name}).")
            self._referenced.add(source_id)
            resolved.append(self.entries[source_id])
        return resolved

    def referenced_ids(self) -> List[str]:
        """Registry ids this instance actually resolved, sorted (§3.10).

        The audit's "sources used" table is built from this. Public since W4.6, because the
        HTML report used to reach into the private set to build the same table itself.
        """
        return sorted(self._referenced)

    def orphaned_ids(self) -> List[str]:
        """Registry entries never referenced by any data entry (flagged by CI, §9.6).

        The complement of `referenced_ids`, and a maintenance signal rather than an error: an orphan
        usually means a datapoint was deleted or re-sourced and its citation was left behind, which
        makes the registry look better documented than the data actually is. Meaningful only after
        a full database load, since it is defined against what *this instance* resolved.
        """
        return sorted(set(self.entries.keys()) - self._referenced)

    def stale_ids(self, reference_date: Optional[date] = None, max_age_days: int = 365) -> List[str]:
        """Sources whose `retrieved` date is older than the staleness threshold (§9.6).

        Energy prices, subsidy rates and device costs move fast enough that a citation retrieved two
        years ago is a warning sign even when the url still resolves; the default 12-month window is
        the spec's. Unlike the other checks this one covers *all* registry entries, referenced or
        not, and produces a warning rather than a failure — except for an unparseable date, which is
        a data defect.

        Args:
            reference_date: "Today" for the comparison; defaults to the actual current date, which
                is why tests pass an explicit date to stay deterministic.
            max_age_days: Age above which an entry counts as stale.

        Returns:
            The stale ids, sorted.

        Raises:
            CostDataError: If an entry's `retrieved` field is not an ISO `YYYY-MM-DD` date.
        """
        reference = reference_date or date.today()
        stale = []
        for source_id, entry in self.entries.items():
            try:
                retrieved = datetime.strptime(entry.retrieved, "%Y-%m-%d").date()
            except ValueError as err:
                raise CostDataError(f"Source {source_id!r} has invalid retrieved date {entry.retrieved!r}.") from err
            if (reference - retrieved).days > max_age_days:
                stale.append(source_id)
        return sorted(stale)
