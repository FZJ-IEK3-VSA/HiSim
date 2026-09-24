"""Refreshes the vendored RenoVisor contract files from local checkouts of the two repositories.

Usage::

    python -m hisim.renovisor.contract.refresh /path/to/renovisor-api-contract --specs /path/to/renovisorissues
    python -m hisim.renovisor.contract.refresh /path/to/renovisor-api-contract --specs ''

``--specs`` is required and has no default: a clone refreshes the three spec copies with the
contract files, and ``--specs ''`` keeps them and their pin entries exactly as they are, so the
contract repository can be refreshed alone. Which of the two a run does is therefore always
written on its command line (owner decision 2026-09-24).

Every vendored file comes from a git repository at a ref, and the script reads it there with
``git show``, recording the commit the ref resolved to and its date. Since 2026-09-23 there are two
repositories. The **contract repository** (``renovisor-api-contract``, the positional checkout)
holds the contract files at its root: the measure catalogue, the superseded OpenAPI draft and the
home inventory schema it references. The **specs repository** (``renovisorissues`` on jugit,
``--specs``) holds, under ``specs/``, the shared specifications all packages read, among them the
request schema the translator validates against, the worked example and the capability
document's schema. They moved there from the contract repository's ``specs/`` folder, which was
read from its working tree without a commit; now every pin names an immutable revision.

Every entry of ``PINNED.yaml`` records its repository, ref, path, commit, commit date and the
SHA-256 of the content written, and ``tests/renovisor/test_contract.py`` recomputes the hashes, so
a vendored copy that was edited by hand, or a refresh that did not run to completion, fails the
build. The script does not fetch: run ``git fetch`` in both checkouts first when the latest
revision is wanted.

A refresh reads everything before it writes anything. Every ref is resolved and every file read
before the first copy is written, so a ref that does not resolve, a path missing at the resolved
commit, a checkout that is not a git clone, a git call that hangs past its timeout or a pin entry
``--specs ''`` cannot keep ends the run with a message naming it and leaves the vendored
directory untouched.

The sources are class attributes of :class:`ContractSources` so that a file moving to another
path, branch or repository is a one-line change here and nowhere else. A file dropped from those
attributes is dropped from the pin as well: the pin is written from the sources every run, so a
vendored copy stops being recorded the moment it stops being a source. That is what happened to
``materials.yaml`` on 2026-09-20, when HiSim stopped vendoring the material database it never
reads.
"""

import argparse
import datetime
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple

import yaml

from hisim.renovisor.contract import ContractFiles


class ContractSources:
    """Where each vendored file comes from: which repository, at which ref, under which path.

    ``CONTRACT_BY_FILENAME`` and ``SPECS_BY_FILENAME`` map the vendored file name to
    ``(git ref, path in the repository)`` for the contract and the specs repository. The ref is
    resolved to a commit at refresh time and that commit is what ``PINNED.yaml`` records, so the
    pin names an immutable revision even when the ref is a moving branch.
    """

    #: The contract repository, recorded verbatim on each of its files' pin entries.
    CONTRACT_REPOSITORY: ClassVar[str] = "https://github.com/climatemedia/renovisor-api-contract"

    #: The repository the shared specifications live in since 2026-09-23, with the packages' issues.
    SPECS_REPOSITORY: ClassVar[str] = "https://jugit.fz-juelich.de/iek-3/groups/urbanmodels/renovisorissues"

    #: Where the specs repository is cloned on the machine the agents share. It is not a
    #: ``--specs`` default -- the refresh names its clone explicitly -- but the place
    #: ``tests/renovisor/test_contract.py`` compares the spec copies with. CI and the container
    #: image have no clone, which is why the files are vendored.
    SPECS_CHECKOUT: ClassVar[str] = "/home/renovisorissues/repo"

    #: vendored file name -> (git ref, path inside the contract repository)
    CONTRACT_BY_FILENAME: ClassVar[Dict[str, Tuple[str, str]]] = {
        ContractFiles.OPENAPI_FILENAME: ("origin/main", "openapi.yaml"),
        ContractFiles.HOMEINVENTORY_FILENAME: ("origin/main", "homeinventory.yaml"),
        ContractFiles.MEASURES_FILENAME: ("origin/main", "measures.yaml"),
    }

    #: vendored file name -> (git ref, path inside the specs repository)
    SPECS_BY_FILENAME: ClassVar[Dict[str, Tuple[str, str]]] = {
        ContractFiles.REQUEST_SCHEMA_FILENAME: ("origin/main", "specs/calculation-request.schema.json"),
        ContractFiles.REQUEST_MOCKUP_FILENAME: ("origin/main", "specs/calculation-request.mockup-1.yaml"),
        ContractFiles.CAPABILITIES_SCHEMA_FILENAME: ("origin/main", "specs/measure-capabilities.openapi.yaml"),
    }

    #: vendored file name -> the ``note`` its pin entry carries, for a copy that deliberately
    #: differs from its source. The note names the revision it deviates from and why, so the
    #: deviation is a recorded fact rather than unexplained drift. Empty since 2026-09-20: every
    #: vendored copy is its source's own bytes.
    NOTES: ClassVar[Dict[str, str]] = {}

    #: Vendored files that are kept for the record but must not be read as the truth about
    #: anything. ``openapi.yaml`` is the v0.3 draft the request schema supersedes, and
    #: ``homeinventory.yaml`` is the part of it contract PR #10 split into its own file.
    NOT_AUTHORITATIVE: ClassVar[Dict[str, str]] = {
        ContractFiles.OPENAPI_FILENAME: (
            "v0.3 draft written before the energy-system redesign; superseded by "
            "calculation-request.schema.json, which the translator validates against"
        ),
        ContractFiles.HOMEINVENTORY_FILENAME: (
            "HomeInventoryInput of the v0.3 draft, split out of openapi.yaml and referenced from "
            "it; the house the translator reads is calculation-request.schema.json's"
        ),
    }


class ContractRefresher:
    """Copies the contract files from the two checkouts and writes the pin record.

    Args:
        checkout: Path of a local clone of the contract repository.
        specs_checkout: Path of a local clone of the specs repository. ``None`` leaves the files
            vendored from it, and their pin entries, exactly as they are, so the contract
            repository can be refreshed alone.
        target_directory: Where the vendored copies are written; defaults to the directory of
            :mod:`hisim.renovisor.contract`.
    """

    #: Seconds any one git call may take before the refresh gives up on it by name.
    GIT_TIMEOUT_SECONDS: ClassVar[int] = 60

    #: The keys a kept pin entry must carry to be reused verbatim by a run with ``--specs ''``.
    KEPT_ENTRY_KEYS: ClassVar[Tuple[str, ...]] = ("repository", "ref", "path", "commit", "commit_date", "sha256")

    def __init__(
        self,
        checkout: Path,
        specs_checkout: Optional[Path] = None,
        target_directory: Optional[Path] = None,
    ) -> None:
        """Store the source and target paths; nothing is read or written until :meth:`run`."""
        self.checkout = checkout
        self.specs_checkout = specs_checkout
        self.target_directory = target_directory if target_directory is not None else ContractFiles.DIRECTORY

    @classmethod
    def _git(cls, checkout: Path, *arguments: str) -> str:
        """Run one git command inside a checkout and return its stdout as text.

        Raises:
            subprocess.CalledProcessError: When git exits non-zero; the caller names what failed.
            SystemExit: When git runs longer than :attr:`GIT_TIMEOUT_SECONDS`.
        """
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=checkout,
                check=True,
                capture_output=True,
                text=True,
                timeout=cls.GIT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            raise SystemExit(
                f"`git {' '.join(arguments)}` in {checkout} did not finish within {cls.GIT_TIMEOUT_SECONDS} s."
            ) from error
        return completed.stdout

    @classmethod
    def _require_clone(cls, checkout: Path, argument: str) -> None:
        """Stop with a message naming *argument* unless *checkout* is a git clone.

        ``git rev-parse --git-dir`` is the probe rather than a look for a ``.git`` directory,
        because in a git worktree ``.git`` is a file.

        Raises:
            SystemExit: When the path is not a directory or git does not recognise it as a clone.
        """
        if not checkout.is_dir():
            raise SystemExit(f"{argument} {checkout} is not a directory, let alone a git clone.")
        try:
            cls._git(checkout, "rev-parse", "--git-dir")
        except subprocess.CalledProcessError as error:
            raise SystemExit(f"{argument} {checkout} is not a git clone: {error.stderr.strip()}") from error

    @classmethod
    def _resolve(cls, checkout: Path, ref: str) -> str:
        """Resolve a ref to its full commit hash.

        Raises:
            SystemExit: When the ref names no commit in the checkout.
        """
        try:
            return cls._git(checkout, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
        except subprocess.CalledProcessError as error:
            raise SystemExit(
                f"Ref '{ref}' does not resolve to a commit in {checkout}; run `git fetch` there first."
            ) from error

    def run(self) -> Dict[str, Dict[str, Any]]:
        """Copy every source file, write ``PINNED.yaml`` and return the pin entries written.

        Everything is read first and written after: a failure anywhere in the read phase leaves
        the vendored directory exactly as it was.

        Returns:
            The ``files`` mapping of the pin record: vendored file name to a dictionary with
            ``repository``, ``ref``, ``path``, ``commit``, ``commit_date`` and the ``sha256`` of the
            content written, plus ``authoritative`` and ``note`` where
            :attr:`ContractSources.NOT_AUTHORITATIVE` or :attr:`ContractSources.NOTES` names it.

        Raises:
            SystemExit: When a checkout is not a git clone, a ref does not resolve, a path is
                missing at the resolved commit, a git call times out, or no specs checkout is
                given while a file vendored from it has no complete pin entry to keep.
        """
        previous = self._previous_entries()
        self._require_clone(self.checkout, "CONTRACT_CHECKOUT")
        if self.specs_checkout is not None:
            self._require_clone(self.specs_checkout, "--specs")
        entries: Dict[str, Dict[str, Any]] = {}
        contents: Dict[str, str] = {}
        commits: Dict[Tuple[Path, str], Tuple[str, str]] = {}
        sources = (
            (self.checkout, ContractSources.CONTRACT_REPOSITORY, ContractSources.CONTRACT_BY_FILENAME),
            (self.specs_checkout, ContractSources.SPECS_REPOSITORY, ContractSources.SPECS_BY_FILENAME),
        )
        for checkout, repository, table in sources:
            for filename, (ref, path_in_repository) in table.items():
                if checkout is None:
                    entries[filename] = self._kept(previous, filename, repository, ref, path_in_repository)
                    continue
                if (checkout, ref) not in commits:
                    commit = self._resolve(checkout, ref)
                    commit_date = self._git(checkout, "log", "-1", "--format=%cI", commit).strip()
                    commits[(checkout, ref)] = (commit, commit_date)
                commit, commit_date = commits[(checkout, ref)]
                content = self._show(checkout, repository, filename, commit, path_in_repository)
                contents[filename] = content
                entries[filename] = {
                    "repository": repository,
                    "ref": ref,
                    "path": path_in_repository,
                    "commit": commit,
                    "commit_date": commit_date,
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
        for filename, note in ContractSources.NOT_AUTHORITATIVE.items():
            if filename in entries:
                entries[filename]["authoritative"] = False
                entries[filename]["note"] = note
        for filename, note in ContractSources.NOTES.items():
            if filename in entries:
                entries[filename]["note"] = note
        pin = {
            "refreshed_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "files": entries,
        }
        header = (
            "# Written by `python -m hisim.renovisor.contract.refresh`; do not edit by hand.\n"
            "# Records which repository revision each vendored file was copied from and the\n"
            "# SHA-256 it had; tests/renovisor/test_contract.py recomputes the hashes.\n"
        )
        for filename, content in contents.items():
            self._write(filename, content)
        (self.target_directory / ContractFiles.PINNED_FILENAME).write_text(
            header + yaml.safe_dump(pin, sort_keys=False), encoding="utf-8"
        )
        return entries

    def _show(self, checkout: Path, repository: str, filename: str, commit: str, path_in_repository: str) -> str:
        """Return one file's text at a commit, or stop naming the vendored file and where it was sought.

        Raises:
            SystemExit: When the path does not exist at that commit.
        """
        try:
            return self._git(checkout, "show", f"{commit}:{path_in_repository}")
        except subprocess.CalledProcessError as error:
            raise SystemExit(
                f"'{filename}' is vendored from {repository}, but {commit}:{path_in_repository} does not exist "
                f"in {checkout}: {error.stderr.strip()}"
            ) from error

    @classmethod
    def _kept(
        cls, previous: Dict[str, Dict[str, Any]], filename: str, repository: str, ref: str, path_in_repository: str
    ) -> Dict[str, Any]:
        """Return the pin entry a run with ``--specs ''`` keeps for one file, after checking its shape.

        Only the keys of :attr:`KEPT_ENTRY_KEYS` are kept; ``authoritative`` and ``note`` are
        applied afresh from :class:`ContractSources`, like for every other entry.

        Raises:
            SystemExit: When the entry is missing, of an older format or incomplete, or records a
                repository, ref or path other than the source table's.
        """

        def refuse(problem: str) -> SystemExit:
            return SystemExit(
                f"'{filename}' {problem}, so --specs '' has nothing to keep; run once with "
                "--specs <clone of renovisorissues>."
            )

        kept = previous.get(filename)
        if not isinstance(kept, dict):
            raise refuse("has no pin entry")
        missing = [key for key in cls.KEPT_ENTRY_KEYS if kept.get(key) in (None, "")]
        if missing:
            raise refuse(f"has a pin entry without {', '.join(missing)}")
        if (kept["repository"], kept["ref"], kept["path"]) != (repository, ref, path_in_repository):
            raise refuse(
                f"is pinned to {kept['repository']} {kept['ref']}:{kept['path']}, not to its source "
                f"{repository} {ref}:{path_in_repository}"
            )
        if not re.fullmatch(r"[0-9a-f]{40}", str(kept["commit"])):
            raise refuse(f"has a pin entry whose commit {kept['commit']!r} is not a full hexadecimal hash")
        if not re.fullmatch(r"[0-9a-f]{64}", str(kept["sha256"])):
            raise refuse(f"has a pin entry whose sha256 {kept['sha256']!r} is not a hexadecimal SHA-256")
        return {key: kept[key] for key in cls.KEPT_ENTRY_KEYS}

    def _write(self, filename: str, content: str) -> None:
        """Write one vendored copy.

        Args:
            filename: The vendored file name inside the contract directory.
            content: The file's text, exactly as the source had it.
        """
        (self.target_directory / filename).write_text(content, encoding="utf-8")

    def _previous_entries(self) -> Dict[str, Dict[str, Any]]:
        """Return the pin entries of the record as it stands, or an empty mapping when it has none.

        A refresh without a specs checkout keeps the files vendored from it and their entries
        exactly as they were, so refreshing only the contract repository never drops a pin.
        """
        path = self.target_directory / ContractFiles.PINNED_FILENAME
        if not path.is_file():
            return {}
        with path.open(encoding="utf-8") as handle:
            record = yaml.safe_load(handle) or {}
        files = record.get("files") if isinstance(record, dict) else None
        return dict(files) if isinstance(files, dict) else {}


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry point: refresh the vendored files from the given checkouts."""
    parser = argparse.ArgumentParser(
        prog="python -m hisim.renovisor.contract.refresh",
        usage="%(prog)s CONTRACT_CHECKOUT --specs {RENOVISORISSUES_CLONE | ''}",
        description="Refresh the vendored RenoVisor contract files.",
    )
    parser.add_argument("checkout", metavar="CONTRACT_CHECKOUT", help="local clone of renovisor-api-contract")
    parser.add_argument(
        "--specs",
        metavar="CLONE",
        default=None,
        help="required: a local clone of renovisorissues, whose specs/ holds the shared specifications, "
        "to refresh the three spec copies too; or '' to keep them and their pins as they are",
    )
    arguments = parser.parse_args(argv)
    if arguments.specs is None:
        parser.error(
            "--specs is required: pass --specs <clone of renovisorissues> to refresh the spec copies, "
            "or --specs '' to keep them and their pins"
        )
    specs = Path(arguments.specs).expanduser().resolve() if arguments.specs else None
    entries = ContractRefresher(Path(arguments.checkout).expanduser().resolve(), specs_checkout=specs).run()
    for filename, entry in entries.items():
        print(f"{filename:36} <- {entry['repository']} {entry['ref']}@{entry['commit'][:12]} ({entry['commit_date']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
