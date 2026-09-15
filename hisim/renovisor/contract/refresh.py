"""Refreshes the vendored RenoVisor contract files from a local checkout of the contract repository.

Usage::

    python -m hisim.renovisor.contract.refresh /path/to/renovisor-api-contract

Each vendored file has a source: a branch (or any git ref) of the contract repository and a
path inside it. The script reads the file at that ref with ``git show``, writes it beside this
module, and records in ``PINNED.yaml`` the repository, the ref, the commit the ref resolved to,
the commit date and the SHA-256 of the content written. ``tests/test_renovisor_contract.py``
recomputes the hashes, so a vendored copy that was edited by hand, or a refresh that did not run
to completion, fails the build.

The sources are class attributes of :class:`ContractSources` so that a file moving to another
branch (``materials.yaml`` lives on the ``materials`` branch today) is a one-line change here and
nowhere else.
"""

import argparse
import datetime
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import ClassVar, Dict, List, Optional, Tuple

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
        ContractFiles.OPENAPI_FILENAME: ("main", "openapi.yaml"),
        ContractFiles.MEASURES_FILENAME: ("main", "measures.yaml"),
        ContractFiles.MATERIALS_FILENAME: ("materials", "materials.yaml"),
    }


class ContractRefresher:
    """Copies the contract files from a checkout and writes the pin record.

    Args:
        checkout: Path of a local clone of the contract repository. Refs are resolved in that
            clone as they are; the script does not fetch, so run ``git fetch`` first when the
            latest revision is wanted.
        target_directory: Where the vendored copies are written; defaults to the directory of
            :mod:`hisim.renovisor.contract`.
    """

    def __init__(self, checkout: Path, target_directory: Optional[Path] = None) -> None:
        """Store the checkout and target paths; nothing is read or written until :meth:`run`."""
        self.checkout = checkout
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

    def run(self) -> Dict[str, Dict[str, str]]:
        """Copy every source file, write ``PINNED.yaml`` and return the pin entries written.

        Returns:
            The ``files`` mapping of the pin record: vendored file name to a dictionary with
            ``ref``, ``path``, ``commit``, ``commit_date`` and ``sha256``.
        """
        entries: Dict[str, Dict[str, str]] = {}
        for filename, (ref, path_in_repository) in ContractSources.BY_FILENAME.items():
            commit = self._resolve(ref)
            content = self._git("show", f"{commit}:{path_in_repository}")
            commit_date = self._git("log", "-1", "--format=%cI", commit).strip()
            target = self.target_directory / filename
            target.write_text(content, encoding="utf-8")
            entries[filename] = {
                "ref": ref,
                "path": path_in_repository,
                "commit": commit,
                "commit_date": commit_date,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
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


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry point: refresh the vendored files from the given checkout."""
    parser = argparse.ArgumentParser(description="Refresh the vendored RenoVisor contract files.")
    parser.add_argument("checkout", help="local clone of renovisor-api-contract")
    arguments = parser.parse_args(argv)
    entries = ContractRefresher(Path(arguments.checkout).expanduser().resolve()).run()
    for filename, entry in entries.items():
        print(f"{filename:16} <- {entry['ref']}@{entry['commit'][:12]} ({entry['commit_date']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
