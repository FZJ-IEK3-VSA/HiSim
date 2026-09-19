"""The command-line entry point of the RenoVisor translation layer.

Four commands, files in and files out, and nothing posted anywhere -- the service that started
the container collects the files::

    python -m hisim.renovisor run          <request.{json,yaml}> --out DIR [--period ...]
    python -m hisim.renovisor translate    <request> --out DIR
    python -m hisim.renovisor validate     <request>
    python -m hisim.renovisor capabilities --out FILE [--measures measures.yaml]
    python -m hisim.renovisor map          [--out translation_map.html]

``run`` is what the backend calls. ``translate`` stops after the energy-system file and the
mapping report, which is what the verification harness and every probe use. ``validate`` prints
the problems JSON and exits without touching the disk. ``capabilities`` writes the document the
backend serves as ``GET /measures`` for this image. ``map`` regenerates the committed HTML page
that shows the whole translation at a glance.

There is no ``--variant``: the baseline is a request with ``measures: []``.

Exit codes are the translator's, not argparse's: ``0`` finished, ``2`` the request is not a
request, ``3`` the translator could not map something nobody listed, ``5`` HiSim refused the
file or the simulation raised. The last line on standard error for ``3`` and ``5`` is one line,
because the backend shows it as the job's error message.
"""

import argparse
import sys
from pathlib import Path
from typing import Any, ClassVar, List, Optional

from hisim.renovisor.run import Calculation
from hisim.renovisor.simulation import Period, PeriodNames


class RunCommand:
    """The ``run`` subcommand: one request in, one directory of results out."""

    #: The subcommand's name on the command line.
    NAME: ClassVar[str] = "run"

    #: What the command does, printed by ``--help``.
    HELP: ClassVar[str] = "validate, translate and simulate one calculation request"

    @classmethod
    def add_to(cls, subparsers: Any) -> None:
        """Declare the subcommand and its arguments on an argument parser."""
        parser = subparsers.add_parser(cls.NAME, help=cls.HELP)
        _add_request_argument(parser)
        _add_out_argument(parser, "directory to write every output file into")
        parser.add_argument(
            "--period",
            default=PeriodNames.DEFAULT,
            choices=list(PeriodNames.CHOICES),
            help="how long the simulation covers (default: %(default)s)",
        )
        _add_shared_arguments(parser)
        parser.set_defaults(handler=cls.run)

    @classmethod
    def run(cls, arguments: argparse.Namespace) -> int:
        """Run one calculation and return its exit code."""
        return int(_calculation(arguments).run())


class TranslateCommand:
    """The ``translate`` subcommand: the energy-system file and the report, without a simulation."""

    #: The subcommand's name on the command line.
    NAME: ClassVar[str] = "translate"

    #: What the command does, printed by ``--help``.
    HELP: ClassVar[str] = "write the energy-system file and the mapping report, and stop"

    @classmethod
    def add_to(cls, subparsers: Any) -> None:
        """Declare the subcommand and its arguments on an argument parser."""
        parser = subparsers.add_parser(cls.NAME, help=cls.HELP)
        _add_request_argument(parser)
        _add_out_argument(parser, "directory to write the file and the report into")
        _add_shared_arguments(parser)
        parser.set_defaults(handler=cls.run)

    @classmethod
    def run(cls, arguments: argparse.Namespace) -> int:
        """Translate one request and return the exit code."""
        return int(_calculation(arguments).translate_only())


class ValidateCommand:
    """The ``validate`` subcommand: say whether a request is one, and print every problem."""

    #: The subcommand's name on the command line.
    NAME: ClassVar[str] = "validate"

    #: What the command does, printed by ``--help``.
    HELP: ClassVar[str] = "print the problems of one calculation request as JSON"

    @classmethod
    def add_to(cls, subparsers: Any) -> None:
        """Declare the subcommand and its arguments on an argument parser."""
        parser = subparsers.add_parser(cls.NAME, help=cls.HELP)
        _add_request_argument(parser)
        parser.set_defaults(handler=cls.run)

    @classmethod
    def run(cls, arguments: argparse.Namespace) -> int:
        """Validate one request and return ``0`` or ``2``."""
        return int(
            Calculation(
                request_path=Path(arguments.request),
                output_directory=Path("."),
            ).validate()
        )


class CapabilitiesCommand:
    """The ``capabilities`` subcommand: the document the backend serves per image."""

    #: The subcommand's name on the command line.
    NAME: ClassVar[str] = "capabilities"

    #: What the command does, printed by ``--help``.
    HELP: ClassVar[str] = "write the capability document of this translator build"

    @classmethod
    def add_to(cls, subparsers: Any) -> None:
        """Declare the subcommand and its arguments on an argument parser."""
        parser = subparsers.add_parser(cls.NAME, help=cls.HELP)
        parser.add_argument("--out", required=True, metavar="FILE", help="where to write the JSON")
        parser.add_argument(
            "--measures",
            default=None,
            help="a measures.yaml to check the frozen table against (default: the vendored copy)",
        )
        parser.add_argument(
            "--generated-at",
            default=None,
            help="override the document's timestamp, so that a test can compare two runs",
        )
        parser.set_defaults(handler=cls.run)

    @classmethod
    def run(cls, arguments: argparse.Namespace) -> int:
        """Build the capability document and write it."""
        from hisim.renovisor.capabilities import CapabilityDocument

        document = CapabilityDocument.build(
            measures_path=Path(arguments.measures) if arguments.measures else None,
            generated_at=arguments.generated_at,
        )
        return int(document.write(Path(arguments.out)))


class MapCommand:
    """The ``map`` subcommand: regenerate the committed translation map."""

    #: The subcommand's name on the command line.
    NAME: ClassVar[str] = "map"

    #: What the command does, printed by ``--help``.
    HELP: ClassVar[str] = "regenerate roadmap/renovisor/translation_map.html"

    @classmethod
    def add_to(cls, subparsers: Any) -> None:
        """Declare the subcommand and its arguments on an argument parser."""
        parser = subparsers.add_parser(cls.NAME, help=cls.HELP)
        parser.add_argument(
            "--out",
            default=None,
            metavar="FILE",
            help="where to write the page (default: the committed roadmap/renovisor page)",
        )
        parser.set_defaults(handler=cls.run)

    @classmethod
    def run(cls, arguments: argparse.Namespace) -> int:
        """Render the map and write it."""
        from hisim.renovisor.map import TranslationMap

        return int(TranslationMap.write(Path(arguments.out) if arguments.out else None))


def _add_request_argument(parser: argparse.ArgumentParser) -> None:
    """Declare the positional request file every request-taking command has."""
    parser.add_argument("request", metavar="REQUEST", help="the calculation request, JSON or YAML")


def _add_out_argument(parser: argparse.ArgumentParser, help_text: str) -> None:
    """Declare the ``--out`` directory the writing commands share."""
    parser.add_argument("--out", required=True, metavar="DIR", help=help_text)


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    """Declare the two optional locations a container maps in."""
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


def _calculation(arguments: argparse.Namespace) -> Calculation:
    """Build the calculation one command line describes."""
    return Calculation(
        request_path=Path(arguments.request),
        output_directory=Path(arguments.out),
        period=Period(getattr(arguments, "period", PeriodNames.DEFAULT)),
        cache_directory=Path(arguments.cache_dir) if arguments.cache_dir else None,
        base_files_directory=Path(arguments.base_files) if arguments.base_files else None,
    )


class RenovisorCommandLine:
    """The top-level command line of the translation layer, one subcommand deep."""

    #: The program name ``--help`` prints.
    PROGRAM: ClassVar[str] = "python -m hisim.renovisor"

    #: The one-line description ``--help`` prints under it.
    DESCRIPTION: ClassVar[str] = "Translate a RenoVisor calculation request into a HiSim simulation."

    @classmethod
    def parser(cls) -> argparse.ArgumentParser:
        """Return the argument parser with every subcommand declared on it."""
        parser = argparse.ArgumentParser(prog=cls.PROGRAM, description=cls.DESCRIPTION)
        subparsers = parser.add_subparsers(dest="command", required=True)
        for command in (RunCommand, TranslateCommand, ValidateCommand, CapabilitiesCommand, MapCommand):
            command.add_to(subparsers)
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
    sys.exit(int(RenovisorCommandLine.main()))
