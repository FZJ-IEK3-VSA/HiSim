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
``hisim energy-system schema`` writes the JSON Schema an editor binds to. And ``hisim
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
from typing import Any, Optional, Sequence, TextIO

from hisim.config.introspection import (
    ConfigDescription,
    ConstructorInfo,
    FieldInfo,
    describe_config,
)
from hisim.energy_system.bindings import facts_read_by
from hisim.energy_system.classes import validate_classes
from hisim.energy_system.configure import configure_energy_system
from hisim.energy_system.errors import EnergySystemError
from hisim.energy_system.executor import run_energy_system
from hisim.energy_system.groups import expand_groups
from hisim.energy_system.loader import parse_energy_system
from hisim.energy_system.schema_classes import ComponentClassScan
from hisim.energy_system.schema_export import default_schema_path, export_schema
from hisim.energy_system.validation import validate_structure


class ExitCodes:
    """What the process returns, and what each value tells a script that called it.

    Three outcomes are worth distinguishing, and the values follow the convention every command
    line uses: zero for success, two for a caller that spelled the command wrong, and one for a
    command that was understood and whose subject was refused. A wrapper script therefore knows
    from the code alone whether to fix its invocation or to show the user a file error.
    """

    OK: int = 0
    FILE_REJECTED: int = 1
    USAGE: int = 2


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


class Report:
    """The shared shape of the blocks this command line prints: headings, items and details.

    Both reports are the same kind of document — a title, then named sections, each holding items
    that may carry labelled details — so the indentation and the separation of a heading from what
    precedes it are decided once here. Keeping it a base class rather than a formatting function
    means a subclass can widen a column without re-stating the rest.
    """

    #: Indentation, in spaces, of a heading, of an item under it, and of an item's own details.
    HEADING_INDENT: int = 2
    CONTENT_INDENT: int = 4
    DETAIL_INDENT: int = 6

    @classmethod
    def _heading(cls, title: str, stream: TextIO) -> None:
        """Writes one section heading, preceded by a blank line separating it from the last."""
        print(f"\n{' ' * cls.HEADING_INDENT}{title}", file=stream)

    @classmethod
    def _item(cls, text: str, stream: TextIO) -> None:
        """Writes one item of the current section."""
        print(f"{' ' * cls.CONTENT_INDENT}{text}", file=stream)

    @classmethod
    def _detail(cls, label: str, value: str, stream: TextIO) -> None:
        """Writes one labelled detail line under the item it belongs to."""
        print(f"{' ' * cls.DETAIL_INDENT}{label}: {value}", file=stream)


class DescriptionRenderer(Report):
    """Renders one configuration class as the block ``describe`` prints.

    Everything shown comes from :func:`~hisim.config.introspection.describe_config`, so the
    command is a rendering and nothing else: it cannot show a preset the executor would not
    accept, or omit one it would. The five sections follow the order an author needs them in —
    what can be set, what sets it for me, what I have to supply myself, what the system computes,
    and what this class computes for everybody else.
    """

    #: Width the field column is padded to, chosen so the longest HiSim field names still leave
    #: room for a type on an eighty-column terminal.
    FIELD_WIDTH: int = 44

    #: What is printed where a field or a parameter has no default at all, as opposed to one
    #: whose default happens to be ``None``.
    NO_DEFAULT: str = "(required)"

    #: How a union annotation opens, and the short spelling a sizable field's union is folded
    #: back into for display.
    UNION_PREFIX: str = "Union["
    SIZABLE_SPELLING: str = "Sizable"

    @classmethod
    def render(cls, description: ConfigDescription, stream: TextIO) -> None:
        """Writes the whole description.

        Args:
            description: The description to render.
            stream: Where to write it.
        """
        print(description.config_class_name, file=stream)
        cls._fields(description, stream)
        cls._presets(description, stream)
        cls._constructors(description, stream)
        cls._sizable(description, stream)
        cls._facts(description, stream)

    @classmethod
    def _fields(cls, description: ConfigDescription, stream: TextIO) -> None:
        """Writes the settable fields with their types, defaults and sizability."""
        cls._heading("fields", stream)
        for field in description.fields:
            default = cls.NO_DEFAULT if field.default is dataclasses.MISSING else repr(field.default)
            marker = "  [sizable]" if field.sizable else ""
            name = f"{field.name} ({cls.type_of(field)})".ljust(cls.FIELD_WIDTH)
            cls._item(f"{name} = {default}{marker}", stream)

    @classmethod
    def type_of(cls, field: FieldInfo) -> str:
        """Renders a field's type the way the format's own documentation spells it.

        A sizable field is annotated as the union of its value type with the sizing sentinel and
        the law type, which is how the kernel expresses "this may be left to be sized" and is a
        mouthful nobody needs to read three times per class. It is folded back into the
        ``Sizable[T]`` spelling the source itself uses; every other type is shown as written.

        Args:
            field: The field to render.

        Returns:
            The type as one short string.
        """
        if not field.sizable or not field.type_name.startswith(cls.UNION_PREFIX):
            return field.type_name
        inner = field.type_name[len(cls.UNION_PREFIX):].split(",")[0].strip()
        return f"{cls.SIZABLE_SPELLING}[{inner}]"

    @classmethod
    def _presets(cls, description: ConfigDescription, stream: TextIO) -> None:
        """Writes each preset with the sizable fields it pins and the ones it leaves open."""
        cls._heading("presets", stream)
        if not description.presets:
            cls._item("(none)", stream)
            return
        for preset in description.presets:
            canonical = "  (canonical)" if preset.canonical else ""
            cls._item(f"{preset.name}{canonical}", stream)
            cls._detail("pinned", ", ".join(preset.pinned) or "(nothing sizable)", stream)
            cls._detail("AUTO", ", ".join(preset.auto) or "(nothing left open)", stream)
            if preset.note:
                cls._detail("note", preset.note, stream)

    @classmethod
    def _constructors(cls, description: ConfigDescription, stream: TextIO) -> None:
        """Writes each named constructor as the call an author would write."""
        cls._heading("constructors", stream)
        if not description.constructors:
            cls._item("(none)", stream)
            return
        for entry in description.constructors:
            cls._item(cls.signature(entry), stream)
            if entry.note:
                cls._detail("note", entry.note, stream)

    @classmethod
    def signature(cls, entry: ConstructorInfo) -> str:
        """Renders one named constructor as ``name(parameter: type, other: type = default)``.

        Args:
            entry: The constructor to render.

        Returns:
            The one-line signature, the instance name omitted because the executor supplies it
            from the entry's own key and an author never writes it.
        """
        parameters = []
        for parameter in entry.parameters:
            rendered = f"{parameter.name}: {parameter.type_name}"
            if parameter.default is not dataclasses.MISSING:
                rendered = f"{rendered} = {parameter.default!r}"
            parameters.append(rendered)
        return f"{entry.name}({', '.join(parameters)})"

    @classmethod
    def _sizable(cls, description: ConfigDescription, stream: TextIO) -> None:
        """Writes each sizable field with its law, the facts it reads and where its value comes from."""
        cls._heading("sizable fields", stream)
        if not description.sizable_fields:
            cls._item("(none)", stream)
            return
        for field in description.sizable_fields:
            cls._item(field.name, stream)
            cls._detail("law", field.law, stream)
            facts = ", ".join(f"{fact} ({cardinality})" for fact, cardinality in field.facts_read)
            cls._detail("facts", facts or "(none)", stream)
            if field.fields_read:
                cls._detail("fields", ", ".join(field.fields_read), stream)
            cls._detail("kind", field.kind.explain(), stream)
            if field.note:
                cls._detail("note", field.note, stream)

    @classmethod
    def _facts(cls, description: ConfigDescription, stream: TextIO) -> None:
        """Writes the facts the class contributes to the rest of an energy system."""
        cls._heading("facts provided", stream)
        for fact in description.facts_provided or ("(none)",):
            cls._item(fact, stream)


class FactsRenderer(Report):
    """Renders the sizing resolution of a whole file: who provides what, and who reads it from whom.

    This is the report an author wants *before* a run, because it answers the two questions a
    sizing failure raises after one: which components could have supplied a fact, and which one
    a given consumer actually read it from. Both halves are printed even when resolution fails,
    with the kernel's own refusal underneath, so that a file which does not resolve still shows
    how far it got.

    The report opens with the file's knobs, which is what a consumer of a checked-in energy
    system edits: one boolean per group and one option name per variant. The two are listed
    together because they are one surface to whoever is configuring a run, even though they are
    two constructs — an independent add-on and an exclusive choice — to whoever authored it.
    """

    #: Width the fact column is padded to before the list of components, and the wider width the
    #: resolution section needs because its left column carries a component name as well.
    FACT_WIDTH: int = 46
    CONSUMER_WIDTH: int = 54

    #: Width the knob names are padded to before their value, wide enough for the dotted
    #: ``variants.<name>`` spelling a caller edits.
    KNOB_WIDTH: int = 46

    @classmethod
    def render(cls, path: Path, stream: TextIO) -> int:
        """Loads a file, resolves its sizing without building anything, and writes the report.

        Args:
            path: The ``*.energy_system.yaml`` file to inspect.
            stream: Where to write the report.

        Returns:
            The exit code: zero when the file resolves, and the file-rejected code when it does
            not, the refusal having been written to the report itself.
        """
        authored = parse_energy_system(path)
        expanded, _ = expand_groups(authored)
        validate_structure(expanded)
        print(f"{expanded.name} ({path})", file=stream)
        cls._knobs(authored, stream)
        bindings = validate_classes(expanded)
        cls._provided(bindings, stream)
        cls._consumed(bindings, stream)
        try:
            configured = configure_energy_system(expanded, bindings=bindings)
        except EnergySystemError as error:
            cls._heading("resolution", stream)
            for line in str(error).splitlines():
                cls._item(line, stream)
            return ExitCodes.FILE_REJECTED
        cls._resolved(configured, stream)
        cls._warnings(configured.warnings, stream)
        return ExitCodes.OK

    @classmethod
    def _knobs(cls, authored: Any, stream: TextIO) -> None:
        """Writes the switches of the authored file: a flag per group, an option per variant.

        The authored file is read rather than the expanded one, because expansion is exactly
        what removes the knobs: a switched-off group is gone from it and a variant is resolved
        into the top level. Every option a variant offers is listed after the selected one, so
        the line says both what is set and what may be set instead.

        Args:
            authored: The parsed file, before expansion.
            stream: Where to write the section.
        """
        cls._heading("knobs", stream)
        if not authored.groups and not authored.variants:
            cls._item("(none)", stream)
        for name, group in authored.groups.items():
            cls._item(f"{f'groups.{name}'.ljust(cls.KNOB_WIDTH)}{str(group.enabled).lower()}", stream)
        for name, variant in authored.variants.items():
            alternatives = ", ".join(option for option in variant.options if option != variant.selected)
            knob = f"variants.{name}".ljust(cls.KNOB_WIDTH)
            cls._item(f"{knob}{variant.selected}  (or {alternatives or '<nothing else>'})", stream)

    @classmethod
    def _provided(cls, bindings: Any, stream: TextIO) -> None:
        """Writes every fact the file's components contribute, with the components providing it."""
        cls._heading("facts provided", stream)
        providers: dict = {}
        for name in bindings.names():
            for fact in bindings[name].description.facts_provided:
                providers.setdefault(fact, []).append(name)
        if not providers:
            cls._item("(none)", stream)
        for fact in sorted(providers):
            cls._item(f"{fact.ljust(cls.FACT_WIDTH)}{', '.join(providers[fact])}", stream)

    @classmethod
    def _consumed(cls, bindings: Any, stream: TextIO) -> None:
        """Writes every fact the file's components read, with the components reading it."""
        cls._heading("facts consumed", stream)
        consumers: dict = {}
        for name in bindings.names():
            for fact in facts_read_by(bindings[name].config_class):
                consumers.setdefault(fact, []).append(name)
        if not consumers:
            cls._item("(none)", stream)
        for fact in sorted(consumers):
            cls._item(f"{fact.ljust(cls.FACT_WIDTH)}{', '.join(consumers[fact])}", stream)

    @classmethod
    def _resolved(cls, configured: Any, stream: TextIO) -> None:
        """Writes one line per fact that was actually read, naming the provider and the rule."""
        cls._heading("resolution", stream)
        lookups = configured.report.lookups
        if not lookups:
            cls._item("(nothing was sized)", stream)
        for lookup in lookups:
            consumer = f"{lookup.consumer}.{lookup.fact}".ljust(cls.CONSUMER_WIDTH)
            candidates = ""
            if len(lookup.candidates) > 1:
                candidates = f"  of {', '.join(sorted(lookup.candidates))}"
            cls._item(f"{consumer}<- {lookup.source}  [{lookup.mode}]{candidates}", stream)

    @classmethod
    def _warnings(cls, warnings: Sequence[str], stream: TextIO) -> None:
        """Writes the warnings a run would print, which are never a reason to refuse a file."""
        cls._heading("warnings", stream)
        for warning in warnings or ("(none)",):
            cls._item(warning, stream)


class EnergySystemCommands:
    """The four verbs of the ``energy-system`` noun, one method each.

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
