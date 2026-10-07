"""The source registry `sources.json` and its validation (cost_spec.md §3.5, §9.6).

Every datapoint in a data directory (`cost_database/`, `subsidy_catalog/`) must cite at least one registry entry, and
an unknown id fails at load time, so every value can be traced to a citation (§3.10 "no unsourced numbers"). An
honestly labelled guess (`kind: EXPERT_ESTIMATE`) is admissible. The registry also records which ids were resolved, for
the orphan and staleness checks and the "sources used" tables.
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
    """One publication, standard, statute or estimate that a datapoint may cite (§3.5).

    `kind` classifies the evidence (see `SourceRegistry.SOURCE_KINDS`), and `retrieved` is the date the staleness check
    compares against. `url` may be absent only when `notes` explains why (a printed standard, internal project data).
    """

    source_id: str
    citation: str
    url: Optional[str]
    publication_year: int
    retrieved: str
    kind: str
    notes: Optional[str] = None

    def to_resolved(self) -> ResolvedSource:
        """Return this entry as the `ResolvedSource` shape that provenance reports and the audit use.

        `ResolvedSource` lives in `provenance.py` so those consumers need not import the data layer.
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
    """One loaded `sources.json` file: the id-to-entry index, its validation, and the set of ids resolved so far.

    After a load it names the sources a result rests on (`referenced_ids`), the entries nothing cites (`orphaned_ids`)
    and those whose retrieval date is too old (`stale_ids`). It is mutable and per load, so it must not be shared
    between independently loaded databases.
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
        """Create a registry; use :meth:`load` instead of calling this directly.

        `file_name` is used in error messages. The reference set starts empty.
        """
        self.entries = entries
        self.file_name = file_name
        self._referenced: set = set()

    @classmethod
    def load(cls, path: str) -> "SourceRegistry":
        """Load and validate a `sources.json` file.

        Id, citation, publication year, retrieval date and a known `kind` are mandatory, and an entry without a url
        must explain the absence in `notes`.

        Args:
            path: Path to a `sources.json` file.

        Returns:
            A fresh registry with an empty reference set.

        Raises:
            CostDataError: If an entry lacks a mandatory field, declares an unknown kind, or has neither a url nor
                notes.
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
        """Resolve source ids to entries and record them as referenced (§9.6).

        Called once per data entry while the cost database is parsed.

        Args:
            source_ids: The ids a data entry declares; an empty tuple resolves to an empty list.
            context: Human-readable location of the citing entry, for the error message.

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
        """Return the ids this instance resolved, sorted; the audit's "sources used" table lists them (§3.10)."""
        return sorted(self._referenced)

    def orphaned_ids(self) -> List[str]:
        """Return the registry ids no data entry referenced, sorted (§9.6).

        An orphan usually means a datapoint was deleted or re-sourced and its citation left behind. Meaningful only
        after a full database load.
        """
        return sorted(set(self.entries.keys()) - self._referenced)

    def stale_ids(self, reference_date: Optional[date] = None, max_age_days: int = 365) -> List[str]:
        """Return the ids whose `retrieved` date is older than the staleness threshold, sorted (§9.6).

        Covers all entries, referenced or not; staleness is a warning, not a failure.

        Args:
            reference_date: "Today" for the comparison; defaults to the current date.
            max_age_days: Age in days above which an entry counts as stale.

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
