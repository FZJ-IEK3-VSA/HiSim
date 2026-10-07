#!/usr/bin/env python3
"""Print the one-page overview of the open beads, sorted by the schema in AGENTS.md.

Run from the repository root, where the bead database lives::

    python scripts/beads_overview.py            # the page, on stdout
    python scripts/beads_overview.py --json     # the same content for a machine

The page answers, in order: what is burning (P0 and P1 in full), who the rest waits on (the
owner's decisions, people outside the repository, beads nobody has sorted yet), where the open
work sits (a kind x area table), how far each epic has got, and which beads break the schema
(not exactly one area, no parent, a kind that does not fit, untouched for a fortnight).

It only reads: ``br list``, ``br show`` (for each bead's parent and notes), ``br epic status`` and
``br count``. Nothing is written. The data is fetched in ``fetch`` and everything after that is
a pure function of it, so the test feeds a canned fixture instead of calling ``br``.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 -- runs the local br binary with fixed arguments
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

KINDS: Tuple[str, ...] = ("bug", "feature", "task", "chore", "docs", "question", "epic")
AREAS: Tuple[str, ...] = (
    "renovisor",
    "economics",
    "components",
    "energy-systems",
    "postprocessing",
    "ci",
    "data",
    "rust",
    "docs",
)
QUEUES: Tuple[str, ...] = ("owner-decision", "external", "needs-triage")
# Labels the schema replaced: `human` became `external`, `cleanup` became the type `chore`.
RETIRED_LABELS: Tuple[str, ...] = ("human", "cleanup")
# Who an `external` bead waits on: its own "Waits on: <who>" line (notes or description), else the
# first of these parties its text names.
WAITS_ON = re.compile(r"^\s*Waits on:\s*(?P<who>.+?)\s*$", re.IGNORECASE | re.MULTILINE)
EXTERNAL_PARTIES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("contract owner", ("contract owner", "contract-owner", "contract_owner")),
    ("backend", ("renovisorbackend", "backend")),
    ("frontend", ("renosfhfrontend", "frontend")),
)
STALE_DAYS = 14
SHOW_BATCH = 100


@dataclass
class Bead:
    """One open bead, reduced to the fields the schema sorts by."""

    id: str
    title: str
    kind: str
    priority: int
    labels: List[str] = field(default_factory=list)
    parent: Optional[str] = None
    updated_at: str = ""
    description: str = ""
    notes: str = ""

    @classmethod
    def from_json(cls, row: Dict[str, Any]) -> "Bead":
        """Build a bead from one row of ``br list --json`` / ``br show --json``."""
        return cls(
            id=row["id"],
            title=row.get("title", ""),
            kind=row.get("issue_type", ""),
            priority=int(row.get("priority", 2)),
            labels=list(row.get("labels") or []),
            parent=row.get("parent"),
            updated_at=row.get("updated_at", ""),
            description=row.get("description") or "",
            notes=row.get("notes") or "",
        )

    @property
    def areas(self) -> List[str]:
        """The area labels this bead carries (the schema wants exactly one)."""
        return [label for label in self.labels if label in AREAS]

    @property
    def area(self) -> str:
        """The single area, or a marker when there is none or more than one."""
        areas = self.areas
        if len(areas) == 1:
            return areas[0]
        return "(none)" if not areas else "+".join(areas)

    @property
    def table_area(self) -> str:
        """The area column of the kind x area table: one area, ``(none)`` or ``(several)``."""
        areas = self.areas
        if len(areas) == 1:
            return areas[0]
        return "(none)" if not areas else "(several)"

    def row(self) -> str:
        """One line: id, priority, kind, area, title."""
        return f"  {self.id:<16} P{self.priority} {self.kind:<8} {self.area:<15} {self.title}"


@dataclass
class Epic:
    """An epic and how many of its children are open and closed."""

    id: str
    title: str
    priority: int
    total_children: int
    closed_children: int

    @property
    def open_children(self) -> int:
        """Children not yet closed."""
        return self.total_children - self.closed_children


def _run_br(args: Sequence[str]) -> Any:
    """Run one read-only ``br`` command and parse its JSON output."""
    completed = subprocess.run(  # nosec B603 B607 -- fixed argument list, no shell
        ["br", *args], check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def fetch() -> Dict[str, Any]:
    """Read the open beads, their parents, the epics and the closed count from ``br``."""
    listing = _run_br(["list", "--status", "open", "--limit", "0", "--json"])
    rows: List[Dict[str, Any]] = listing["issues"] if isinstance(listing, dict) else listing
    ids = [row["id"] for row in rows]
    shown: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(ids), SHOW_BATCH):
        for full in _run_br(["show", *ids[start:start + SHOW_BATCH], "--json"]):
            shown[full["id"]] = full
    for row in rows:
        row["parent"] = shown.get(row["id"], {}).get("parent")
        row["notes"] = shown.get(row["id"], {}).get("notes")
    epics = _run_br(["epic", "status", "--json"])
    closed = _run_br(["count", "--status", "closed", "--json"])
    closed_total = closed.get("count", closed.get("total", 0)) if isinstance(closed, dict) else int(closed)
    return {"issues": rows, "epics": epics, "closed_total": closed_total}


def _external_party(bead: Bead) -> str:
    """Name whom an ``external`` bead waits on: its "Waits on:" line, else the first party its text names."""
    for source in (bead.notes, bead.description):
        stated = WAITS_ON.search(source)
        if stated:
            return stated.group("who")
    text = f"{bead.title}\n{bead.description}\n{bead.notes}".lower()
    found: List[Tuple[int, str]] = []
    for party, words in EXTERNAL_PARTIES:
        positions = [text.find(word) for word in words if word in text]
        if positions:
            found.append((min(positions), party))
    return min(found)[1] if found else "unnamed"


def _parse_time(stamp: str) -> Optional[datetime]:
    """Parse br's timestamps (nanosecond fractions, trailing Z)."""
    if not stamp:
        return None
    whole, _, rest = stamp.rstrip("Z").partition(".")
    try:
        parsed = datetime.strptime(whole, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    micro = int((rest + "000000")[:6]) if rest.isdigit() else 0
    return parsed.replace(microsecond=micro, tzinfo=timezone.utc)


def kind_problem(bead: Bead) -> Optional[str]:
    """Say why a bead's kind does not fit the schema, or None when it does."""
    if bead.kind not in KINDS:
        return f"type '{bead.kind}' is not a schema kind"
    if bead.kind == "question" and "owner-decision" not in bead.labels and "external" not in bead.labels:
        return "a question nobody is asked: answered (retype or close) or missing its queue"
    return None


def build(data: Dict[str, Any], now: datetime, stale_days: int = STALE_DAYS) -> Dict[str, Any]:
    """Sort the fetched data into the overview's sections."""
    beads = sorted((Bead.from_json(row) for row in data["issues"]), key=lambda b: (b.priority, b.id))
    epics = [
        Epic(
            id=entry["epic"]["id"],
            title=entry["epic"].get("title", ""),
            priority=int(entry["epic"].get("priority", 2)),
            total_children=int(entry.get("total_children", 0)),
            closed_children=int(entry.get("closed_children", 0)),
        )
        for entry in data["epics"]
        if entry["epic"].get("status", "open") != "closed"
    ]
    external: Dict[str, List[Bead]] = defaultdict(list)
    for bead in beads:
        if "external" in bead.labels:
            external[_external_party(bead)].append(bead)
    table: Counter = Counter((bead.kind, bead.table_area) for bead in beads)
    cutoff = now - timedelta(days=stale_days)
    stale = [b for b in beads if (_parse_time(b.updated_at) or now) < cutoff]
    return {
        "open_total": len(beads),
        "closed_total": data.get("closed_total", 0),
        "urgent": [b for b in beads if b.priority <= 1],
        "owner_decision": [b for b in beads if "owner-decision" in b.labels],
        "external": dict(sorted(external.items())),
        "needs_triage": [b for b in beads if "needs-triage" in b.labels],
        "table": table,
        "epics": sorted(epics, key=lambda e: (-e.open_children, e.id)),
        "gaps": {
            "not exactly one area label": [b for b in beads if len(b.areas) != 1],
            "no parent epic": [b for b in beads if b.kind != "epic" and not b.parent],
            "kind does not fit": [b for b in beads if kind_problem(b)],
            "retired label (human, cleanup)": [b for b in beads if set(b.labels) & set(RETIRED_LABELS)],
            f"not updated for {stale_days} days": stale,
        },
    }


def _section(title: str, beads: Sequence[Bead], lines: List[str]) -> None:
    lines.append(f"\n{title} ({len(beads)})")
    lines.extend(bead.row() for bead in beads)
    if not beads:
        lines.append("  none")


def render(overview: Dict[str, Any], gap_limit: int = 10) -> str:
    """Render the overview as one page of plain text."""
    lines = [f"Beads: {overview['open_total']} open, {overview['closed_total']} closed"]
    _section("P0 and P1", overview["urgent"], lines)
    _section("Waiting on the owner (owner-decision)", overview["owner_decision"], lines)
    waiting = sum(len(group) for group in overview["external"].values())
    lines.append(f"\nWaiting outside the repository (external) ({waiting})")
    for party, group in overview["external"].items():
        lines.append(f"  -- {party} ({len(group)})")
        lines.extend(bead.row() for bead in group)
    if not waiting:
        lines.append("  none")
    _section("Not sorted yet (needs-triage)", overview["needs_triage"], lines)

    table: Counter = overview["table"]
    columns = [area for area in (*AREAS, "(several)", "(none)") if any(key[1] == area for key in table)]
    kinds = [kind for kind in KINDS if any(key[0] == kind for key in table)]
    kinds += sorted({kind for (kind, _) in table} - set(KINDS))
    widths = [max(len(column), 3) + 1 for column in columns]
    lines.append("\nOpen beads by kind x area")
    lines.append(f"  {'':<9}" + "".join(f"{c:>{w}}" for c, w in zip(columns, widths)) + f"{'total':>7}")
    for kind in kinds:
        counts = [table[(kind, area)] for area in columns]
        cells = "".join(f"{n or '.':>{w}}" for n, w in zip(counts, widths))
        lines.append(f"  {kind:<9}" + cells + f"{sum(counts):>7}")
    totals = [sum(table[(kind, area)] for kind in kinds) for area in columns]
    lines.append(f"  {'total':<9}" + "".join(f"{n:>{w}}" for n, w in zip(totals, widths)) + f"{sum(totals):>7}")

    lines.append("\nEpics (open / closed children)")
    for epic in overview["epics"]:
        progress = f"{epic.open_children:>3} / {epic.closed_children:<3}"
        lines.append(f"  {epic.id:<16} {progress} P{epic.priority} {epic.title}")

    lines.append("\nGaps")
    for name, beads in overview["gaps"].items():
        lines.append(f"  {name}: {len(beads)}")
        for bead in beads[:gap_limit]:
            problem = kind_problem(bead) if name == "kind does not fit" else None
            lines.append(bead.row() + (f"  [{problem}]" if problem else ""))
        if len(beads) > gap_limit:
            lines.append(f"    ... and {len(beads) - gap_limit} more")
    return "\n".join(lines)


def to_json(overview: Dict[str, Any]) -> Dict[str, Any]:
    """The overview with beads reduced to ids, for ``--json``."""

    def ids(beads: Sequence[Bead]) -> List[str]:
        return [bead.id for bead in beads]

    return {
        "open_total": overview["open_total"],
        "closed_total": overview["closed_total"],
        "urgent": ids(overview["urgent"]),
        "owner_decision": ids(overview["owner_decision"]),
        "external": {party: ids(group) for party, group in overview["external"].items()},
        "needs_triage": ids(overview["needs_triage"]),
        "kind_by_area": [
            {"kind": kind, "area": area, "count": count} for (kind, area), count in sorted(overview["table"].items())
        ],
        "epics": [
            {"id": e.id, "title": e.title, "open": e.open_children, "closed": e.closed_children}
            for e in overview["epics"]
        ],
        "gaps": {name: ids(beads) for name, beads in overview["gaps"].items()},
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Fetch, build and print the overview."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead of the page")
    parser.add_argument("--stale-days", type=int, default=STALE_DAYS, help="days without update that count as stale")
    parser.add_argument("--gap-limit", type=int, default=10, help="beads listed per gap (the count is always full)")
    args = parser.parse_args(argv)
    overview = build(fetch(), datetime.now(timezone.utc), args.stale_days)
    if args.json:
        print(json.dumps(to_json(overview), indent=2))
    else:
        print(render(overview, args.gap_limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
