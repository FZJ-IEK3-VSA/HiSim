"""The vendored RenoVisor contract files match what ``PINNED.yaml`` says was copied.

A vendored copy is only trustworthy while it is byte-identical to the revision it claims to
come from. These tests recompute the SHA-256 of every vendored file and compare it with the
pin, so a hand edit or a half-done refresh fails the build. They also parse each file, so a copy
that is not valid YAML or JSON -- or a request schema that lost the definitions the validator
resolves -- is caught before any translation code reads it.

Two of the six pinned files come from the contract repository at a named commit, three from the
frontend side's proposal directory with only the phrase naming where they were read, and one --
``openapi.yaml`` -- is pinned with ``authoritative: false`` because the request schema
supersedes it and it is kept only so that the revision the branch once aligned against stays a
committed fact. A seventh file, ``measure-capabilities.results-extension.yaml``, is HiSim's own
proposal back to the frontend team and is deliberately unpinned; it is listed in
``ContractFiles.HISIM_AUTHORED`` so that the "everything here is pinned" test stays exact
instead of being loosened.
"""

import hashlib

from pathlib import Path

import pytest

from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.contract.refresh import ContractSources


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
            origin = entry.get("commit", entry.get("source", "?"))
            assert actual == entry["sha256"], (
                f"{filename} differs from the pinned revision {origin}; run "
                "`python -m hisim.renovisor.contract.refresh <checkout>` instead of editing the copy"
            )

    def test_a_git_sourced_file_records_a_full_commit_and_a_local_one_its_directory(self) -> None:
        """The two source kinds carry the provenance each of them can carry, and no less."""
        for filename, entry in ContractFiles.pinned()["files"].items():
            if "commit" in entry:
                assert len(entry["commit"]) == 40, f"{filename}: no full commit hash"
                assert entry["commit_date"], f"{filename}: no commit date"
            else:
                assert entry["source"], f"{filename}: neither a commit nor a source phrase"
                assert entry["path"], f"{filename}: no path inside the source directory"

    def test_every_vendored_file_is_pinned(self) -> None:
        """No *copied* contract file lies beside PINNED.yaml without being recorded in it.

        HiSim's own proposal files are the exception and are named as such: a pin records which
        revision of somebody else's file a copy came from, and a file HiSim wrote has none.
        """
        pinned = set(ContractFiles.pinned()["files"])
        present = {
            path.name
            for pattern in ("*.yaml", "*.json")
            for path in ContractFiles.DIRECTORY.glob(pattern)
        } - {ContractFiles.PINNED_FILENAME} - set(ContractFiles.HISIM_AUTHORED)
        assert present == pinned, f"unpinned or missing contract files: {present ^ pinned}"

    def test_the_hisim_authored_results_extension_is_present_and_unpinned(self) -> None:
        """The HiSim proposal for the capability document's ``results`` section, beside the copies."""
        assert ContractFiles.RESULTS_EXTENSION_FILENAME in ContractFiles.HISIM_AUTHORED
        assert ContractFiles.RESULTS_EXTENSION_FILENAME not in ContractFiles.pinned()["files"]

        schemas = ContractFiles.results_extension_schema()["components"]["schemas"]
        for name in ("ResultFields", "ResultField"):
            assert name in schemas, f"the results extension lacks {name}"

    def test_the_superseded_openapi_is_pinned_as_not_authoritative(self) -> None:
        """The v0.3 draft stays vendored and says of itself that nothing may be read from it."""
        entry = ContractFiles.pinned()["files"][ContractFiles.OPENAPI_FILENAME]
        assert entry["authoritative"] is False
        assert "calculation-request.schema.json" in entry["note"]

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
        for name in ("ImplementedMeasures", "ImplementedMeasure", "ImplementedOption"):
            assert name in schemas, f"measure-capabilities.openapi.yaml lacks {name}"

    def test_measures_and_materials_parse_to_their_lists(self) -> None:
        """The catalogue and the materials dump parse and expose their top-level lists."""
        assert ContractFiles.measures()["measures"], "measures.yaml has no measures"
        assert ContractFiles.materials()["materials"], "materials.yaml has no materials"


@pytest.mark.base
class TestTheSharedFolder:
    """The vendored copies equal the shared specifications wherever the shared folder exists.

    ``/home/contract-proposals`` is the single home of every specification the three repositories
    share; the copies under ``hisim/renovisor/contract/`` exist only because CI and the container
    image cannot see that folder. On a machine that has it, a copy that differs from the shared
    file is drift, and this test says so by name; elsewhere it skips.
    """

    def test_every_locally_vendored_file_equals_the_shared_one(self) -> None:
        """Byte-for-byte equality with the shared folder, or a skip where the folder is absent."""
        shared = Path(ContractSources.SHARED_DIRECTORY)
        if not shared.is_dir():
            pytest.skip(f"{shared} is not on this machine; CI and the image vendor the files instead")
        pinned = ContractFiles.pinned()["files"]
        compared = 0
        for filename, entry in pinned.items():
            if entry.get("source") != ContractSources.LOCAL_SOURCE:
                continue
            shared_file = shared / filename
            assert shared_file.is_file(), f"{filename} is vendored from the shared folder but no longer there"
            assert ContractFiles.path(filename).read_bytes() == shared_file.read_bytes(), (
                f"{filename} differs from {shared_file}; run `python -m hisim.renovisor.contract.refresh "
                "<contract checkout>` rather than editing either copy"
            )
            compared += 1
        assert compared > 0, "no file is vendored from the shared folder any more; drop this test"
