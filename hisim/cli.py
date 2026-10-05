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
``hisim energy-system test-assemblies`` runs the test contract every assembly of a library carries
(``assemblies_spec.md`` §9.4): each assembly in isolation over its presets, boundaries, values and
variants, and nightly over a Latin hypercube sample of its parameter box.
A second noun reads what a run wrote: ``hisim kpis list`` prints the address, value and unit of
every KPI of one ``all_kpis.json``, filtered by building, tag, name, source, import or instance.

Two conventions hold throughout. Nothing here decides anything: every command asks the same code
the executor asks, so a command can never report something a run would contradict. And a failure
of the format is reported as the message the executor would print, on the standard error stream,
with the exit code that says a file was rejected rather than that the command was misused.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import sys
from pathlib import Path
from typing import Optional, Sequence, TextIO

from dotenv import load_dotenv

from hisim.cli_exit import ExitCodes
from hisim.cli_grouping import GroupingCommands, GroupingPaths
from hisim.config.introspection import describe_config
from hisim.cli_render import DescriptionRenderer, FactsRenderer, KpiAddressRenderer
from hisim.energy_system.assemblies.describe import AssemblyDescription
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.assemblies.schema import AssemblySchemaBuilder
from hisim.energy_system.errors import EnergySystemError
from hisim.energy_system.executor import run_energy_system
from hisim.energy_system.executor import SimulationParametersReader
from hisim.energy_system.recording.grouping_overview import OverviewPage
from hisim.energy_system.recording.session import RecordingSession, record_setup
from hisim.energy_system.schema_classes import ComponentClassScan
from hisim.energy_system.schema_export import default_schema_path, export_schema
from hisim.postprocessing.kpi_computation.kpi_address import KpiFinder


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
    """The seven verbs of the ``energy-system`` noun, one method each.

    Each method takes the parsed arguments and the two streams, does one thing and returns an
    exit code. Keeping them free of argument parsing is what lets a test drive them the way a
    user does — through :func:`main` with a list of words — while keeping each verb's own logic
    readable on its own.
    """

    @classmethod
    def describe(cls, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Prints everything declared about one configuration class, or about one assembly.

        An argument with a slash (``pv/array``) or the ``.assembly.yaml`` suffix names an assembly
        (``assemblies_spec.md`` §9.3), resolved along the library search path; anything else is a
        dotted class path.
        """
        if AssemblyDescription.is_assembly_path(arguments.class_path):
            AssemblyDescription.render(
                AssemblyDescription.resolve(arguments.class_path, AssemblyResolver.default()), out
            )
            return ExitCodes.OK
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
        written = export_schema(arguments.out)
        print(
            f"Wrote the schema of the energy-system format to {written}, and the schema of the assembly "
            f"file beside it, {written.parent / AssemblySchemaBuilder.FILENAME}.",
            file=out,
        )
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
        directory = Path(arguments.out) if arguments.out else RecordingSession.default_output_directory(module)
        parameters = SimulationParametersReader.read(Path(arguments.simulation_parameters))
        result = record_setup(
            module,
            parameters,
            directory,
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
        """Dispatches the three grouping verbs, which are a workflow rather than a single command."""
        actions = {
            "probe": GroupingCommands.probe,
            "import": GroupingCommands.import_workbook,
            "overview": GroupingCommands.overview,
        }
        action = actions.get(getattr(arguments, "action", None) or "")
        if action is None:
            print("Usage: hisim energy-system grouping {probe,import,overview} ...", file=error_stream)
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

    @classmethod
    def test_assemblies(cls, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Runs the test contract of every assembly of a library (``assemblies_spec.md`` §9.4).

        The summary goes to the standard output. Exit codes, each after the report of what ran is
        written where anything ran:

        - 0: every check held.
        - 1: a check failed (every failure listed on the standard error stream), or the harness
          cannot run at all with this library (a test-partner registry that does not read, a port
          no registered partner serves, a sample the harness built against the constraints).
        - 2: the harness cannot run as asked: an unknown tier, ``--samples``/``--seed`` with the pr
          tier, a ``--library`` directory that does not exist, an empty library or shard, an
          output directory that already holds runs.

        The harness and its sampler (scipy) are imported here, so that no other command of the
        console script needs them.
        """
        # pylint: disable=import-outside-toplevel  # only this verb needs the harness and scipy
        from hisim.energy_system.assemblies.testing.command import test_assemblies
        from hisim.energy_system.assemblies.testing.errors import AssemblyHarnessError, HarnessUsageError
        from hisim.energy_system.assemblies.testing.report import AssemblyTestFailure

        try:
            test_assemblies(
                tier=arguments.tier,
                libraries=arguments.library,
                samples=arguments.samples,
                seed=arguments.seed,
                steps=arguments.steps,
                out=arguments.out,
                shard=arguments.shard,
                stream=out,
            )
        except AssemblyTestFailure as failure:
            print(str(failure), file=error_stream)
            return ExitCodes.FILE_REJECTED
        except HarnessUsageError as error:
            print(str(error), file=error_stream)
            return ExitCodes.USAGE
        except AssemblyHarnessError as error:
            print(f"{type(error).__name__}: {error}", file=error_stream)
            return ExitCodes.FILE_REJECTED
        return ExitCodes.OK


class KpiCommands:
    """The verbs of the ``kpis`` noun: reading a run's KPI collection by address.

    ``hisim kpis list`` prints every KPI of one ``all_kpis.json`` that matches the filters, one
    ``<dotted address> = <value> <unit>`` per line (``roadmap/kpi_address_spec.md``, "Finder").
    The filters are the finder's own and are exact: ``--building``, ``--tag``, ``--name``,
    ``--source`` (the source's runtime name, ``source.name``), ``--import`` and ``--instance``
    (the source's import and instance keys).
    """

    @classmethod
    def list(cls, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Prints the address, value and unit of every KPI matching the filters."""
        try:
            path = KpiAddressRenderer.document_path(Path(arguments.path))
        except FileNotFoundError as error:
            print(str(error), file=error_stream)
            return ExitCodes.FILE_REJECTED
        try:
            finder = KpiFinder(json.loads(path.read_text(encoding="utf-8")))
        except ValueError as error:  # json.JSONDecodeError is a ValueError, and so is a refused collection
            print(f"{path}: {error}", file=error_stream)
            return ExitCodes.FILE_REJECTED
        entries = finder.entries(
            building=arguments.building,
            tag=arguments.tag,
            name=arguments.name,
            source=arguments.source,
            import_key=arguments.import_key,
            instance=arguments.instance,
        )
        KpiAddressRenderer.render(entries, out)
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

    describe = verbs.add_parser(
        "describe", help="print what one class can be configured with, or what one assembly offers"
    )
    describe.add_argument(
        "class_path",
        metavar="CLASS_OR_ASSEMBLY",
        help="dotted path of a component or config class, or an assembly: <family>/<name> or a *.assembly.yaml file",
    )

    facts = verbs.add_parser("facts", help="print where a file's sized values would come from")
    facts.add_argument("energy_system", metavar="ENERGY_SYSTEM", help="the *.energy_system.yaml file")

    schema = verbs.add_parser(
        "schema", help="write the JSON Schemas an editor binds to: the energy-system file's and the assembly file's"
    )
    schema.add_argument(
        "--out",
        default=None,
        help=(
            f"where to write the energy-system schema (default: {default_schema_path()}); the assembly "
            f"schema, {AssemblySchemaBuilder.FILENAME}, is written into the same directory"
        ),
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

    overview = actions.add_parser("overview", help="render the committed page describing every decision")
    overview.add_argument(
        "--out", default=None, help=f"where the page goes (default: the repository's {OverviewPage.DEFAULT_OUTPUT})"
    )

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

    harness = verbs.add_parser(
        "test-assemblies", help="run the test contract of every assembly of a library (assemblies_spec.md §9.4)"
    )
    harness.add_argument("--tier", choices=("pr", "nightly"), default="pr",
                         help="pr: presets, boundaries, values and variants; nightly: also the Latin hypercube")
    harness.add_argument("--library", action="append", default=None, metavar="DIR",
                         help="a library directory (repeatable, in search order); the machine's search path if none")
    harness.add_argument("--samples", type=int, default=None,
                         help="the nightly hypercube's samples per constraint branch (default 16)")
    harness.add_argument("--seed", type=int, default=None, help="the nightly hypercube's seed (default 20261003)")
    harness.add_argument("--steps", type=int, default=4, help="the values a monotone sweep moves through")
    harness.add_argument("--out", required=True,
                         help="where the runs and the report go; a directory that holds no runs yet")
    harness.add_argument("--shard", default="1/1", help="i/n: every n-th assembly of the sorted library from the i-th")

    kpis = nouns.add_parser("kpis", help="read a run's all_kpis.json by address")
    kpi_verbs = kpis.add_subparsers(dest="verb")
    kpi_list = kpi_verbs.add_parser(
        "list", help="print '<dotted address> = <value> <unit>' for every matching KPI"
    )
    kpi_list.add_argument("path", metavar="PATH", help="a result directory, or its all_kpis.json")
    kpi_list.add_argument("--building", default=None, help="only this building object")
    kpi_list.add_argument("--tag", default=None, help="only this KPI tag, as written in the JSON")
    kpi_list.add_argument("--name", default=None, help="only KPIs of this name")
    kpi_list.add_argument("--source", default=None, help="only KPIs whose source has this runtime name (source.name)")
    kpi_list.add_argument("--import", dest="import_key", default=None, help="only KPIs whose source has this import")
    kpi_list.add_argument("--instance", default=None, help="only KPIs whose source has this instance")
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
    # The two documented ways to run the same setup have to see the same configuration, so the
    # console script reads the `.env` holding UTSP_URL and UTSP_API_KEY exactly as
    # `hisim/hisim_main.py` does: python-dotenv walks up from the directory of the module that
    # calls it, which is `hisim/` for both of them, so both find the same file. It is read here
    # and not at import, because importing a library should not read files.
    load_dotenv()
    parser = build_parser()
    try:
        arguments = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exit_request:  # argparse reports usage errors by exiting
        return int(exit_request.code or ExitCodes.OK)
    verbs_by_noun = {
        "energy-system": {
            "describe": EnergySystemCommands.describe,
            "facts": EnergySystemCommands.facts,
            "grouping": EnergySystemCommands.grouping,
            "record": EnergySystemCommands.record,
            "schema": EnergySystemCommands.schema,
            "run": EnergySystemCommands.run,
            "test-assemblies": EnergySystemCommands.test_assemblies,
        },
        "kpis": {"list": KpiCommands.list},
    }
    verb = verbs_by_noun.get(arguments.noun or "", {}).get(getattr(arguments, "verb", None) or "")
    if verb is None:
        parser.print_help(sys.stderr)
        return ExitCodes.USAGE
    try:
        return verb(arguments, sys.stdout, sys.stderr)
    except EnergySystemError as error:
        print(str(error), file=sys.stderr)
        return ExitCodes.FILE_REJECTED


if __name__ == "__main__":  # pragma: no cover - the console script calls main() directly
    sys.exit(main())
