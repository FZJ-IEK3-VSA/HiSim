#!/usr/bin/env python3
"""Rename bead ids across a checkout, by the mapping in ``.beads/renamed_ids.yaml``.

The mapping lists old epic ids and their new readable ids, e.g. ``hisim-ab1`` ->
``hisim-some-theme-ab1`` (invented ids: a real old id here would be renamed with the rest). Children
follow their parent at every depth: ``hisim-ab1.18`` becomes ``hisim-some-theme-ab1.18``. Run from
the repository root::

    python scripts/rename_bead_ids.py           # rewrite the checkout, print what changed
    python scripts/rename_bead_ids.py --check   # exit 1 while any old id is still mentioned

What it rewrites:

- ``.beads/issues.jsonl``: every string of every bead (its id, the ids of its dependencies and
  comments, every mention in its title, description, notes and other text). Each renamed epic gets
  the note "Renamed from <old id> on <date>." once. The file is then re-sorted by id in plain string
  order, the order ``br sync --flush-only`` writes, because renamed ids move.
- every other text file git tracks, except ``.beads/``, the golden reference data
  (``golden_references/``, ``tests/goldens/``) and the vendored RenoVisor contract
  (``hisim/renovisor/contract/``). Binary files are skipped.

An id is matched only as a whole id: ``hisim-ab1`` does not match inside ``hisim-ab1x``, and a
new id never contains an old one, so a second run changes nothing. The script is meant to be
re-run on every branch that still carries old ids, e.g. before rebasing it onto the rename.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 -- runs the local git binary with fixed arguments
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml


class Layout:
    """Where the script finds things in a checkout, as paths relative to the repository root.

    The mapping file is the one source of truth for the renamed ids; the issues file is the bead
    database as git tracks it. Paths under ``SKIPPED_PREFIXES`` are never rewritten or checked:
    ``.beads/`` is handled separately, the golden reference data is recorded output, not prose, and
    the RenoVisor contract files are copies of another repository, pinned by their sha256 in
    ``hisim/renovisor/contract/PINNED.yaml``.
    """

    MAPPING = ".beads/renamed_ids.yaml"
    ISSUES = ".beads/issues.jsonl"
    SKIPPED_PREFIXES: Tuple[str, ...] = (".beads/", "golden_references/", "tests/goldens/", "hisim/renovisor/contract/")


@dataclass(frozen=True)
class IdRenaming:
    """The old -> new id mapping and the one regular expression that finds old ids in text.

    A match is an old id, optionally followed by child suffixes ``.N`` at any depth, standing as a
    whole id: no letter, digit or underscore directly before it, and after it no letter, digit,
    underscore, ``-<letter or digit>`` (a longer id) or ``.<digit>`` (a child suffix it could not
    take whole). The note "Renamed from <old id> ..." is deliberately not matched, so it survives
    re-runs and ``--check`` does not count it.

    Args:
        renamed_on: The date written into the rename notes, e.g. ``"2026-10-20"``.
        ids: Old epic id -> new epic id.
    """

    renamed_on: str
    ids: Dict[str, str]

    NOTE_PREFIX = "Renamed from "

    @classmethod
    def load(cls, path: Path) -> "IdRenaming":
        """Read the mapping file.

        Args:
            path: The YAML file with ``renamed_on`` and ``ids``.

        Returns:
            The renaming it describes.

        Raises:
            ValueError: If the file lacks either key, an id maps to itself, or two old ids map to
                one new id.
        """
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "renamed_on" not in data or not isinstance(data.get("ids"), dict):
            raise ValueError(f"{path}: expected the keys 'renamed_on' and 'ids'")
        ids = {str(old): str(new) for old, new in data["ids"].items()}
        if any(old == new for old, new in ids.items()):
            raise ValueError(f"{path}: an id maps to itself")
        if len(set(ids.values())) != len(ids):
            raise ValueError(f"{path}: two old ids map to one new id")
        return cls(renamed_on=str(data["renamed_on"]), ids=ids)

    def pattern(self) -> "re.Pattern[str]":
        """Return the regular expression that matches an old id (with its child suffixes) as a whole id."""
        alternatives = "|".join(re.escape(old) for old in sorted(self.ids, key=len, reverse=True))
        return re.compile(
            rf"(?<![A-Za-z0-9_])(?<!{re.escape(self.NOTE_PREFIX)})"
            rf"(?P<old>{alternatives})(?P<children>(?:\.[0-9]+)*)"
            r"(?![A-Za-z0-9_]|-[A-Za-z0-9]|\.[0-9])"
        )

    def rewrite(self, text: str, pattern: "re.Pattern[str]") -> Tuple[str, int]:
        """Replace every old id in ``text`` by its new id, keeping child suffixes.

        Args:
            text: Any text.
            pattern: The result of :meth:`pattern`, compiled once by the caller.

        Returns:
            The rewritten text and the number of replaced mentions.
        """
        return pattern.subn(lambda match: self.ids[match.group("old")] + match.group("children"), text)

    def note(self, old_id: str) -> str:
        """Return the sentence recorded in a renamed epic's notes, e.g. "Renamed from hisim-x on 2026-10-20."."""
        return f"{self.NOTE_PREFIX}{old_id} on {self.renamed_on}."


class IssuesFile:
    """Rewrites the bead database ``.beads/issues.jsonl``, one JSON object per line.

    A line nothing changes is kept byte for byte. A changed line is written back with the separators
    it had: ``br`` writes compact JSON, a few hand-written lines use the default ``", "`` and ``": "``.
    A changed line that neither style reproduces stops the run rather than reformat it.
    """

    SEPARATOR_STYLES: Tuple[Tuple[str, str], ...] = ((",", ":"), (", ", ": "))

    @classmethod
    def separators(cls, line: str, bead: Dict[str, Any]) -> Tuple[str, str]:
        """Return the separators that reproduce ``line`` from its parsed ``bead`` exactly.

        Args:
            line: One line of the issues file, without its newline.
            bead: ``json.loads(line)``.

        Returns:
            The ``separators`` argument for ``json.dumps``.

        Raises:
            ValueError: If no known style reproduces the line.
        """
        for style in cls.SEPARATOR_STYLES:
            if json.dumps(bead, ensure_ascii=False, separators=style) == line:
                return style
        raise ValueError(f"bead {bead.get('id')}: the line does not round-trip through json.dumps; fix it by hand")

    @staticmethod
    def _rewrite_value(value: Any, renaming: IdRenaming, pattern: "re.Pattern[str]") -> Tuple[Any, int]:
        """Rewrite every string inside a JSON value, recursively; return the new value and the mention count."""
        if isinstance(value, str):
            return renaming.rewrite(value, pattern)
        if isinstance(value, list):
            items = [IssuesFile._rewrite_value(item, renaming, pattern) for item in value]
            return [item for item, _ in items], sum(count for _, count in items)
        if isinstance(value, dict):
            entries = {key: IssuesFile._rewrite_value(item, renaming, pattern) for key, item in value.items()}
            return {key: item for key, (item, _) in entries.items()}, sum(count for _, count in entries.values())
        return value, 0

    @staticmethod
    def _with_note(bead: Dict[str, Any], note: str) -> Dict[str, Any]:
        """Return ``bead`` with ``note`` appended to its notes, which ``br`` places just before ``status``."""
        if note in bead.get("notes", ""):
            return bead
        notes = f"{bead['notes']}\n\n{note}" if bead.get("notes") else note
        if "notes" in bead:
            return {**bead, "notes": notes}
        result: Dict[str, Any] = {}
        for key, value in bead.items():
            if key == "status":
                result["notes"] = notes
            result[key] = value
        result.setdefault("notes", notes)
        return result

    @classmethod
    def rewrite(cls, text: str, renaming: IdRenaming) -> Tuple[str, int]:
        """Rename the ids in the issues file's content and re-sort it by id.

        Args:
            text: The whole file.
            renaming: The ids to rename.

        Returns:
            The new content and the number of rewritten mentions (ids included).
        """
        pattern = renaming.pattern()
        lines = text.split("\n")
        trailing = lines[-1] == ""
        if trailing:
            lines = lines[:-1]
        rewritten: List[Tuple[str, str]] = []
        total = 0
        for line in lines:
            bead = json.loads(line)
            new_bead, count = cls._rewrite_value(bead, renaming, pattern)
            if new_bead["id"] != bead["id"] and bead.get("issue_type") == "epic":
                new_bead = cls._with_note(new_bead, renaming.note(bead["id"]))
            if new_bead != bead:
                line = json.dumps(new_bead, ensure_ascii=False, separators=cls.separators(line, bead))
            rewritten.append((new_bead["id"], line))
            total += count
        rewritten.sort(key=lambda entry: entry[0])
        return "\n".join(line for _, line in rewritten) + ("\n" if trailing else ""), total


class TextFiles:
    """Finds and rewrites the tracked text files of a checkout outside ``.beads/`` and the golden data."""

    @staticmethod
    def tracked(repo: Path) -> List[str]:
        """Return the paths git tracks in ``repo``, relative to it, that are candidates for rewriting.

        Args:
            repo: The repository root.

        Returns:
            Every tracked path outside :attr:`Layout.SKIPPED_PREFIXES`.
        """
        listing = subprocess.run(  # nosec B603 B607 -- fixed arguments, local git
            ["git", "-C", str(repo), "ls-files", "-z"], check=True, capture_output=True
        ).stdout.decode("utf-8")
        return [path for path in listing.split("\0") if path and not path.startswith(Layout.SKIPPED_PREFIXES)]

    @staticmethod
    def read(path: Path) -> Optional[str]:
        """Return the file's text, or ``None`` for a symlink, a missing, binary or non-UTF-8 file."""
        if path.is_symlink() or not path.is_file():
            return None
        data = path.read_bytes()
        if b"\0" in data:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None


class Renamer:
    """Applies an :class:`IdRenaming` to a checkout, or counts what is left to rename."""

    def __init__(self, repo: Path, renaming: IdRenaming) -> None:
        """Bind the renamer to a repository root and the renaming to apply there."""
        self.repo = repo
        self.renaming = renaming

    def run(self, paths: Sequence[str]) -> Counter:
        """Rename the ids in the issues file and in ``paths``, writing only the files that change.

        Args:
            paths: Text files to rewrite, relative to the repository root (usually
                :meth:`TextFiles.tracked`).

        Returns:
            The number of rewritten mentions per relative path; files without a mention are absent.
        """
        counts: Counter = Counter()
        issues = self.repo / Layout.ISSUES
        if issues.is_file():
            old = issues.read_text(encoding="utf-8")
            new, count = IssuesFile.rewrite(old, self.renaming)
            if new != old:
                issues.write_text(new, encoding="utf-8")
            if count:
                counts[Layout.ISSUES] = count
        pattern = self.renaming.pattern()
        for relative in paths:
            path = self.repo / relative
            text = TextFiles.read(path)
            if text is None:
                continue
            new, count = self.renaming.rewrite(text, pattern)
            if count:
                path.write_bytes(new.encode("utf-8"))
                counts[relative] = count
        return counts

    def remaining(self, paths: Sequence[str]) -> Counter:
        """Count the old ids still mentioned in the issues file and in ``paths``, without writing.

        Args:
            paths: Text files to inspect, relative to the repository root.

        Returns:
            The number of old-id mentions per relative path; files without a mention are absent.
        """
        pattern = self.renaming.pattern()
        counts: Counter = Counter()
        for relative in [Layout.ISSUES, *paths]:
            text = TextFiles.read(self.repo / relative)
            if text is not None:
                count = len(pattern.findall(text))
                if count:
                    counts[relative] = count
        return counts


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse the command line, rename (or check), print the counts per file.

    Args:
        argv: The arguments; ``None`` reads ``sys.argv``.

    Returns:
        0 on success; 1 when ``--check`` finds an old id.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path("."), help="repository root (default: the current directory)")
    parser.add_argument("--mapping", type=Path, default=None, help=f"mapping file (default: <repo>/{Layout.MAPPING})")
    parser.add_argument("--check", action="store_true", help="only count old ids still mentioned; exit 1 if any")
    args = parser.parse_args(argv)
    repo: Path = args.repo
    renaming = IdRenaming.load(args.mapping or repo / Layout.MAPPING)
    renamer = Renamer(repo, renaming)
    paths = TextFiles.tracked(repo)
    counts = renamer.remaining(paths) if args.check else renamer.run(paths)
    for relative, count in sorted(counts.items()):
        print(f"{count:6d}  {relative}")
    verb = "old-id mentions left" if args.check else "mentions rewritten"
    print(f"{sum(counts.values()):6d}  {verb} in {len(counts)} files")
    return 1 if args.check and counts else 0


if __name__ == "__main__":
    sys.exit(main())
