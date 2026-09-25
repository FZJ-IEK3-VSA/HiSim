"""The vendored RenoVisor contract files match what ``PINNED.yaml`` says was copied.

A vendored copy is only trustworthy while it is byte-identical to the revision it claims to
come from. These tests recompute the SHA-256 of every vendored file and compare it with the
pin, so a hand edit or a half-done refresh fails the build. They also parse each file, so a copy
that is not valid YAML or JSON -- or a request schema that lost the definitions the validator
resolves -- is caught before any translation code reads it.

One of the four pinned files comes from the contract repository -- the measure catalogue -- and
three from the specs repository (``renovisorissues``, where the shared specifications live since
2026-09-23), each at a named commit. The contract repository's superseded v0.3 draft,
``openapi.yaml``, and the ``homeinventory.yaml`` it references are not vendored since 2026-09-25
(hisim-4p3n), and neither is its material database. Every file in the directory is a pinned copy:
HiSim's former proposal for the capability document's ``results`` section is part of the shared
``measure-capabilities.openapi.yaml`` since 2026-09-23.
"""

import hashlib
import subprocess

from pathlib import Path
from typing import ClassVar, Tuple

import pytest

from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.contract.refresh import ContractRefresher, ContractSources


@pytest.mark.base
class TestVendoredContract:
    """Checks on the copies under ``hisim/renovisor/contract/``."""

    def test_every_pinned_file_exists_and_matches_its_hash(self) -> None:
        """Each file named in PINNED.yaml is present and hashes to the recorded SHA-256."""
        pin = ContractFiles.pinned()
        assert pin["files"], "PINNED.yaml names no files"
        for filename, entry in pin["files"].items():
            path = ContractFiles.path(filename)
            assert path.is_file(), f"{filename} is pinned but missing"
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            origin = f"{entry.get('repository', '?')}@{entry.get('commit', '?')}"
            assert actual == entry["sha256"], (
                f"{filename} differs from the pinned revision {origin}; run "
                "`python -m hisim.renovisor.contract.refresh <contract checkout> --specs <renovisorissues clone>` "
                "instead of editing the copy"
            )

    def test_every_file_records_its_repository_and_a_full_commit(self) -> None:
        """Both repositories are read at a git revision, so every pin names an immutable one."""
        repositories = {ContractSources.CONTRACT_REPOSITORY, ContractSources.SPECS_REPOSITORY}
        for filename, entry in ContractFiles.pinned()["files"].items():
            assert entry["repository"] in repositories, f"{filename}: unknown repository {entry.get('repository')}"
            assert len(entry["commit"]) == 40, f"{filename}: no full commit hash"
            assert entry["commit_date"], f"{filename}: no commit date"
            assert entry["path"], f"{filename}: no path inside the repository"

    def test_each_file_comes_from_the_repository_its_source_table_names(self) -> None:
        """The pin and :class:`ContractSources` agree on where every file lives, in both directions.

        Every pin entry has a source in ``CONTRACT_BY_FILENAME`` or ``SPECS_BY_FILENAME`` with the
        same repository, ref and path, and every source has a pin entry.
        """
        pinned = ContractFiles.pinned()["files"]
        sources = {
            filename: (repository, ref, path)
            for table, repository in (
                (ContractSources.CONTRACT_BY_FILENAME, ContractSources.CONTRACT_REPOSITORY),
                (ContractSources.SPECS_BY_FILENAME, ContractSources.SPECS_REPOSITORY),
            )
            for filename, (ref, path) in table.items()
        }
        assert set(pinned) == set(sources), (
            f"pinned without a source: {sorted(set(pinned) - set(sources))}; "
            f"a source without a pin: {sorted(set(sources) - set(pinned))}"
        )
        for filename, source in sources.items():
            entry = pinned[filename]
            recorded = (entry.get("repository"), entry.get("ref"), entry.get("path"))
            assert recorded == source, f"{filename} is pinned to {recorded}, but its source is {source}"

    def test_every_vendored_file_is_pinned(self) -> None:
        """No contract file lies beside PINNED.yaml without being recorded in it."""
        pinned = set(ContractFiles.pinned()["files"])
        present = {
            path.name
            for pattern in ("*.yaml", "*.json")
            for path in ContractFiles.DIRECTORY.glob(pattern)
        } - {ContractFiles.PINNED_FILENAME}
        assert present == pinned, f"unpinned or missing contract files: {present ^ pinned}"

    def test_the_catalogue_is_the_revision_the_frozen_table_was_written_against(self) -> None:
        """32 measures, lowercase ids, ``options`` always a list (the 2026-09-17 revision)."""
        measures = ContractFiles.measures()["measures"]
        assert len(measures) == 32
        for measure in measures:
            assert measure["id"] == measure["id"].lower()
            assert isinstance(measure["options"], list)

    def test_the_request_schema_and_the_mockup_are_readable_and_agree(self) -> None:
        """The vendored mockup validates against the vendored schema, which is the whole point."""
        from jsonschema import Draft202012Validator

        schema = ContractFiles.request_schema()
        assert schema["$schema"].endswith("2020-12/schema")
        Draft202012Validator(schema).validate(ContractFiles.request_mockup())

    def test_the_capability_schema_carries_the_shape_the_document_is_checked_against(self) -> None:
        """``ImplementedMeasures`` is what ``capabilities`` validates its output against."""
        schemas = ContractFiles.capabilities_schema()["components"]["schemas"]
        for name in (
            "ImplementedMeasures", "ImplementedMeasure", "ImplementedOption", "ImplementedField",
            "ImplementedValue", "ResultFields", "ResultField",
        ):
            assert name in schemas, f"measure-capabilities.openapi.yaml lacks {name}"

    def test_the_catalogue_parses_to_its_list(self) -> None:
        """``measures.yaml`` parses and exposes the top-level list the frozen table mirrors."""
        assert ContractFiles.measures()["measures"], "measures.yaml has no measures"


@pytest.mark.base
class TestTheMaterialDatabaseIsNotVendored:
    """No ``materials.yaml`` is vendored here, and the pin has no entry for one.

    The translator reads no material data at run time: rule 5 of the contract has the request
    carry a material's physical properties, and its ``asp_id`` travels as provenance only. A
    vendored copy of the material database would therefore be a file nothing reads, kept in step
    with the contract for nothing. The one thing worth checking about it -- that every ``material``
    option value of ``measures.yaml`` resolves to exactly one material row -- is a fact about two
    files of the contract repository, so the owner's decision of 2026-09-20 put that check with
    both files in the contract repository and dropped the copy from HiSim. This test is what would
    notice a refresh quietly bringing it back.
    """

    #: The file name this package deliberately does not hold, spelled out because there is no
    #: ``ContractFiles`` attribute for it any more.
    MATERIALS_FILENAME: ClassVar[str] = "materials.yaml"

    def test_the_vendored_directory_holds_no_material_database(self) -> None:
        """No ``materials.yaml`` beside the other vendored copies."""
        assert not (ContractFiles.DIRECTORY / self.MATERIALS_FILENAME).exists(), (
            f"{self.MATERIALS_FILENAME} is vendored again; the translator reads no material data "
            "and the resolution check lives in the contract repository's CI"
        )

    def test_the_pin_records_no_material_database(self) -> None:
        """``PINNED.yaml`` has no entry for it either, so no refresh would write one back."""
        assert self.MATERIALS_FILENAME not in ContractFiles.pinned()["files"]


@pytest.mark.base
class TestTheSupersededDraftIsNotVendored:
    """Neither ``openapi.yaml`` nor ``homeinventory.yaml`` is vendored, and the pin records neither.

    Both are the contract repository's v0.3 draft, which ``calculation-request.schema.json``
    supersedes. They were kept here with ``authoritative: false`` for the record only, and the one
    thing HiSim read from them -- the example values of the three mocked KPIs -- became HiSim's own
    constants (``hisim.renovisor.kpis.MockedKpis``), so the owner's decision of 2026-09-25
    (hisim-4p3n) dropped the copies. This test is what would notice a refresh bringing them back.
    """

    #: The file names this package deliberately does not hold, spelled out because there is no
    #: ``ContractFiles`` attribute for them any more.
    SUPERSEDED_FILENAMES: ClassVar[Tuple[str, ...]] = ("openapi.yaml", "homeinventory.yaml")

    def test_the_vendored_directory_holds_neither_file(self) -> None:
        """No copy of the draft beside the other vendored files."""
        for filename in self.SUPERSEDED_FILENAMES:
            assert not (ContractFiles.DIRECTORY / filename).exists(), f"{filename} is vendored again"

    def test_neither_the_pin_nor_the_sources_name_them(self) -> None:
        """``PINNED.yaml`` has no entry and :class:`ContractSources` no source for either file."""
        pinned = ContractFiles.pinned()["files"]
        for filename in self.SUPERSEDED_FILENAMES:
            assert filename not in pinned
            assert filename not in ContractSources.CONTRACT_BY_FILENAME


@pytest.mark.base
class TestTheSpecsCheckout:
    """The spec copies equal the specs repository's ``origin/main`` wherever its clone exists.

    ``specs/`` of the specs repository is the single home of every specification the packages
    share; the copies under ``hisim/renovisor/contract/`` exist only because CI and the container
    image have no clone. On the machine that has one (:attr:`ContractSources.SPECS_CHECKOUT`), a
    copy that differs from the fetched ``origin/main`` is drift, and this test says so by name --
    a failure by design, since the copies are to follow the specs repository (owner decision
    2026-09-24); elsewhere it skips. It reads what the clone last fetched and fetches nothing itself.
    """

    @staticmethod
    def _git(checkout: Path, *arguments: str) -> "subprocess.CompletedProcess[bytes]":
        """Run one git command in the clone, failing the test by name when it hangs."""
        try:
            completed = subprocess.run(
                ["git", "-c", "safe.directory=*", *arguments],
                cwd=checkout,
                capture_output=True,
                check=False,
                timeout=ContractRefresher.GIT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(
                f"`git {' '.join(arguments)}` in {checkout} did not finish within "
                f"{ContractRefresher.GIT_TIMEOUT_SECONDS} s"
            )
        return completed

    def test_every_spec_copy_equals_the_specs_repository(self) -> None:
        """Byte-for-byte equality with ``origin/main:specs/…``, or a skip where there is no clone."""
        assert ContractSources.SPECS_BY_FILENAME, (
            "nothing is vendored from the specs repository any more; drop this test rather than let it pass vacuously"
        )
        checkout = Path(ContractSources.SPECS_CHECKOUT)
        # ``git rev-parse --git-dir`` rather than a look for a ``.git`` directory: in a worktree it is a file.
        if not checkout.is_dir() or self._git(checkout, "rev-parse", "--git-dir").returncode != 0:
            pytest.skip(f"{checkout} is not a git clone on this machine; CI and the image vendor the files instead")
        for filename, (ref, path) in ContractSources.SPECS_BY_FILENAME.items():
            shown = self._git(checkout, "show", f"{ref}:{path}")
            assert shown.returncode == 0, f"{filename} is vendored from {ref}:{path}, which the clone does not have"
            assert ContractFiles.path(filename).read_bytes() == shown.stdout, (
                f"{filename} differs from {ref}:{path} in {checkout}; run "
                "`python -m hisim.renovisor.contract.refresh <contract checkout> --specs <renovisorissues clone>` "
                "rather than editing the copy"
            )
