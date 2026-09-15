"""The vendored RenoVisor contract files match what ``PINNED.yaml`` says was copied.

A vendored copy is only trustworthy while it is byte-identical to the contract revision it claims
to come from. These tests recompute the SHA-256 of every vendored file and compare it with the pin,
so a hand edit or a half-done refresh fails the build. They also parse each file, so a copy that is
not valid YAML, or an ``openapi.yaml`` that lost its schemas, is caught before any translation code
reads it.
"""

import hashlib

import pytest

from hisim.renovisor.contract import ContractFiles


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
            assert actual == entry["sha256"], (
                f"{filename} differs from the pinned contract revision {entry['commit'][:12]}; "
                "run `python -m hisim.renovisor.contract.refresh <checkout>` instead of editing the copy"
            )
            assert len(entry["commit"]) == 40, f"{filename}: pin does not record a full commit hash"

    def test_every_vendored_file_is_pinned(self) -> None:
        """No contract file lies beside PINNED.yaml without being recorded in it."""
        pinned = set(ContractFiles.pinned()["files"])
        present = {p.name for p in ContractFiles.DIRECTORY.glob("*.yaml")} - {ContractFiles.PINNED_FILENAME}
        assert present == pinned, f"unpinned or missing contract files: {present ^ pinned}"

    def test_openapi_has_the_schemas_the_translator_reads(self) -> None:
        """The OpenAPI document carries the two request schemas the translation layer validates."""
        schemas = ContractFiles.openapi()["components"]["schemas"]
        for name in ("HomeInventoryInput", "PackageDefinition", "Measure"):
            assert name in schemas, f"openapi.yaml lacks components.schemas.{name}"

    def test_measures_and_materials_parse_to_their_lists(self) -> None:
        """The catalogue and the materials dump parse and expose their top-level lists."""
        assert ContractFiles.measures()["measures"], "measures.yaml has no measures"
        assert ContractFiles.materials()["materials"], "materials.yaml has no materials"
