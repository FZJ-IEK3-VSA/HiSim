"""Refreshes the vendored RenoVisor contract files from a local checkout of the contract repository.

Usage::

    python -m hisim.renovisor.contract.refresh /path/to/renovisor-api-contract \
        --proposals ~/contract-proposals

A vendored file has one of two source kinds. A **git source** is a branch (or any git ref) of the
contract repository and a path inside it; the script reads the file at that ref with ``git show``
and records the commit the ref resolved to and its date. A **local source** is a file in a
directory outside any repository -- today the frontend side's ``~/contract-proposals`` -- and is
recorded with the phrase naming where it came from instead of a commit, because the proposal
directory is not versioned. Both kinds record the SHA-256 of the content written, and
``tests/test_renovisor_contract.py`` recomputes the hashes, so a vendored copy that was edited by
hand, or a refresh that did not run to completion, fails the build.

The sources are class attributes of :class:`ContractSources` so that a file moving to another
branch (``materials.yaml`` lives on the ``materials`` branch today), or a proposal file moving
into the contract repository, is a one-line change here and nowhere else.
"""

import argparse
import datetime
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple

import yaml

from hisim.renovisor.contract import ContractFiles


class ContractSources:
    """Where each vendored file comes from inside the contract repository.

    ``BY_FILENAME`` maps the vendored file name to ``(git ref, path in the repository)``. The
    ref is resolved to a commit at refresh time and that commit is what ``PINNED.yaml`` records,
    so the pin names an immutable revision even when the ref is a moving branch.
    """

    #: The repository the files are taken from, recorded verbatim in ``PINNED.yaml``.
    REPOSITORY: ClassVar[str] = "https://github.com/climatemedia/renovisor-api-contract"

    #: vendored file name -> (git ref, path inside the repository)
    BY_FILENAME: ClassVar[Dict[str, Tuple[str, str]]] = {
        ContractFiles.OPENAPI_FILENAME: ("origin/main", "openapi.yaml"),
        ContractFiles.MEASURES_FILENAME: ("origin/main", "measures.yaml"),
        ContractFiles.MATERIALS_FILENAME: ("origin/main", "materials.yaml"),
    }

    #: vendored file name -> file name inside the proposal directory, for the files that have no
    #: home in the contract repository yet.
    LOCAL_BY_FILENAME: ClassVar[Dict[str, str]] = {
        ContractFiles.REQUEST_SCHEMA_FILENAME: "calculation-request.schema.json",
        ContractFiles.REQUEST_MOCKUP_FILENAME: "calculation-request.mockup-1.yaml",
        ContractFiles.CAPABILITIES_SCHEMA_FILENAME: "measure-capabilities.openapi.yaml",
    }

    #: The phrase recorded as the ``source`` of every locally vendored file. It names the
    #: directory and the day the proposal was read, which is all the provenance an unversioned
    #: directory can carry.
    LOCAL_SOURCE: ClassVar[str] = "contract-proposals 2026-09-19"

    #: Vendored files that are kept for the record but must not be read as the truth about
    #: anything. ``openapi.yaml`` is the v0.3 draft the request schema supersedes.
    NOT_AUTHORITATIVE: ClassVar[Dict[str, str]] = {
        ContractFiles.OPENAPI_FILENAME: (
            "v0.3 draft written before the energy-system redesign; superseded by "
            "calculation-request.schema.json, which the translator validates against"
        ),
    }


class ContractRefresher:
    """Copies the contract files from a checkout and writes the pin record.

    Args:
        checkout: Path of a local clone of the contract repository. Refs are resolved in that
            clone as they are; the script does not fetch, so run ``git fetch`` first when the
            latest revision is wanted.
        proposals: Directory holding the frontend side's proposal files. Omit it to leave the
            locally vendored files untouched and refresh only the git-sourced ones.
        target_directory: Where the vendored copies are written; defaults to the directory of
            :mod:`hisim.renovisor.contract`.
    """

    def __init__(
        self,
        checkout: Path,
        proposals: Optional[Path] = None,
        target_directory: Optional[Path] = None,
    ) -> None:
        """Store the source and target paths; nothing is read or written until :meth:`run`."""
        self.checkout = checkout
        self.proposals = proposals
        self.target_directory = target_directory if target_directory is not None else ContractFiles.DIRECTORY

    def _git(self, *arguments: str) -> str:
        """Run one git command inside the checkout and return its stdout as text."""
        completed = subprocess.run(
            ["git", *arguments], cwd=self.checkout, check=True, capture_output=True, text=True
        )
        return completed.stdout

    def _resolve(self, ref: str) -> str:
        """Resolve a ref to its full commit hash, trying ``origin/<ref>`` when the local ref is absent."""
        for candidate in (ref, f"origin/{ref}"):
            try:
                return self._git("rev-parse", "--verify", f"{candidate}^{{commit}}").strip()
            except subprocess.CalledProcessError:
                continue
        raise SystemExit(f"Ref '{ref}' does not exist in {self.checkout} (tried '{ref}' and 'origin/{ref}').")

    def run(self) -> Dict[str, Dict[str, Any]]:
        """Copy every source file, write ``PINNED.yaml`` and return the pin entries written.

        Returns:
            The ``files`` mapping of the pin record: vendored file name to a dictionary with the
            source description and the ``sha256`` of the content written. A git-sourced file
            carries ``ref``, ``path``, ``commit`` and ``commit_date``; a locally sourced one
            carries ``source`` and ``path``.
        """
        previous = self._previous_entries()
        entries: Dict[str, Dict[str, Any]] = {}
        for filename, (ref, path_in_repository) in ContractSources.BY_FILENAME.items():
            commit = self._resolve(ref)
            content = self._git("show", f"{commit}:{path_in_repository}")
            commit_date = self._git("log", "-1", "--format=%cI", commit).strip()
            entries[filename] = {
                "ref": ref,
                "path": path_in_repository,
                "commit": commit,
                "commit_date": commit_date,
                "sha256": self._write(filename, content),
            }
        for filename, source_name in ContractSources.LOCAL_BY_FILENAME.items():
            if self.proposals is None:
                kept = previous.get(filename)
                if kept is None:
                    raise SystemExit(
                        f"'{filename}' has never been vendored; pass --proposals to copy it in."
                    )
                entries[filename] = kept
                continue
            content = (self.proposals / source_name).read_text(encoding="utf-8")
            entries[filename] = {
                "source": ContractSources.LOCAL_SOURCE,
                "path": source_name,
                "sha256": self._write(filename, content),
            }
        for filename, note in ContractSources.NOT_AUTHORITATIVE.items():
            if filename in entries:
                entries[filename]["authoritative"] = False
                entries[filename]["note"] = note
        pin = {
            "repository": ContractSources.REPOSITORY,
            "refreshed_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "files": entries,
        }
        header = (
            "# Written by `python -m hisim.renovisor.contract.refresh`; do not edit by hand.\n"
            "# Records which contract revision each vendored file was copied from and the\n"
            "# SHA-256 it had; tests/test_renovisor_contract.py recomputes the hashes.\n"
        )
        (self.target_directory / ContractFiles.PINNED_FILENAME).write_text(
            header + yaml.safe_dump(pin, sort_keys=False), encoding="utf-8"
        )
        return entries

    def _write(self, filename: str, content: str) -> str:
        """Write one vendored copy and return the SHA-256 of what was written.

        Args:
            filename: The vendored file name inside the contract directory.
            content: The file's text, exactly as the source had it.

        Returns:
            The hexadecimal SHA-256 of the UTF-8 encoding of *content*.
        """
        (self.target_directory / filename).write_text(content, encoding="utf-8")
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _previous_entries(self) -> Dict[str, Dict[str, Any]]:
        """Return the pin entries of the record as it stands, or an empty mapping when it has none.

        A refresh that names no proposal directory keeps the locally vendored files and their
        entries exactly as they were, so refreshing only the contract repository never drops a
        pin.
        """
        path = self.target_directory / ContractFiles.PINNED_FILENAME
        if not path.is_file():
            return {}
        with path.open(encoding="utf-8") as handle:
            record = yaml.safe_load(handle) or {}
        files = record.get("files")
        return dict(files) if isinstance(files, dict) else {}


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry point: refresh the vendored files from the given checkout."""
    parser = argparse.ArgumentParser(description="Refresh the vendored RenoVisor contract files.")
    parser.add_argument("checkout", help="local clone of renovisor-api-contract")
    parser.add_argument(
        "--proposals",
        default=None,
        help="directory holding the frontend side's proposal files (~/contract-proposals)",
    )
    arguments = parser.parse_args(argv)
    proposals = Path(arguments.proposals).expanduser().resolve() if arguments.proposals else None
    entries = ContractRefresher(
        Path(arguments.checkout).expanduser().resolve(), proposals=proposals
    ).run()
    for filename, entry in entries.items():
        if "commit" in entry:
            origin = f"{entry['ref']}@{entry['commit'][:12]} ({entry['commit_date']})"
        else:
            origin = f"{entry['source']}:{entry['path']}"
        print(f"{filename:36} <- {origin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
