"""The command-line entry point of the RenoVisor translation layer.

The intended interface is files in, files out, and nothing else: a container invocation receives a
home inventory, a package and the calculation parameters as files, runs one simulation, and writes
its results, its translation report and, on failure, its errors into an output directory. Nothing
is posted anywhere — the service that started the container collects the files.

Intended shape, once step 5 lands::

    python -m hisim.renovisor calculate \
        --inventory inventory.json \
        --package package.json \
        --parameters parameters.json \
        --output-directory ./out

    out/
      results/                          the simulation's own output files
      parametrised_energy_system.yaml   the base file after the overrides, written before timestep 0
      translation_report.json           one line per inventory field and per measure (requirement R7)
      errors.json                       reason codes and field paths, when the request was refused

Exit codes are to be: 0 success, 2 validation failure, 3 refusal, 4 simulation failure.

The command is a stub today. Everything it needs below the translation layer — the bindings, the
parametriser and the sizing laws — belongs to steps 4 and 5, so running it exits 2 with a message
saying so rather than half-doing the job.
"""

import argparse
import sys
from typing import Any, ClassVar, List, Optional


class CalculateCommand:
    """The ``calculate`` subcommand: one inventory plus one package in, one result directory out.

    Kept as a class so the argument names are stated once and step 5 can fill in :meth:`run`
    without changing how the command is spelled.
    """

    #: The subcommand's name on the command line.
    NAME: ClassVar[str] = "calculate"

    #: What the command will do, printed by ``--help``.
    HELP: ClassVar[str] = "translate one inventory and package into a simulation and write its results"

    #: What it prints until step 5 implements it.
    NOT_IMPLEMENTED_MESSAGE: ClassVar[str] = (
        "hisim.renovisor calculate: not implemented until step 5 (bindings, parametriser and the "
        "sizing laws). The pure translation layer is importable today: see "
        "hisim.renovisor.application.PackageApplication."
    )

    #: The exit code a caller gets meanwhile: the code a validation failure uses, because nothing
    #: was calculated.
    NOT_IMPLEMENTED_EXIT_CODE: ClassVar[int] = 2

    @classmethod
    def add_to(cls, subparsers: Any) -> None:
        """Declare the subcommand and its arguments on an argument parser.

        Args:
            subparsers: The subparser collection of the top-level parser, as
                ``ArgumentParser.add_subparsers`` returns it.
        """
        parser = subparsers.add_parser(cls.NAME, help=cls.HELP)
        parser.add_argument("--inventory", required=True, help="path of the home inventory JSON file")
        parser.add_argument("--package", required=True, help="path of the renovation package JSON file")
        parser.add_argument("--parameters", required=True, help="path of the calculation parameters JSON file")
        parser.add_argument("--output-directory", required=True, help="directory to write every output file into")
        parser.set_defaults(handler=cls.run)

    @classmethod
    def run(cls, arguments: argparse.Namespace) -> int:
        """Run the command.

        Args:
            arguments: The parsed command line.

        Returns:
            :attr:`NOT_IMPLEMENTED_EXIT_CODE` until step 5 implements the calculation.
        """
        del arguments
        print(cls.NOT_IMPLEMENTED_MESSAGE, file=sys.stderr)
        return cls.NOT_IMPLEMENTED_EXIT_CODE


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
