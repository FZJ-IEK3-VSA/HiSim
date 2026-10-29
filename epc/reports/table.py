"""The batch table: one row per run and building, one column per indicator key seen.

``epc_table.csv`` starts with ``run, building, country, engine, edition`` and then has every
indicator key of the batch in the order it was first seen, its unit in the header as ``key [unit]``,
and an empty cell where a result lacks the key (R7.2, design §5.5). The table knows the result
dictionary and no country: a key it has never heard of is just one more column.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

#: The file name of the table in the output directory.
FILENAME = "epc_table.csv"

#: The leading columns: ``run`` is the run path, the others come from the result's ``run`` block.
LEADING_COLUMNS: Tuple[str, ...] = ("run", "building", "country", "engine", "edition")


def write(rows: Sequence[Tuple[str, Mapping[str, Any]]], path: Path) -> Path:
    """Writes the table of a batch.

    Args:
        rows: Per computed building, its run path relative to the results root and its result
            dictionary, in the order of the batch.
        path: The file to write.

    Returns:
        The path written.
    """
    units: Dict[str, str] = {}
    for _, document in rows:
        for key, entry in document["indicators"].items():
            units.setdefault(key, entry["unit"])
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(list(LEADING_COLUMNS) + [f"{key} [{unit}]" for key, unit in units.items()])
        for run_path, document in rows:
            run = document["run"]
            indicators = document["indicators"]
            writer.writerow(
                [run_path, run["building"], run["country"], run["engine"], run["edition"]]
                + [_cell(indicators[key]["value"]) if key in indicators else "" for key in units]
            )
    return path


def _cell(value: Any) -> str:
    """One indicator value as a cell; a series is written space-separated."""
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value)
