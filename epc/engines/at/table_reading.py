"""Reading one CSV table of the data contract, refusing what does not meet it.

Every Austrian table is a CSV file under ``epc/data/at/`` with ``#`` lines for its citation, one header
line and one row per value (``epc/data/README.md``). The helpers here read such a file and its cells and
refuse, naming the file and the line: a missing file with ``E-11``, a wrong row count with ``E-12``,
anything else malformed (a header, a cell, a second row for one key) with ``E-19``. They know the shape
of a table, never a value of one.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from epc.model.errors import EpcError, EpcErrorKind

#: The country the tables belong to, as the messages name it.
COUNTRY = "AT"

#: Months 1 (January) … 12 (December), the order of every monthly series.
MONTHS: Tuple[int, ...] = tuple(range(1, 13))

#: One monthly series, January to December.
Monthly = Tuple[float, ...]

#: One table row: its line number in the file and its cells by column.
Row = Tuple[int, Dict[str, str]]


def malformed(file: str, line: int, problem: str) -> EpcError:
    """The ``E-19`` refusal of a malformed table, to be raised by the caller."""
    return EpcError(EpcErrorKind.TABLE_MALFORMED, file=file, line=line, problem=problem)


def read_rows(folder: Path, file: str, columns: Sequence[str]) -> List[Row]:
    """Reads a table without its ``#`` lines and checks its header and the width of every row.

    Args:
        folder: The country's data folder.
        file: The file name.
        columns: The columns the contract prescribes, in order.

    Returns:
        Every non-empty row with its line number.

    Raises:
        EpcError: ``E-11`` for a missing file, ``E-19`` for another header or a row of another width.
    """
    path = folder / file
    if not path.is_file():
        raise EpcError(EpcErrorKind.TABLE_FILE_MISSING, country=COUNTRY, folder=folder, file=file)
    with path.open(encoding="utf-8", newline="") as stream:
        numbered = [(index, line) for index, line in enumerate(stream, start=1) if not line.startswith("#")]
    parsed = list(csv.reader(line for _, line in numbered))
    if not parsed or parsed[0] != list(columns):
        line = numbered[0][0] if numbered else 1
        raise malformed(file, line, f"expected the columns {list(columns)}, found {parsed[0] if parsed else []}")
    rows: List[Row] = []
    for (line_number, _), values in zip(numbered[1:], parsed[1:]):
        if not values:
            continue
        if len(values) != len(columns):
            raise malformed(file, line_number, f"expected {len(columns)} values, found {len(values)}")
        rows.append((line_number, dict(zip(columns, values))))
    return rows


def number(file: str, row: Row, column: str) -> float:
    """A finite number from a cell; ``E-19`` otherwise."""
    text = row[1][column]
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise malformed(file, row[0], f"{column} must be a finite number, found '{text}'")
    return value


def optional_number(file: str, row: Row, column: str) -> Optional[float]:
    """A finite number from a cell, or ``None`` when the cell is empty."""
    return None if row[1][column] == "" else number(file, row, column)


def member(file: str, row: Row, column: str, allowed: Sequence[float]) -> float:
    """A number from a cell that must be one of the grid values ``allowed``; ``E-19`` otherwise."""
    value = number(file, row, column)
    if value not in allowed:
        raise malformed(file, row[0], f"{column} {row[1][column]} is not one of {list(allowed)}")
    return value


def month(file: str, row: Row) -> int:
    """The month 1 … 12 of a row."""
    return int(member(file, row, "month", MONTHS))


def require_count(file: str, dimension: str, expected: int, found: int) -> None:
    """Refuses a table, or one dimension of it, with another number of rows (``E-12``)."""
    if found != expected:
        raise EpcError(EpcErrorKind.TABLE_INCOMPLETE, file=file, n=expected, dimension=dimension, m=found)


def store(file: str, row: Row, table: Dict[Any, Any], key: Any, value: Any) -> None:
    """Adds one value under its key; a second row for the same key is refused (``E-19``)."""
    if key in table:
        raise malformed(file, row[0], f"a second row for {key}")
    table[key] = value


def monthly(values_by_month: Mapping[int, float]) -> Monthly:
    """A monthly series from values keyed by month."""
    return tuple(values_by_month[each] for each in MONTHS)
