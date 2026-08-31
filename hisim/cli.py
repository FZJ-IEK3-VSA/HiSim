"""The ``hisim`` command line: inspecting what can be configured, and running what is.

A declarative format is only as good as what an author can find out about it without reading
source code. Three questions come up constantly — *what can this class be configured with*,
*where will this file's numbers come from*, and *what does an editor need to check my file* —
and all three are answered from declarations HiSim already carries, so all three are answered
here rather than in a wiki page that goes stale.

The verbs are grouped under the noun they act on. ``hisim energy-system describe`` prints one
configuration class in full: its fields, its named presets and what each leaves to be sized, its
named constructors and their parameters, how each sizable field is computed and from which facts,
and which facts it contributes to the rest of a system. ``hisim energy-system facts`` takes a
whole file and prints the resolution table — every fact somebody provides, every fact somebody
reads, and which provider each read resolved to — without running a single timestep.
``hisim energy-system schema`` writes the JSON Schema an editor binds to. ``hisim energy-system
record`` goes the other way and writes a Python setup out as such a file, which is how the setups
this repository already has become declarative twins without anybody retyping them. And ``hisim
energy-system run`` runs a file, which is the same thing ``hisim_main.py`` does when handed one.

Two conventions hold throughout. Nothing here decides anything: every command asks the same code
the executor asks, so a command can never report something a run would contradict. And a failure
of the format is reported as the message the executor would print, on the standard error stream,
with the exit code that says a file was rejected rather than that the command was misused.
"""

# clean

from __future__ import annotations

import argparse
import dataclasses
import importlib
import sys
from pathlib import Path
from typing import Optional, Sequence, TextIO

from hisim.cli_exit import ExitCodes
from hisim.cli_grouping import GroupingCommands, GroupingPaths
from hisim.config.introspection import describe_config
from hisim.cli_render import DescriptionRenderer, FactsRenderer
from hisim.energy_system.errors import EnergySystemError
from hisim.energy_system.executor import run_energy_system
from hisim.energy_system.executor import SimulationParametersReader
from hisim.energy_system.recording.session import RecordingSession, record_setup
from hisim.energy_system.schema_classes import ComponentClassScan
from hisim.energy_system.schema_export import default_schema_path, export_schema


class ClassLookup:
    """Resolving the dotted path a caller types into the configuration class to describe.

    Both spellings are accepted, because both are things a caller has in front of them: the
    component class, which an energy-system file writes under ``class``, and the configuration
    class itself, which appears in a traceback or in a source file. A component path is followed
    to its configuration through the same constructor annotation the validator reads.
    """

    @classmethod
    def resolve(cls, path: str) -> type:
        """Imports one dotted path and returns the configuration class it stands for.

        Args:
            path: ``<module>.<ClassName>`` naming either a component or a configuration class.

        Returns:
            The configuration dataclass.

        Raises:
            ValueError: If the path has no module part, cannot be imported, names nothing, or
                names something that is neither a configuration dataclass nor a component with
                one.
        """
        if "." not in path:
            raise ValueError(f"'{path}' is not a dotted path; write '<module>.<ClassName>'.")
        module_path, class_name = path.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
        except ImportError as error:
            raise ValueError(f"the module '{module_path}' cannot be imported: {error}.") from error
        found = getattr(module, class_name, None)
        if not isinstance(found, type):
            raise ValueError(f"the module '{module_path}' has no class '{class_name}'.")
        if dataclasses.is_dataclass(found):
            return found
        config_class = ComponentClassScan.config_class_of(found)
        if config_class is None:
            raise ValueError(
                f"'{path}' is neither a configuration dataclass nor a component that annotates "
                "its constructor's 'config' parameter with one."
            )
        return config_class


class EnergySystemCommands:
    """The six verbs of the ``energy-system`` noun, one method each.

    Each method takes the parsed arguments and the two streams, does one thing and returns an
    exit code. Keeping them free of argument parsing is what lets a test drive them the way a
    user does — through :func:`main` with a list of words — while keeping each verb's own logic
    readable on its own.
    """

    @classmethod
    def describe(cls, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Prints everything declared about one configuration class."""
        try:
            config_class = ClassLookup.resolve(arguments.class_path)
        except ValueError as error:
            print(str(error), file=error_stream)
            return ExitCodes.USAGE
        DescriptionRenderer.render(describe_config(config_class), out)
        return ExitCodes.OK

    @classmethod
    def facts(cls, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Prints the sizing resolution of one energy-system file without running it."""
        del error_stream  # every command shares one signature; this one reports nothing separately
        return FactsRenderer.render(Path(arguments.energy_system), out)

    @classmethod
    def schema(cls, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Writes the JSON Schema of the format, either to the committed file or to a given path."""
        del error_stream  # every command shares one signature; this one reports nothing separately
        print(f"Wrote the schema of the energy-system format to {export_schema(arguments.out)}.", file=out)
        return ExitCodes.OK

    @classmethod
    def record(cls, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Records one Python setup as an energy-system file and proves the file builds again.

        The verb that turns the existing Python setups into declarative twins: it runs the setup,
        observes what it built and writes the file that describes it, then loads that file back
        through the executor. A recorded file that does not build is reported as a failure of the
        recording rather than written and left for somebody to discover.

        With ``--grouping`` it runs the second pass of the grouping workflow instead: every probe of
        the setup's probe list is recorded, the grouped file is built from the decisions that file
        carries, and every probe column is checked against it byte for byte.
        """
        if arguments.grouping:
            return GroupingCommands.record(arguments, out, error_stream)
        del error_stream  # every command shares one signature; a refusal propagates to main()
        module = Path(arguments.setup)
        parameters_path = Path(arguments.simulation_parameters)
        directory = Path(arguments.out) if arguments.out else RecordingSession.default_output_directory(module)
        parameters = SimulationParametersReader.read(parameters_path)
        result = record_setup(
            module,
            parameters,
            directory,
            parameters_path=parameters_path,
            module_config=Path(arguments.module_config) if arguments.module_config else None,
            probe=arguments.probe,
            probes=arguments.probes,
        )
        origin = "wrote" if result.parameters.written else "referenced"
        print(
            f"Recorded {result.setup} as {result.path} "
            f"({len(result.model.components)} components); {origin} {result.parameters.path}.",
            file=out,
        )
        return ExitCodes.OK

    @classmethod
    def grouping(cls, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Dispatches the two grouping verbs, which are a workflow rather than a single command."""
        actions = {"probe": GroupingCommands.probe, "import": GroupingCommands.import_workbook}
        action = actions.get(getattr(arguments, "action", None) or "")
        if action is None:
            print("Usage: hisim energy-system grouping {probe,import} ...", file=error_stream)
            return ExitCodes.USAGE
        return action(arguments, out, error_stream)

    @classmethod
    def run(cls, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Runs one energy-system file over one simulation period."""
        del error_stream  # every command shares one signature; failures propagate as exceptions
        built = run_energy_system(
            arguments.energy_system,
            arguments.simulation_parameters,
            result_directory=arguments.result_dir,
            rerun=arguments.rerun,
        )
        directory = built.simulator.get_simulation_parameters().result_directory
        print(f"Results of '{built.model.name}' are in {directory}.", file=out)
        return ExitCodes.OK


def build_parser() -> argparse.ArgumentParser:
    """Builds the whole argument parser, nouns and verbs included.

    Returns:
        The parser; calling it with no arguments at all prints the help and is treated as a usage
        error by :func:`main`, because a bare ``hisim`` asked for nothing.
    """
    parser = argparse.ArgumentParser(
        prog="hisim",
        description="ETHOS.HiSim — inspect and run declarative energy systems.",
    )
    nouns = parser.add_subparsers(dest="noun")
    energy_system = nouns.add_parser(
        "energy-system", help="inspect and run *.energy_system.yaml files"
    )
    verbs = energy_system.add_subparsers(dest="verb")

    describe = verbs.add_parser("describe", help="print what one class can be configured with")
    describe.add_argument("class_path", metavar="CLASS", help="dotted path of a component or config class")

    facts = verbs.add_parser("facts", help="print where a file's sized values would come from")
    facts.add_argument("energy_system", metavar="ENERGY_SYSTEM", help="the *.energy_system.yaml file")

    schema = verbs.add_parser("schema", help="write the JSON Schema an editor binds to")
    schema.add_argument(
        "--out", default=None, help=f"where to write it (default: {default_schema_path()})"
    )

    record = verbs.add_parser("record", help="write a Python setup out as an energy-system file")
    record.add_argument("setup", metavar="SETUP", help="the system_setups/*.py module to record")
    record.add_argument(
        "simulation_parameters",
        metavar="SIMULATION",
        help="the *.simulation.yaml or *.simulation.json file the setup is run with",
    )
    record.add_argument(
        "--out",
        default=None,
        help=f"where the recorded file goes (default: the repository's {RecordingSession.DEFAULT_OUTPUT_DIRECTORY}/)",
    )
    record.add_argument(
        "--grouping",
        default=None,
        help="a *.grouping.yaml decision; records every probe of its list and writes the grouped file",
    )
    record.add_argument(
        "--module-config",
        dest="module_config",
        default=None,
        help="a module-configuration file to hand the setup (one probe of a grouping pass)",
    )
    record.add_argument(
        "--probe", default="", help="the probe column this recording fills, written into its name and header"
    )
    record.add_argument("--probes", default="", help="the probe list the column comes from")

    grouping = verbs.add_parser("grouping", help="prefill, and apply, the grouping table of one setup")
    actions = grouping.add_subparsers(dest="action")
    probe = actions.add_parser("probe", help="record every probe and write the workbook to fill in")
    probe.add_argument("setup", metavar="SETUP", help="the system_setups/*.py module to probe")
    probe.add_argument("probes", metavar="PROBES", help="the authored *.probes.yaml list")
    probe.add_argument("--out", default=None, help="where the workbook goes (it is never committed)")
    probe.add_argument(
        "--simulation",
        dest="simulation_parameters",
        default=None,
        help=f"the parameters each probe is recorded with (default: {GroupingPaths.DEFAULT_PARAMETERS})",
    )
    probe.add_argument(
        "--grouping", default=None, help="a decision to carry into the workbook (default: the committed one)"
    )
    importer = actions.add_parser("import", help="normalise a filled-in workbook into the committed file")
    importer.add_argument("workbook", metavar="WORKBOOK", help="the filled-in *.grouping.xlsx")
    importer.add_argument("--out", default=None, help="where the *.grouping.yaml goes")

    run = verbs.add_parser("run", help="run a file over a simulation period")
    run.add_argument("energy_system", metavar="ENERGY_SYSTEM", help="the *.energy_system.yaml file")
    run.add_argument(
        "simulation_parameters",
        metavar="SIMULATION",
        help="the *.simulation.yaml or *.simulation.json file",
    )
    run.add_argument("--result-dir", dest="result_dir", default=None, help="where the results go")
    run.add_argument("--rerun", action="store_true",
                     help="the file is a generated run record and is expected to reproduce it")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs one command line and returns the process exit code.

    The single entry point, installed as the ``hisim`` console script and called directly by the
    tests, which is why it returns a code rather than exiting: a test drives the real command with
    a list of words and reads what it wrote, without a subprocess and without catching
    ``SystemExit``.

    Args:
        argv: The arguments after the program name; the process arguments when omitted.

    Returns:
        Zero on success, two when the command line itself was wrong, and one when a file was
        rejected — the message having gone to the standard error stream.
    """
    parser = build_parser()
    try:
        arguments = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exit_request:  # argparse reports usage errors by exiting
        return int(exit_request.code or ExitCodes.OK)
    verbs = {
        "describe": EnergySystemCommands.describe,
        "facts": EnergySystemCommands.facts,
        "grouping": EnergySystemCommands.grouping,
        "record": EnergySystemCommands.record,
        "schema": EnergySystemCommands.schema,
        "run": EnergySystemCommands.run,
    }
    verb = verbs.get(getattr(arguments, "verb", None) or "")
    if arguments.noun != "energy-system" or verb is None:
        parser.print_help(sys.stderr)
        return ExitCodes.USAGE
    try:
        return verb(arguments, sys.stdout, sys.stderr)
    except EnergySystemError as error:
        print(str(error), file=sys.stderr)
        return ExitCodes.FILE_REJECTED


if __name__ == "__main__":  # pragma: no cover - the console script calls main() directly
    sys.exit(main())
