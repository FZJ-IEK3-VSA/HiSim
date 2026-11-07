"""The setup rule: one named check on one subject at one stage, and the records it reports.

A setup rule (``SetupRule``) is a class with class-scoped declarations -- its code, its name, its stage, whether its
faults refuse the run, the rules it requires and the kinds that waive it -- a docstring in a fixed template, and one
pure method, ``check``, that returns every fault it finds. A fault (``Fault``) carries the rule's code and name, the
message and the location as fields. The registry (``hisim/setup_rules/registry.py``) runs the rules of a stage and
raises one ``SetupRuleViolation`` listing every fault of the enforced rules and every rule it skipped (``Skip``).

The declarations are checked when a rule class is created, so a malformed rule fails when its module is imported,
before any run.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import ClassVar, Dict, FrozenSet, Generic, Iterable, List, Mapping, Optional, Sequence, Tuple, Type, TypeVar, Union, get_args, get_origin

from hisim.setup_rules.kinds import SetupKind


class RuleStage(str, Enum):
    """When a setup rule runs, in the order of a calculation.

    ``LOAD`` runs on the document before any component class is imported (declarative mode only). ``BUILD`` runs on
    the built, wired system before the first step, in both modes. ``AFTER_RUN`` runs on the result table in the
    post-processing, before anything is written. The values are the words a JSON report and the command line spell.
    """

    LOAD = "load"
    BUILD = "build"
    AFTER_RUN = "after_run"

    @property
    def position(self) -> int:
        """The place of the stage in a calculation, 0 for ``LOAD``; a rule may only require rules of its own or an earlier place."""
        return list(RuleStage).index(self)


class RuleSubject:
    """The base of the object a stage hands to each of its rules: the document, the built system or the results.

    Each subject class states the stage it belongs to. A rule names its subject class as the type argument of its
    base, ``SetupRule[BuiltSystem]``; the registry hands a subject only to the rules that name its class. The
    subjects themselves are in ``hisim/setup_rules/subjects.py``.
    """

    #: The stage whose rules read this subject.
    STAGE: ClassVar[RuleStage]


#: The type of the subject a rule reads (a ``RuleSubject`` subclass).
SubjectT = TypeVar("SubjectT", bound=RuleSubject)

#: One step of a key path from a file's top level to a value: a mapping key or a list index.
KeyPathStep = Union[str, int]


@dataclass(frozen=True)
class Fault:
    """One finding of one setup rule: the message, and where in the setup it is.

    The location is held as fields beside the message, so that a person reads the message and a tool reads the
    fields. ``component`` is the component's runtime name, or the entry's name in a file, and ``None`` for a fault
    of the whole system; ``port`` is the input or output name, or ``None``; ``key_path`` holds the keys and indices
    from the file's top level to the value, for example ``("components", "Battery1", "inputs", 0)``, and is empty
    when the subject is not a file; ``file`` is the file's path, ``None`` in Python mode. A rule builds its faults
    with ``SetupRule.fault``, which fills the code, the name and the stage and ends the message with the pointer
    ``see: hisim setup-rules show <code>``.
    """

    code: str
    name: str
    stage: RuleStage
    message: str
    component: Optional[str] = None
    port: Optional[str] = None
    key_path: Tuple[KeyPathStep, ...] = ()
    file: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        """Return the fault as plain data with every field, for a JSON report.

        Example: ``{"code": "EP-06", "name": "room-heat-needs-receiver", "stage": "build", "message": "...",
        "component": "buffer", "port": "HeatToHeatedZoneInWatt", "key_path": [], "file": None}``.
        """
        return {
            "code": self.code,
            "name": self.name,
            "stage": self.stage.value,
            "message": self.message,
            "component": self.component,
            "port": self.port,
            "key_path": list(self.key_path),
            "file": self.file,
        }

    def log_line(self) -> str:
        """Return the fault as one line with its code, name and location, as a rule that only logs reports it.

        Example: ``EP-06 room-heat-needs-receiver [component=buffer, port=HeatToHeatedZoneInWatt]: <message>``.
        A field that does not apply is left out, and a fault without any location has no brackets.
        """
        located = [
            f"{label}={value}"
            for label, value in (
                ("file", self.file),
                ("key_path", ".".join(str(step) for step in self.key_path) or None),
                ("component", self.component),
                ("port", self.port),
            )
            if value is not None
        ]
        where = f" [{', '.join(located)}]" if located else ""
        return f"{self.code} {self.name}{where}: {self.message}"


@dataclass(frozen=True)
class Skip:
    """A rule the registry did not run because a rule it requires found faults, or was itself skipped.

    ``because_of`` holds the codes of those required rules, in code order. A skip is always reported beside the
    faults, never silent: a rule that would read what a broken earlier rule guards reports nothing useful.
    """

    code: str
    because_of: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        """Return the skip as plain data, ``{"code": "EP-06", "because_of": ["EP-05"]}``, for a JSON report."""
        return {"code": self.code, "because_of": list(self.because_of)}

    def line(self) -> str:
        """Return the skip as one sentence, for example ``EP-06 was not run: it requires EP-05, which found faults or was not run``."""
        return f"{self.code} was not run: it requires {', '.join(self.because_of)}, which found faults or was not run"


class UndeclaredSetupKindError(ValueError):
    """A setup that declares no kind, refused before a stage of its rules runs.

    Every setup declares its kind, and nothing supplies a default: a default would let a demonstration run as a
    house by omission. The refusal is the registry's precondition, not a rule, since the kind decides which rules
    run. The recorder refuses an undeclared setup with the same message.
    """

    #: The one message of the refusal: what to write, and where.
    MESSAGE: ClassVar[str] = (
        "this setup declares no kind: write `setup_kind: residential | commercial | plant | demo` into the file, "
        "or call `my_sim.declare_setup_kind(SetupKind.RESIDENTIAL)` in the setup function"
    )

    def __init__(self) -> None:
        """Build the refusal with its fixed message."""
        super().__init__(self.MESSAGE)


class SetupRuleViolation(ValueError):
    """Every fault the enforced rules of one stage found, and every rule the registry skipped, raised together.

    One violation per stage lists all of them, ordered by code, then by component and port, so that one run shows
    every fault instead of one per run. The faults keep their fields, and ``to_dict`` gives them as plain data for a
    JSON report. Existing error classes of the energy-system format and of the energy balance become subclasses
    when their checks move into the rules package, so an ``except`` of either kind keeps working.
    """

    def __init__(self, stage: RuleStage, kind: SetupKind, faults: Sequence[Fault], skipped: Sequence[Skip]) -> None:
        """Hold the faults and skips of one stage and build the message that lists them.

        Args:
            stage: The stage whose rules found the faults.
            kind: The kind the setup declared, which selected the rules that ran.
            faults: Every fault of the enforced rules of the stage, in the order to report them.
            skipped: Every rule the registry skipped in this stage.
        """
        self.stage = stage
        self.kind = kind
        self.faults: Tuple[Fault, ...] = tuple(faults)
        self.skipped: Tuple[Skip, ...] = tuple(skipped)
        super().__init__(self.describe(stage, kind, self.faults, self.skipped))

    @staticmethod
    def describe(stage: RuleStage, kind: SetupKind, faults: Sequence[Fault], skipped: Sequence[Skip]) -> str:
        """Return the message of a violation: one headline, one line per fault, then one line per skipped rule.

        Example::

            The build rules refuse this demo setup (2 faults):
            - EP-01 circuit-pairing: <message>; see: hisim setup-rules show EP-01
            - EP-05 input-port-pairs: <message>; see: hisim setup-rules show EP-05
            Not run:
            - EP-06 was not run: it requires EP-05, which found faults or was not run

        Args:
            stage: The stage of the faults.
            kind: The kind the setup declared.
            faults: The faults, in the order to list them.
            skipped: The rules that were not run.

        Returns:
            The message, one line per fault and per skip.
        """
        count = f"{len(faults)} fault" + ("" if len(faults) == 1 else "s")
        lines = [f"The {stage.value} rules refuse this {kind.value} setup ({count}):"]
        lines.extend(f"- {fault.code} {fault.name}: {fault.message}" for fault in faults)
        if skipped:
            lines.append("Not run:")
            lines.extend(f"- {skip.line()}" for skip in skipped)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, object]:
        """Return the violation as plain data: the stage, the kind, every fault with its fields and every skip.

        Example: ``{"stage": "build", "kind": "demo", "faults": [{"code": ..., "name": ..., "stage": ...,
        "message": ..., "component": ..., "port": ..., "key_path": [...], "file": ...}],
        "skipped": [{"code": ..., "because_of": [...]}]}``. ``json.dumps`` writes it as it is.
        """
        return {
            "stage": self.stage.value,
            "kind": self.kind.value,
            "faults": [fault.to_dict() for fault in self.faults],
            "skipped": [skip.to_dict() for skip in self.skipped],
        }


class RuleCodes:
    """The scheme of the codes and names of the setup rules, and the codes that are retired.

    A code is a family's two-letter prefix, a hyphen and two characters, a digit and then a digit or a capital
    letter: ``EP-06``, ``EF-31``, ``EF-4K``. It is stable for ever: a code is never changed and never reused, and
    the code of a removed rule goes into ``RETIRED_CODES`` with a comment saying what it was and in which pull
    request it went. A name is kebab-case, ``room-heat-needs-receiver``. The retired codes live here rather than on
    the registry because ``SetupRule`` checks them when a rule class is created, and the registry imports this module.
    """

    #: The form of every code.
    CODE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Z]{2}-[0-9][0-9A-Z]$")

    #: The form of every name: lowercase words of letters and digits joined by hyphens.
    NAME_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

    #: The code prefix of each rule family, by the module of the package that holds the family.
    PREFIX_BY_FAMILY: ClassVar[Mapping[str, str]] = MappingProxyType(
        {
            "document": "EF",
            "bindings": "EF",
            "sizing": "EF",
            "wiring": "EF",
            "assemblies": "EF",
            "energy_ports": "EP",
            "fluid_loops": "FL",
            "components": "CB",
            "results": "AR",
            "meters": "MT",
        }
    )

    #: The codes of rules that were removed. A code here is never given to another rule; the set only grows.
    RETIRED_CODES: ClassVar[FrozenSet[str]] = frozenset()

    #: The package that holds the rules; a rule class in one of its modules belongs to the family of that module.
    PACKAGE: ClassVar[str] = "hisim.setup_rules"

    @classmethod
    def family_of(cls, module: str) -> Optional[str]:
        """Return the family of a rule defined in a module, or ``None`` for a module outside the rules package.

        Example: ``hisim.setup_rules.energy_ports`` gives ``energy_ports``; ``tests.rules.test_aggregation`` gives
        ``None``.
        """
        package, _, family = module.rpartition(".")
        return family if package == cls.PACKAGE else None

    @classmethod
    def code_problems(cls, code: object, module: str) -> List[str]:
        """Return what is wrong with a rule's code: its type, its form, a retired code, or another family's prefix.

        A rule outside the rules package (a test's rule) is not held to a family's prefix. For example, ``"EP-6"``
        breaks the form, and ``"EF-31"`` in ``hisim.setup_rules.energy_ports`` has the prefix of another family.

        Args:
            code: The declared ``CODE``.
            module: The module the rule class is defined in.

        Returns:
            One sentence per problem; empty for a good code.
        """
        if not isinstance(code, str) or not cls.CODE_PATTERN.match(code):
            return [f"its CODE {code!r} is not of the form <PREFIX>-<nn>, for example 'EP-06'"]
        problems = []
        if code in cls.RETIRED_CODES:
            problems.append(f"its CODE {code} is retired and is never given to another rule")
        family = cls.family_of(module)
        if family is not None:
            prefix = cls.PREFIX_BY_FAMILY.get(family)
            if prefix is None:
                problems.append(f"its module '{family}' is not a rule family of the package ({', '.join(cls.PREFIX_BY_FAMILY)})")
            elif not code.startswith(f"{prefix}-"):
                problems.append(f"its CODE {code} does not carry the prefix '{prefix}-' of the family '{family}'")
        return problems

    @classmethod
    def name_problems(cls, name: object) -> List[str]:
        """Return what is wrong with a rule's name: ``"room-heat-needs-receiver"`` is good, ``"RoomHeat"`` is not kebab-case."""
        if isinstance(name, str) and cls.NAME_PATTERN.match(name):
            return []
        return [f"its NAME {name!r} is not kebab-case, for example 'room-heat-needs-receiver'"]

    @classmethod
    def requires_problems(cls, requires: object, own_code: str) -> List[str]:
        """Return what is wrong with a rule's ``REQUIRES``: not a frozenset, a malformed code, or the rule's own code.

        Whether each required code belongs to a live rule of the same or an earlier stage, and whether the
        requirements form a cycle, needs the other rules and is checked by the registry.

        Args:
            requires: The declared ``REQUIRES``.
            own_code: The rule's own code.

        Returns:
            One sentence per problem; empty for good requirements.
        """
        if not isinstance(requires, frozenset):
            return [f"its REQUIRES {requires!r} is not a frozenset of codes"]
        problems = [
            f"its REQUIRES names {code!r}, which is not a code of the form <PREFIX>-<nn>"
            for code in sorted(str(code) for code in requires)
            if not cls.CODE_PATTERN.match(code)
        ]
        if own_code in requires:
            problems.append(f"its REQUIRES names its own code {own_code}")
        return problems


class RuleDocstring:
    """The template every rule's docstring follows: four headings, in order, each followed by text.

    The headings are "What it checks", "Why", "Example of a fault" and "How to fix", each at the start of a line and
    followed by a colon. ``hisim setup-rules show`` prints the docstring, and every refusal points to that command,
    so the reader of a failed run finds the fix one command away.
    """

    #: The four headings, in the order they appear.
    HEADINGS: ClassVar[Tuple[str, ...]] = ("What it checks", "Why", "Example of a fault", "How to fix")

    #: A heading at the start of a line, followed by its colon.
    HEADING_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^(What it checks|Why|Example of a fault|How to fix):", re.MULTILINE)

    @classmethod
    def problems(cls, docstring: Optional[str]) -> List[str]:
        """Return what is wrong with a docstring against the template: a missing, repeated or misplaced heading, or one without text.

        Example: a docstring with "What it checks:", "Why:" and "How to fix:" but no "Example of a fault:" gives one
        problem naming the headings it has and the four it needs.

        Args:
            docstring: The rule class's docstring, ``None`` when it has none.

        Returns:
            One sentence per problem; empty for a docstring that follows the template.
        """
        text = inspect.cleandoc(docstring or "")
        found = list(cls.HEADING_PATTERN.finditer(text))
        headings = tuple(match.group(1) for match in found)
        if headings != cls.HEADINGS:
            return [f"its docstring has the headings {list(headings)}, not {list(cls.HEADINGS)} in this order, each once"]
        ends = [match.start() for match in found[1:]] + [len(text)]
        return [
            f"its docstring's heading '{match.group(1)}' is followed by no text"
            for match, end in zip(found, ends)
            if not text[match.end():end].strip()
        ]


class SetupRule(Generic[SubjectT]):
    """The base of every setup rule: one named check on one subject at one stage.

    A rule is a subclass with class-scoped declarations, a docstring in the template of ``RuleDocstring`` and one
    method, ``check``. Example::

        class RoomHeatNeedsReceiver(SetupRule[BuiltSystem]):
            '''What it checks: ... Why: ... Example of a fault: ... How to fix: ...'''

            CODE = "EP-06"
            NAME = "room-heat-needs-receiver"
            STAGE = RuleStage.BUILD
            ENFORCED = True
            REQUIRES = frozenset({"EP-05"})
            WAIVED_FOR = MappingProxyType({SetupKind.PLANT: "a plant has no heated zone"})

            def check(self, subject: BuiltSystem) -> Sequence[Fault]:
                ...

    The type argument names the subject the rule reads; its ``STAGE`` must be the rule's. ``ENFORCED`` says whether
    its faults refuse the run or are only logged. ``REQUIRES`` names the codes of the rules that must have found no
    fault for this rule's result to mean anything; the registry skips the rule, visibly, when one of them did.
    ``WAIVED_FOR`` maps each kind that does not run the rule to the reason, and is empty by default: a rule applies
    to every kind until it waives one. ``check`` is pure: it returns every fault it finds, raises nothing and keeps
    no state between calls. Every declaration is checked when the class is created, so a malformed rule fails at
    import with a ``TypeError`` naming it. A subclass that declares no ``CODE`` is a shared base of a family's rules,
    not a rule, and is not checked.
    """

    #: The rule's stable code, ``<PREFIX>-<nn>`` (``RuleCodes``).
    CODE: ClassVar[str]
    #: The rule's readable kebab-case name, unique across the package.
    NAME: ClassVar[str]
    #: When the rule runs, and with it the subject it reads.
    STAGE: ClassVar[RuleStage]
    #: Whether the rule's faults refuse the run (``True``) or are only logged (``False``).
    ENFORCED: ClassVar[bool]
    #: The codes of the rules that must have found no fault before this one runs.
    REQUIRES: ClassVar[FrozenSet[str]] = frozenset()
    #: The kinds that do not run the rule, each with the reason; read-only.
    WAIVED_FOR: ClassVar[Mapping[SetupKind, str]] = MappingProxyType({})

    #: The class attributes every rule declares on its own class.
    DECLARATIONS: ClassVar[Tuple[str, ...]] = ("CODE", "NAME", "STAGE", "ENFORCED")

    def __init_subclass__(cls) -> None:
        """Check a rule's declarations when its class is created, and refuse a malformed rule with a ``TypeError`` naming it."""
        super().__init_subclass__()
        if "CODE" not in cls.__dict__:
            return
        problems = cls.declaration_problems()
        if problems:
            raise TypeError(f"the setup rule {cls.__module__}.{cls.__qualname__} is declared wrongly: " + "; ".join(problems))

    @classmethod
    def declaration_problems(cls) -> List[str]:
        """Return every problem of the rule's declarations, docstring and subject, one sentence each.

        Returns:
            The problems, empty for a well-formed rule.
        """
        missing = [name for name in cls.DECLARATIONS if name not in cls.__dict__]
        if missing:
            return [f"it does not declare {', '.join(missing)} on its own class"]
        problems = RuleCodes.code_problems(cls.CODE, cls.__module__) + RuleCodes.name_problems(cls.NAME)
        # The declarations are read as objects: a malformed rule is exactly one whose values break their annotations.
        stage: object = cls.STAGE
        enforced: object = cls.ENFORCED
        if not isinstance(stage, RuleStage):
            problems.append(f"its STAGE {stage!r} is not a RuleStage")
        if not isinstance(enforced, bool):
            problems.append(f"its ENFORCED {enforced!r} is not a bool")
        problems += RuleCodes.requires_problems(cls.REQUIRES, cls.CODE)
        problems += cls.waiver_problems(cls.WAIVED_FOR)
        problems += RuleDocstring.problems(cls.__doc__)
        subject = cls.subject_type()
        if subject is None:
            problems.append("it names no subject: derive it from SetupRule[<a subject class of hisim/setup_rules/subjects.py>]")
        elif isinstance(stage, RuleStage) and subject.STAGE != stage:
            problems.append(f"its subject {subject.__name__} belongs to the stage {subject.STAGE.value}, not {stage.value}")
        return problems

    @staticmethod
    def waiver_problems(waived_for: Mapping[SetupKind, str]) -> List[str]:
        """Return what is wrong with a ``WAIVED_FOR`` mapping: a key that is not a ``SetupKind``, or an empty or blank reason.

        Nothing else is checked about a waiver: any rule may waive any kind, and the reason tells a reviewer what
        the waiver gives up. For example, ``{"demo": "a reason"}`` names a string, not ``SetupKind.DEMO``, and
        ``{SetupKind.DEMO: "  "}`` gives no reason.

        Args:
            waived_for: The declared mapping of kind to reason.

        Returns:
            One sentence per problem; empty for a well-formed mapping.
        """
        problems = []
        # Read as objects: a malformed waiver is exactly one whose keys or reasons break the annotation.
        entries: Iterable[Tuple[object, object]] = waived_for.items()
        for kind, reason in entries:
            if not isinstance(kind, SetupKind):
                problems.append(f"its WAIVED_FOR names {kind!r}, which is not a SetupKind")
            if not isinstance(reason, str) or not reason.strip():
                problems.append(f"its WAIVED_FOR gives {kind!r} no reason")
        return problems

    @classmethod
    def subject_type(cls) -> Optional[Type[RuleSubject]]:
        """Return the subject class the rule reads, the type argument of its ``SetupRule[...]`` base, or ``None`` when it names none."""
        for klass in cls.__mro__:
            for base in klass.__dict__.get("__orig_bases__", ()):
                if get_origin(base) is SetupRule:
                    (argument,) = get_args(base)
                    if isinstance(argument, type) and issubclass(argument, RuleSubject):
                        return argument
                    return None
        return None

    @classmethod
    def fault(
        cls,
        message: str,
        *,
        component: Optional[str] = None,
        port: Optional[str] = None,
        key_path: Tuple[KeyPathStep, ...] = (),
        file: Optional[str] = None,
    ) -> Fault:
        """Return a fault of this rule: its code, name and stage, the message ending in the pointer to the rule, and the location.

        Example: in ``EP-06``, ``cls.fault("buffer.HeatToHeatedZoneInWatt declares PRODUCTION of ROOM_HEAT and no
        component reads it", component="buffer", port="HeatToHeatedZoneInWatt")`` gives a fault whose message ends
        with ``; see: hisim setup-rules show EP-06``.

        Args:
            message: What is wrong, as the reader of a failed run needs it.
            component: The component's runtime name, or the entry's name in a file.
            port: The input or output name.
            key_path: The keys and indices from the file's top level to the value.
            file: The file's path.

        Returns:
            The fault.
        """
        return Fault(
            code=cls.CODE,
            name=cls.NAME,
            stage=cls.STAGE,
            message=f"{message}; {cls.pointer()}",
            component=component,
            port=port,
            key_path=key_path,
            file=file,
        )

    @classmethod
    def pointer(cls) -> str:
        """Return the pointer every refusal of this rule ends with, ``see: hisim setup-rules show EP-06``."""
        return f"see: hisim setup-rules show {cls.CODE}"

    def check(self, subject: SubjectT) -> Sequence[Fault]:
        """Return every fault the rule finds in the subject; raise nothing.

        Args:
            subject: The subject of the rule's stage.

        Returns:
            The faults, empty when the subject obeys the rule.
        """
        raise NotImplementedError(f"the setup rule {type(self).__qualname__} does not implement check")
