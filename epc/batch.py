"""The three steps in order, for one run or for every run under a results root.

``run`` resolves the country's engine and loads its data before any record is opened, reads the
run's expanded record, computes every building and writes one JSON per building; it stops at the
first refusal and then writes nothing (D18). ``batch`` does the same for every directory under a
results root that holds an expanded record, in a fixed order (R12.3): a refused run is recorded and
the batch continues, and at the end it writes one table and one summary (R12.2). A batch writes
only into an output directory that is new or empty (``E-16``, D28).

The output mirrors the results tree: ``<out>/<run path>/<building>.epc.json``, the run path being
the run directory relative to the results root in a batch and the run directory's own name for a
single run.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from epc import __version__
from epc.engines import registry
from epc.engines.base import EpcEngine
from epc.inputs import record as record_reader
from epc.model.errors import EpcError, EpcErrorKind
from epc.model.result import RunInfo
from epc.reports import json_report, summary, table

#: The ``building`` column of a refusal that concerns the whole record.
WHOLE_RECORD = "—"


@dataclass(frozen=True)
class BatchOutcome:
    """What a batch did.

    Attributes:
        found: The runs found under the results root.
        computed: The runs computed.
        refusals: The refused runs.
        written: Every file written, the table and the summary last.
    """

    found: int
    computed: int
    refusals: Tuple[summary.Refusal, ...]
    written: Tuple[Path, ...]


def engine_for(country: str) -> EpcEngine:
    """The country's engine with its data loaded, before any record is opened.

    Raises:
        EpcError: ``E-01`` for a country without an engine, ``E-11``/``E-12`` for its data.
    """
    engine = registry.for_country(country)
    engine.load_data()
    return engine


def compute_run(results_dir: Path, engine: EpcEngine, computed_at: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Reads one run's expanded record and computes every building in it; writes nothing.

    Args:
        results_dir: The run's results directory.
        engine: The engine, its data loaded.
        computed_at: The time stated in every result.

    Returns:
        Per building, its name and its result dictionary, in the record's order.

    Raises:
        EpcError: The first refusal of the run; ``building`` names the building it concerns, if one.
    """
    record = record_reader.load(results_dir)
    documents: List[Tuple[str, Dict[str, Any]]] = []
    for entry in record.buildings:
        try:
            result = engine.compute(record_reader.describe(entry))
            result.run = RunInfo(
                record=record.path.as_posix(),
                building=entry.name,
                country=engine.country,
                engine=engine.country.lower(),
                edition=engine.edition,
                climate_basis=engine.climate_basis,
                calculator=f"epc {__version__}",
                computed_at=computed_at,
            )
            documents.append((entry.name, result.to_dict()))
        except EpcError as error:
            error.building = entry.name
            raise
    return documents


def run(results_dir: Path, country: str, out: Path) -> List[Path]:
    """``python -m epc run``: one run, stopping at the first refusal.

    Args:
        results_dir: The run's results directory.
        country: ISO 3166-1 alpha-2 code of the engine.
        out: The output directory.

    Returns:
        The files written, one per building.

    Raises:
        EpcError: The first refusal; nothing is written then.
    """
    engine = engine_for(country)
    documents = compute_run(results_dir, engine, _now().isoformat(timespec="seconds"))
    run_path = out / results_dir.resolve().name
    return [json_report.write(document, run_path / f"{name}{json_report.SUFFIX}") for name, document in documents]


def batch(root: Path, country: str, out: Path) -> BatchOutcome:
    """``python -m epc batch``: every run under a results root, recording refusals and continuing.

    Args:
        root: The results root.
        country: ISO 3166-1 alpha-2 code of the engine.
        out: The output directory, new or empty.

    Returns:
        What the batch found, computed, refused and wrote.

    Raises:
        EpcError: ``E-01``, ``E-11``, ``E-12`` for the engine, ``E-16`` for a non-empty output
            directory, ``E-15`` for a root without any expanded record.
    """
    engine = engine_for(country)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise EpcError(EpcErrorKind.OUTPUT_DIRECTORY_NOT_EMPTY, dir=out)
    run_dirs = _run_dirs(root)
    if not run_dirs:
        raise EpcError(EpcErrorKind.NO_RECORD_UNDER_ROOT, root=root)
    now = _now()
    rows: List[Tuple[str, Dict[str, Any]]] = []
    refusals: List[summary.Refusal] = []
    written: List[Path] = []
    for run_dir in run_dirs:
        run_path = run_dir.relative_to(root).as_posix()
        try:
            documents = compute_run(run_dir, engine, now.isoformat(timespec="seconds"))
        except EpcError as error:
            refusals.append(summary.Refusal(run_path, error.building or WHOLE_RECORD, error.error_id, error.message))
            continue
        for name, document in documents:
            written.append(json_report.write(document, out / run_path / f"{name}{json_report.SUFFIX}"))
            rows.append((run_path, document))
    out.mkdir(parents=True, exist_ok=True)
    written.append(table.write(rows, out / table.FILENAME))
    computed = len(run_dirs) - len(refusals)
    written.append(summary.write(now.date().isoformat(), len(run_dirs), computed, refusals, out / summary.FILENAME))
    return BatchOutcome(len(run_dirs), computed, tuple(refusals), tuple(written))


def _run_dirs(root: Path) -> List[Path]:
    """Every directory under the root, the root included, that holds an expanded record, by run path."""
    if not root.is_dir():
        return []
    records = (path for path in root.rglob(record_reader.RECORD_FILENAME) if path.is_file())
    return sorted((path.parent for path in records), key=lambda run_dir: run_dir.relative_to(root).as_posix())


def _now() -> datetime.datetime:
    """The local time, to the second."""
    return datetime.datetime.now().replace(microsecond=0)
