"""Validation of the Austrian engine on the licensed local data (marker ``epc_norm_data``).

V-5: the real OENORM tables load complete, cite their source and match the checksums recorded in
``SOURCES.md``. The tests run only where the tables exist under ``epc/data/at/`` and skip naming the
folder elsewhere. They read the tables but state none of their values (R8.5).
"""

from __future__ import annotations

import csv
import hashlib
import re
from collections import Counter
from pathlib import Path

import pytest

from epc.engines.at.data_tables import DEFAULT_DATA_FOLDER, TABLE_FILES, VALIDATION_EXAMPLES_FILE, load_tables

pytestmark = pytest.mark.epc_norm_data

ALL_FILES = TABLE_FILES + (VALIDATION_EXAMPLES_FILE,)


@pytest.fixture(name="data_folder")
def fixture_data_folder() -> Path:
    """The local table folder, or a skip that names it."""
    missing = [file for file in ALL_FILES + ("SOURCES.md",) if not (DEFAULT_DATA_FOLDER / file).is_file()]
    if missing:
        pytest.skip(f"the licensed OENORM tables are not under {DEFAULT_DATA_FOLDER} (missing: {', '.join(missing)})")
    return DEFAULT_DATA_FOLDER


def test_every_table_loads_complete(data_folder: Path) -> None:
    """V-5: the loader accepts every real table, complete grids, contiguous bands and classes (I-6)."""
    tables = load_tables(data_folder)
    assert len(tables.transposition_bands) == 7
    assert len(tables.usage_profiles) == 3
    assert set(tables.digests) == set(TABLE_FILES)


@pytest.mark.parametrize("file", ALL_FILES)
def test_every_header_cites_norm_edition_table_and_page(data_folder: Path, file: str) -> None:
    """V-5: the # lines name the OENORM, the 2024-03 edition, a table, annex or section and a page (R8.1, R11)."""
    with (data_folder / file).open(encoding="utf-8") as stream:
        header = "".join(line for line in stream if line.startswith("#"))
    assert "OENORM" in header
    assert "2024-03" in header
    assert re.search(r"Tabelle|Anhang|Abschnitt", header)
    assert "Seite" in header
    assert "2025" not in header


def test_every_file_matches_the_checksum_in_sources(data_folder: Path) -> None:
    """V-5: SOURCES.md records the SHA-256 of each file; a table changed since is noticed."""
    sources = (data_folder / "SOURCES.md").read_text(encoding="utf-8")
    for file in ALL_FILES:
        row = next(line for line in sources.splitlines() if line.startswith(f"| `{file}` |"))
        assert re.findall(r"`([0-9a-f]{64})`", row) == [hashlib.sha256((data_folder / file).read_bytes()).hexdigest()]


def test_the_validation_examples_have_the_contract_shape(data_folder: Path) -> None:
    """V-5: the columns of the contract; every result series has twelve months and the annual value."""
    with (data_folder / VALIDATION_EXAMPLES_FILE).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(line for line in stream if not line.startswith("#")))
    assert list(rows[0]) == ["example", "variant", "case", "month", "value"]
    results = [row for row in rows if not row["case"].startswith("input:")]
    assert set(Counter((row["example"], row["variant"], row["case"]) for row in results).values()) == {13}
    for row in results:
        assert row["month"] in {str(month) for month in range(1, 13)} | {"annual"}
        float(row["value"])
