"""The ``hisim setup-rules`` commands: the generated overview of every setup rule, and a check of one file.

``hisim setup-rules list`` prints every rule of the registry in code order with its code, name, family, stage,
nature, enforcement, requirements and waivers. ``hisim setup-rules waivers [--kind KIND]`` prints the waivers
grouped by kind. ``hisim setup-rules show CODE_OR_NAME`` prints one rule's declarations and its docstring, the
four headings every refusal points to. ``hisim setup-rules check FILE [--stage load|build] [--format text|json]``
runs the rules of the ``LOAD`` and ``BUILD`` stages on an energy-system file without simulating and prints every
fault and skip, exiting non-zero when an enforced rule found a fault.

Every command reads the registry it is given, which the command line collects from the rules package, so the
overview cannot disagree with the code: there is no committed page of the rules or their waivers.
"""

from __future__ import annotations

import argparse
import contextlib
import inspect
import json
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, TextIO, Tuple

from hisim.cli_exit import ExitCodes
from hisim.energy_system.document import RawDocument
from hisim.energy_system.executor import EnergySystemExecutor
from hisim.energy_system.loader import EnergySystemReader
from hisim.energy_system.validation import validate_structure
from hisim.setup_rules.inventory import Inventory, RuleRecord
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.registry import RuleClass, SetupRuleRegistry, StageFindings
from hisim.setup_rules.rule import RuleCodes, RuleStage
from hisim.setup_rules.subjects import BuiltSystem, LoadedDocument
from hisim.simulationparameters import SimulationParameters

#: The signature every verb of the command line shares: the parsed arguments, the output and the error stream.
Verb = Callable[[argparse.Namespace, TextIO, TextIO], int]


class SetupRuleCommands:
    """The four verbs of the ``setup-rules`` noun, each answered from one registry.

    The registry is handed in rather than collected here, so that a test drives a verb on rules of its own; the
    command line hands in the registry of every rule of the package (:meth:`verbs`).
    """

    #: The year of the one-day simulation parameters ``check`` builds a file's components with.
    CHECK_YEAR: int = 2021

    def __init__(self, registry: SetupRuleRegistry) -> None:
        """Answer the verbs from this registry."""
        self.registry = registry

    @classmethod
    def verbs(cls) -> Dict[str, Verb]:
        """Return the verbs by name, each answered from the registry of the rules package collected when it runs."""
        return {
            "list": lambda arguments, out, error: cls(SetupRuleRegistry.collect()).list(arguments, out, error),
            "waivers": lambda arguments, out, error: cls(SetupRuleRegistry.collect()).waivers(arguments, out, error),
            "show": lambda arguments, out, error: cls(SetupRuleRegistry.collect()).show(arguments, out, error),
            "check": lambda arguments, out, error: cls(SetupRuleRegistry.collect()).check(arguments, out, error),
        }

    @staticmethod
    def add_parser(nouns: argparse._SubParsersAction) -> None:  # pylint: disable=protected-access  # argparse's own type
        """Add the ``setup-rules`` noun and its four verbs to the command line."""
        setup_rules = nouns.add_parser("setup-rules", help="list, explain and check the rules every setup obeys")
        verbs = setup_rules.add_subparsers(dest="verb")
        verbs.add_parser("list", help="print every rule with its code, name, stage, nature, requirements and waivers")
        waivers = verbs.add_parser("waivers", help="print every waiver of a rule, grouped by kind")
        waivers.add_argument("--kind", choices=[kind.value for kind in SetupKind], default=None, help="only this kind")
        show = verbs.add_parser("show", help="print one rule's declarations and its docstring")
        show.add_argument("rule", metavar="CODE_OR_NAME", help="the rule's code (EP-06) or name (room-heat-needs-receiver)")
        check = verbs.add_parser("check", help="run the load and build rules on a file without simulating")
        check.add_argument("energy_system", metavar="ENERGY_SYSTEM", help="the *.energy_system.yaml file")
        check.add_argument(
            "--stage",
            choices=[RuleStage.LOAD.value, RuleStage.BUILD.value],
            default=RuleStage.BUILD.value,
            help="the last stage to run: load, or load and build (default)",
        )
        check.add_argument("--format", dest="output_format", choices=["text", "json"], default="text", help="text (default) or json")

    def list(self, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Print every rule in code order: one line of declarations, then one line per waiver."""
        del arguments, error_stream  # every verb shares one signature; this one takes no option
        if not self.registry.rules:
            moving = sum(record.moves for record in Inventory.RECORDS)
            out.write(f"No setup rule is in the package yet; the inventory lists {moving} checks that move into it.\n")
            return ExitCodes.OK
        for rule in self.registry.rules:
            out.write(self.summary(rule) + "\n")
            for kind, reason in self.waivers_of(rule):
                out.write(f"    waived for {kind.value}: {reason}\n")
        return ExitCodes.OK

    def waivers(self, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Print the waivers grouped by kind, every kind or the one ``--kind`` names, in the order of ``SetupKind``."""
        del error_stream  # every verb shares one signature; this one reports nothing separately
        kinds = [SetupKind(arguments.kind)] if arguments.kind else list(SetupKind)
        for kind in kinds:
            waived = [(rule, reason) for rule in self.registry.rules for waiver, reason in self.waivers_of(rule) if waiver == kind]
            out.write(f"{kind.value}: {len(waived)} rule{'' if len(waived) == 1 else 's'} waived\n")
            for rule, reason in waived:
                out.write(f"    {rule.CODE} {rule.NAME}: {reason}\n")
        return ExitCodes.OK

    def show(self, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Print one rule's declarations and its docstring, or name the unknown code on the error stream."""
        rule = self.registry.rule(arguments.rule)
        if rule is None:
            error_stream.write(f"no setup rule has the code or name '{arguments.rule}'; 'hisim setup-rules list' lists them\n")
            return ExitCodes.USAGE
        out.write(self.summary(rule) + "\n")
        for kind, reason in self.waivers_of(rule):
            out.write(f"waived for {kind.value}: {reason}\n")
        record = self.record_of(rule)
        if record is not None:
            out.write(f"lives: {record.lives}\n")
        out.write("\n" + inspect.cleandoc(rule.__doc__ or "") + "\n")
        return ExitCodes.OK

    def check(self, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Run the load rules, and the build rules unless ``--stage load``, on one file, and print every fault and skip.

        The file is read and structurally validated as a run reads it; a fault of the reader is reported as the
        message a run would print. The build stage builds and wires the components with one-day simulation
        parameters, without preparing or simulating anything, and runs both kinds of build rules: those that read
        the built file and those that read the connected components.

        Returns:
            Zero when no enforced rule found a fault, the file-rejected code otherwise.
        """
        path = Path(arguments.energy_system)
        # What the build logs goes to the error stream, so that the report alone is on the output, as JSON or as text.
        with contextlib.redirect_stdout(error_stream):
            stages = self.findings_of(path, RuleStage(arguments.stage))
        if arguments.output_format == "json":
            kind = stages[0].kind.value if stages else None
            json.dump({"file": str(path), "kind": kind, "stages": [findings.to_dict() for findings in stages]}, out, indent=2)
            out.write("\n")
        else:
            for findings in stages:
                out.write(self.findings_text(findings))
        return ExitCodes.FILE_REJECTED if any(findings.faults for findings in stages) else ExitCodes.OK

    def findings_of(self, path: Path, last_stage: RuleStage) -> List[StageFindings]:
        """Run the rules of every stage up to ``last_stage`` on a file and return what each found.

        The build stage runs only when the load stage found no enforced fault, as in a run. It returns one findings
        record for the rules of the built file and one for the rules of the connected components, in that order. The
        components are built with one-day simulation parameters whose result directory is a temporary one, removed
        afterwards, so a check leaves nothing behind.

        Raises:
            EnergySystemError: If the file cannot be read, is structurally invalid or cannot be built.
        """
        document, origin = RawDocument.read(path)
        model = EnergySystemReader.build(document, origin)
        validate_structure(model)
        stages = [self.registry.findings(RuleStage.LOAD, LoadedDocument(model=model, document=document, file=str(path)), model.setup_kind)]
        if last_stage == RuleStage.LOAD or stages[0].faults:
            return stages
        # A build claims a result directory; a check writes no result, so the directory is a temporary one.
        with tempfile.TemporaryDirectory(prefix="hisim-setup-rules-check-") as result_directory:
            parameters = SimulationParameters.one_day_only(self.CHECK_YEAR)
            parameters.result_directory = result_directory
            executor = EnergySystemExecutor(
                model, parameters, source_directory=str(path.parent), source_filename=path.name, source_energy_system=str(path)
            )
            built = executor.construct()
            stages.append(self.registry.findings(RuleStage.BUILD, built.built_document(), model.setup_kind))
            if not stages[-1].faults:
                built.simulator.connect_all_components()
                components = tuple(wrapped.my_component for wrapped in built.simulator.wrapped_components)
                stages.append(self.registry.findings(RuleStage.BUILD, BuiltSystem(components=components), model.setup_kind))
        return stages

    @staticmethod
    def findings_text(findings: StageFindings) -> str:
        """Return the text report of one stage: a headline, then the faults, the logged faults and the skips.

        Example::

            build rules on the BuiltSystem (demo): 1 fault
              - EP-01 a-rule [component=c]: <message>; see: hisim setup-rules show EP-01
              not run:
              - EP-06 was not run: it requires EP-01, which found faults or was not run
        """
        count = len(findings.faults)
        found = f"{count} fault{'' if count == 1 else 's'}" if count else "no fault"
        headline = f"{findings.stage.value} rules on the {findings.subject} ({findings.kind.value}): {found}"
        lines = [headline]
        lines.extend(f"  - {fault.log_line()}" for fault in findings.faults)
        if findings.logged:
            lines.append("  logged only:")
            lines.extend(f"  - {fault.log_line()}" for fault in findings.logged)
        if findings.skipped:
            lines.append("  not run:")
            lines.extend(f"  - {skip.line()}" for skip in findings.skipped)
        return "\n".join(lines) + "\n"

    @classmethod
    def summary(cls, rule: RuleClass) -> str:
        """Return one rule's declarations on one line: code, name, family, stage, nature, enforcement and requirements.

        Example: ``EP-06 room-heat-needs-receiver  family=energy_ports stage=build nature=D enforced=yes requires=EP-05``.
        The nature comes from the rule's record in the inventory, ``?`` when it has none.
        """
        family = RuleCodes.family_of(rule.__module__) or rule.__module__
        record = cls.record_of(rule)
        nature = record.nature.value if record is not None else "?"
        requires = ",".join(sorted(rule.REQUIRES)) or "-"
        enforced = "yes" if rule.ENFORCED else "no (logged)"
        return f"{rule.CODE} {rule.NAME}  family={family} stage={rule.STAGE.value} nature={nature} enforced={enforced} requires={requires}"

    @staticmethod
    def record_of(rule: RuleClass) -> Optional[RuleRecord]:
        """Return the inventory's record of a rule, found by ``<family>.<snake_name>``, or ``None``."""
        family = RuleCodes.family_of(rule.__module__) or rule.__module__
        return Inventory.record(f"{family}.{rule.NAME.replace('-', '_')}")

    @staticmethod
    def waivers_of(rule: RuleClass) -> Tuple[Tuple[SetupKind, str], ...]:
        """Return a rule's waivers in the order of ``SetupKind``, each kind with its reason."""
        return tuple((kind, rule.WAIVED_FOR[kind]) for kind in SetupKind if kind in rule.WAIVED_FOR)
