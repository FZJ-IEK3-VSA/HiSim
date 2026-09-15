"""The command-line entry point of the RenoVisor translation layer.

The interface is files in, files out, and nothing else: a container invocation receives a home
inventory, a renovation package and the calculation parameters as files in one directory, runs one
simulation, and writes its results, its translation report and, on failure, its errors into
another. Nothing is posted anywhere -- the service that started the container collects the files::

    python -m hisim.renovisor calculate INPUT_DIR OUTPUT_DIR [--cache-dir DIR] [--base-files DIR]

    INPUT_DIR/                         OUTPUT_DIR/
      home_inventory.json                parametrised.energy_system.yaml   the file that ran
      package.json                       realized.energy_system.yaml       what was built
      simulation.yaml                    realized.audit.yaml               where each number came from
                                         component_connections.json        every wire that was made
                                         translation_report.json           the fate of every field
                                         calculation.json                  the outcome
                                         errors.json                       only when it went wrong
                                         results/                          the simulation's output

Exit codes: 0 finished, 2 the request was malformed, 3 the request was well formed and cannot be
simulated, 4 the calculation failed after being accepted. ``--cache-dir`` is where the occupancy
and weather caches live, which is the one location outside the output directory a calculation
writes to (decision Q25); ``--base-files`` is where the recorded energy-system files live, and
defaults to this repository's ``energy_systems/``.

This module is a thin layer over :class:`hisim.renovisor.calculate.CalculationRunner`: it parses
the command line and returns an exit code, and everything that could fail happens inside the
runner, which turns failure into files rather than into a traceback on standard error.
"""

import argparse
import sys
from pathlib import Path
from typing import Any, ClassVar, List, Optional

from hisim.renovisor.calculate import CalculationRunner, ExitCode


class CalculateCommand:
    """The ``calculate`` subcommand: one input directory in, one output directory out.

    Kept as a class so that the argument names are stated once and the help text lives beside the
    runner's own description of what each location is for.
    """

    #: The subcommand's name on the command line.
    NAME: ClassVar[str] = "calculate"

    #: What the command does, printed by ``--help``.
    HELP: ClassVar[str] = "translate one inventory and package into a simulation and write its results"

    @classmethod
    def add_to(cls, subparsers: Any) -> None:
        """Declare the subcommand and its arguments on an argument parser.

        Args:
            subparsers: The subparser collection of the top-level parser, as
                ``ArgumentParser.add_subparsers`` returns it.
        """
        parser = subparsers.add_parser(cls.NAME, help=cls.HELP)
        parser.add_argument(
            "input_directory",
            metavar="INPUT_DIR",
            help="directory holding home_inventory.json, package.json and simulation.yaml",
        )
        parser.add_argument(
            "output_directory",
            metavar="OUTPUT_DIR",
            help="directory to write every output file into; nothing is written anywhere else",
        )
        parser.add_argument(
            "--cache-dir",
            default=None,
            help="directory for the occupancy and weather caches, shared between calculations",
        )
        parser.add_argument(
            "--base-files",
            default=None,
            help="directory holding the recorded energy-system files (default: energy_systems/)",
        )
        parser.set_defaults(handler=cls.run)

    @classmethod
    def run(cls, arguments: argparse.Namespace) -> int:
        """Run one calculation.

        Args:
            arguments: The parsed command line.

        Returns:
            The exit code of the outcome: 0 finished, 2 invalid, 3 refused, 4 failed.
        """
        exit_code: ExitCode = CalculationRunner(
            input_directory=Path(arguments.input_directory),
            output_directory=Path(arguments.output_directory),
            cache_directory=Path(arguments.cache_dir) if arguments.cache_dir else None,
            base_files_directory=Path(arguments.base_files) if arguments.base_files else None,
        ).run()
        return int(exit_code)


class RenovisorCommandLine:
    """The top-level command line of the translation layer, one subcommand deep."""

    #: The program name ``--help`` prints.
    PROGRAM: ClassVar[str] = "python -m hisim.renovisor"

    #: The one-line description ``--help`` prints under it.
    DESCRIPTION: ClassVar[str] = "Translate a RenoVisor request into a HiSim simulation."

    @classmethod
    def parser(cls) -> argparse.ArgumentParser:
        """Return the argument parser with every subcommand declared on it."""
        parser = argparse.ArgumentParser(prog=cls.PROGRAM, description=cls.DESCRIPTION)
        subparsers = parser.add_subparsers(dest="command", required=True)
        CalculateCommand.add_to(subparsers)
        return parser

    @classmethod
    def main(cls, argv: Optional[List[str]] = None) -> int:
        """Parse the command line and run the chosen subcommand.

        Args:
            argv: The arguments, without the program name; defaults to ``sys.argv[1:]``.

        Returns:
            The subcommand's exit code.
        """
        arguments = cls.parser().parse_args(sys.argv[1:] if argv is None else argv)
        exit_code: int = arguments.handler(arguments)
        return exit_code


if __name__ == "__main__":
    sys.exit(RenovisorCommandLine.main())
