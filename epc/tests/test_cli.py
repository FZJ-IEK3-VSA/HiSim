"""T-9: ``python -m epc`` in a subprocess over a fixture results tree: outputs, refusals, exit codes.

A country without engine runs through ``python -m epc`` itself. The runs that need an engine use a
driver that registers the dummy engines of :mod:`epc.tests.support` in the child process and then
starts the package exactly as ``-m`` does (``runpy.run_module("epc", run_name="__main__")``), so no
engine is ever registered in the package itself.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

from epc.inputs.record import RECORD_FILENAME
from epc.tests import support

pytestmark = pytest.mark.base

#: Registers the dummy engines, then runs the package as ``python -m epc`` would.
DRIVER = (
    "import runpy; "
    "from epc.engines import registry; "
    "from epc.tests import support; "
    "registry.register(support.DummyEngine); "
    "registry.register(support.SiteEngine); "
    "runpy.run_module('epc', run_name='__main__', alter_sys=True)"
)


def _epc(*arguments: str, dummy: bool = True) -> subprocess.CompletedProcess:
    """Runs the command line from the repository root; with ``dummy`` through the driver."""
    command: List[str] = [sys.executable, "-c", DRIVER] if dummy else [sys.executable, "-m", "epc"]
    return subprocess.run(
        command + list(arguments),
        cwd=support.REPOSITORY_ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def _results_tree(root: Path, refused: bool = True) -> Path:
    """A results root with both fixtures as runs and, optionally, a run whose wall has factor 0.7."""
    for name in support.FIXTURE_NAMES:
        (root / "results" / name).mkdir(parents=True)
        shutil.copy(support.FIXTURES / name / RECORD_FILENAME, root / "results" / name / RECORD_FILENAME)
    if refused:
        document = support.fixture_document(support.CONFIGURED)
        support.first_building(document)["elements"]["wall"]["adjustment_factor"] = 0.7
        support.write_record(root / "results" / "refused", document)
    return root


def test_a_country_without_engine_is_refused_and_nothing_written(tmp_path: Path) -> None:
    """``--country DE`` exits 2 with E-01 for run and batch; no output directory appears (AC3)."""
    for command in ("run", "batch"):
        completed = _epc(command, str(support.FIXTURES), "--country", "DE", "--out", str(tmp_path / "out"), dummy=False)
        assert completed.returncode == 2
        assert "E-01" in completed.stderr and "'DE'" in completed.stderr
    assert not (tmp_path / "out").exists()


def test_countries_lists_the_engines() -> None:
    """``countries`` exits 0 and names every registered engine with its edition."""
    completed = _epc("countries")
    assert completed.returncode == 0
    assert "ZZ\tdummy edition\tnational" in completed.stdout
    assert _epc("countries", dummy=False).returncode == 0


def test_run_writes_one_json_per_building(tmp_path: Path) -> None:
    """``run`` exits 0 and writes ``<out>/<run directory name>/<building>.epc.json`` (AC1 shape)."""
    completed = _epc("run", str(support.FIXTURES / support.CONFIGURED), "--country", "ZZ", "--out", str(tmp_path))
    assert completed.returncode == 0, completed.stderr
    document = json.loads((tmp_path / support.CONFIGURED / "building.epc.json").read_text(encoding="utf-8"))
    assert document["run"]["country"] == "ZZ"
    assert document["provenance"]["hisim_building_code"] == (
        support.first_building(support.fixture_document(support.CONFIGURED))["hisim_building_code"]
    )


def test_run_stops_at_a_refusal_and_writes_nothing(tmp_path: Path) -> None:
    """A refused record exits 2 with its id, and the output directory stays absent."""
    results = _results_tree(tmp_path / "root") / "results" / "refused"
    completed = _epc("run", str(results), "--country", "ZZ", "--out", str(tmp_path / "out"))
    assert completed.returncode == 2
    assert "E-06" in completed.stderr
    assert not (tmp_path / "out").exists()


def test_an_engine_refuses_a_site_it_needs(tmp_path: Path) -> None:
    """An engine reading a site field the record leaves null refuses with E-09 naming it (AC4 shape)."""
    completed = _epc("run", str(support.FIXTURES / support.CONFIGURED), "--country", "ZY", "--out", str(tmp_path))
    assert completed.returncode == 2
    assert "E-09" in completed.stderr and "'climate_region'" in completed.stderr


def test_batch_records_a_refusal_and_continues(tmp_path: Path) -> None:
    """One refused run: the others' JSON, the table and the summary are written; exit 1 (AC9, I-11)."""
    root = _results_tree(tmp_path / "root")
    out = tmp_path / "out"
    completed = _epc("batch", str(root), "--country", "ZZ", "--out", str(out))
    assert completed.returncode == 1, completed.stderr
    for name in support.FIXTURE_NAMES:
        assert (out / "results" / name / "building.epc.json").is_file()
    assert not (out / "results" / "refused").exists()
    with (out / "epc_table.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    assert [row[0] for row in rows[1:]] == [f"results/{name}" for name in sorted(support.FIXTURE_NAMES)]
    text = (out / "epc_summary.md").read_text(encoding="utf-8")
    assert "runs found: 3 · computed: 2 · refused: 1" in text
    assert "| results/refused | building | E-06 |" in text


def test_batch_without_refusal_exits_0(tmp_path: Path) -> None:
    """Every run computed: exit 0."""
    root = _results_tree(tmp_path / "root", refused=False)
    completed = _epc("batch", str(root), "--country", "ZZ", "--out", str(tmp_path / "out"))
    assert completed.returncode == 0, completed.stderr
    assert "refused: 0" in completed.stdout


def test_batch_refuses_a_non_empty_output_directory(tmp_path: Path) -> None:
    """A non-empty output directory exits 2 with E-16 and is left as it was (D28)."""
    root = _results_tree(tmp_path / "root")
    out = tmp_path / "out"
    out.mkdir()
    (out / "earlier.txt").write_text("earlier", encoding="utf-8")
    completed = _epc("batch", str(root), "--country", "ZZ", "--out", str(out))
    assert completed.returncode == 2
    assert "E-16" in completed.stderr
    assert [path.name for path in out.iterdir()] == ["earlier.txt"]


def test_batch_refuses_a_root_without_records(tmp_path: Path) -> None:
    """A root without any expanded record exits 2 with E-15."""
    completed = _epc("batch", str(tmp_path), "--country", "ZZ", "--out", str(tmp_path / "out"))
    assert completed.returncode == 2
    assert "E-15" in completed.stderr
