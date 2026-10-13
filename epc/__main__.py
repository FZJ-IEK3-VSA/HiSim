"""The command line: ``python -m epc run | batch | countries``, run from the repository root.

Exit codes (design §5.4): ``run`` exits 0, or 2 on a refusal, which is printed with its id and
leaves nothing written. ``batch`` exits 0 when every run was computed, 1 when some run was refused
(the summary lists it), and 2 when the country, the engine's data, the results root or the output
directory is unusable. ``countries`` lists the implemented engines with whether their tables load
(D42) and exits 0. Argument errors exit 2, as argparse does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from epc import batch
from epc.engines import registry
from epc.model.errors import EpcError

#: The default output directory of local runs, git-ignored.
DEFAULT_OUT = Path(__file__).resolve().parent / "results"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs one command.

    Args:
        argv: The arguments after the program name; ``None`` reads ``sys.argv``.

    Returns:
        The exit code.
    """
    arguments = _parser().parse_args(argv)
    if arguments.command == "countries":
        if not registry.ENGINES_BY_COUNTRY:
            print("No EPC engine is implemented yet.")
        for code, engine in sorted(registry.ENGINES_BY_COUNTRY.items()):
            print(f"{code}\t{engine.edition}\t{engine.climate_basis}\t{_table_status(code)}")
        return 0
    try:
        if arguments.command == "run":
            for path in batch.run(arguments.results_dir, arguments.country, arguments.out):
                print(path)
            return 0
        outcome = batch.batch(arguments.results_root, arguments.country, arguments.out)
    except EpcError as error:
        print(error, file=sys.stderr)
        return 2
    print(f"runs found: {outcome.found}, computed: {outcome.computed}, refused: {len(outcome.refusals)}")
    return 1 if outcome.refusals else 0


def _table_status(country: str) -> str:
    """``ready`` when the country's engine loads its tables, else the error id and where to look (D42)."""
    try:
        batch.engine_for(country)
    except EpcError as error:
        return f"tables not usable ({error.error_id}): see epc/data/README.md"
    return "ready"


def _parser() -> argparse.ArgumentParser:
    """The argument parser of the three commands."""
    parser = argparse.ArgumentParser(
        prog="python -m epc", description="EPC heating demand of the buildings HiSim computed with."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for command, folder, text in (
        ("run", "results_dir", "one run: the results directory holding building.expanded.yaml"),
        ("batch", "results_root", "every run under a results root; the output directory must be new or empty"),
    ):
        subparser = commands.add_parser(command, help=text)
        subparser.add_argument(folder, type=Path)
        subparser.add_argument("--country", required=True, help="ISO 3166-1 alpha-2 code, e.g. AT")
        subparser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output directory, default {DEFAULT_OUT}")
    commands.add_parser("countries", help="the implemented engines, their editions and whether their tables load")
    return parser


if __name__ == "__main__":
    sys.exit(main())
