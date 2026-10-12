"""The batch summary: how many runs were found, computed and refused, and every refusal.

``epc_summary.md`` states the date, the counts and one table row per refused run with its building,
error id and message (R7.2, R12.2, design §5.5). The counts satisfy found = computed + refused
(I-11); a summary that would break it is refused instead of written.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

#: The file name of the summary in the output directory.
FILENAME = "epc_summary.md"


@dataclass(frozen=True)
class Refusal:
    """One refused run of a batch.

    Attributes:
        run: The run path relative to the results root.
        building: The building the refusal concerns, ``—`` for one of the whole record.
        error_id: The id of the refusal, ``E-nn``.
        message: Its message.
    """

    run: str
    building: str
    error_id: str
    message: str


def write(date: str, found: int, computed: int, refusals: Sequence[Refusal], path: Path) -> Path:
    """Writes the summary of a batch.

    Args:
        date: The date of the batch, ISO 8601.
        found: The runs found under the results root.
        computed: The runs computed.
        refusals: The refused runs, in the order of the batch.
        path: The file to write.

    Returns:
        The path written.

    Raises:
        ValueError: When the counts do not add up (I-11).
    """
    if found != computed + len(refusals):
        raise ValueError(f"{found} runs found, but {computed} computed and {len(refusals)} refused (I-11).")
    lines: List[str] = [
        f"# EPC batch summary — {date}",
        "",
        f"runs found: {found} · computed: {computed} · refused: {len(refusals)}",
    ]
    if refusals:
        lines += ["", "| run | building | error | message |", "|---|---|---|---|"]
        lines += [
            f"| {_cell(refusal.run)} | {_cell(refusal.building)} | {refusal.error_id} | {_cell(refusal.message)} |"
            for refusal in refusals
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _cell(text: str) -> str:
    """Text for one cell of a Markdown table, its pipes escaped."""
    return text.replace("|", "\\|")
