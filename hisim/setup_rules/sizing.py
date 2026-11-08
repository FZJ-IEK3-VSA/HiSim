"""The sizing rules of an energy-system file: the refusal of the sizing kernel, classified, and the facts nobody reads.

Declarative mode only, stage ``BUILD``, subject ``ConfiguredDocument``: the file with its configurations built and the
outcome of one run of the sizing kernel over them (``hisim/energy_system/sizing_bridge.py:resolve_sizing``). The
kernel resolves the whole system at once and stops at its first failure, which it states in prose, naming the
candidate providers and printing the block an author can paste; the bridge records that failure instead of raising
it. The rules here classify it (``KernelFailure``): each rule ``EF-4A`` to ``EF-4J`` refuses the failure whose
message is its condition, and ``EF-4X`` one that matches none of them, a deadlock for instance. Every fault keeps the
catalogue message the sizing error carried before the checks moved, ``EF-4n at components.<consumer>: <the kernel's
message>``, located at the consumer whose lines an author edits, and the registry raises it as
``EnergySystemSizingError``. A failure is one fault, since the kernel reports one.

Every sizing rule requires the binding rules of the configured file, ``EF-1A`` and ``EF-04``: an entry whose
configuration could not be built leaves nothing to size, and the kernel does not run. The fact nobody reads,
``EF-4K``, only logs; it requires the rules of the kernel's failure, since a failed run reports no unread facts.
"""

from __future__ import annotations

import enum
import re
from typing import ClassVar, FrozenSet, Optional, Sequence, Tuple

from hisim.config.report import ResolutionReport
from hisim.config.sizing import SizedTogether
from hisim.energy_system.errors import EnergySystemCatalogueError, EnergySystemErrorId, EnergySystemSizingError
from hisim.setup_rules.bindings import CatalogueFaults
from hisim.setup_rules.rule import Fault, RuleStage, SetupRule
from hisim.setup_rules.subjects import ConfiguredDocument


@enum.unique
class KernelFailure(enum.Enum):
    """The failure modes of the sizing kernel, recognized by their message.

    The kernel raises one exception type for every binding problem and distinguishes the cases
    only in prose, which is right for a Python setup — the message *is* the diagnosis — but too
    coarse for a file, where a caller wants to know which one it hit without reading
    English. Each member pairs the catalogue identifier of the wrapped condition with the
    fragments of the kernel's own message that identify it uniquely.

    Recognition is by substring rather than by a code the kernel carries, so the members are
    ordered from the most specific message to the least and matched in that order; the tests
    provoke all of them through the real kernel, which is what keeps this table honest when a
    kernel message is reworded.
    """

    DUPLICATE_NAME = (EnergySystemErrorId.SIZING_DUPLICATE_NAME, ("two configs named",))
    MISSING_NAME = (EnergySystemErrorId.SIZING_DUPLICATE_NAME, ("has no component_id.name",))
    UNPROVIDED = (EnergySystemErrorId.SIZING_UNPROVIDED, ("is provided by nobody",))
    NULL_VALUE = (EnergySystemErrorId.SIZING_NULL_VALUE, ("provided as null by",))
    NOT_A_PROVIDER = (EnergySystemErrorId.SIZING_NOT_A_PROVIDER, ("does not declare",))
    SHAPE_MISMATCH = (EnergySystemErrorId.SIZING_SHAPE_MISMATCH, ("reference(s) of the form",))
    FIELD_CYCLE = (EnergySystemErrorId.SIZING_FIELD_CYCLE, ("via Self",))
    AMBIGUOUS = (EnergySystemErrorId.SIZING_AMBIGUOUS, ("is provided by", "sources="))
    MANY_READ = (EnergySystemErrorId.SIZING_MANY_READ, ("is read many-fold by",))
    STATED_IN_PART = (EnergySystemErrorId.SIZING_STATED_IN_PART, (SizedTogether.MARKER,))

    def __init__(self, error_id: EnergySystemErrorId, markers: Tuple[str, ...]) -> None:
        """Stores the identifier and the message fragments of one failure mode.

        Args:
            error_id: The catalogue identifier this kernel condition is reported as.
            markers: Fragments that all have to appear in the kernel's message.
        """
        self.error_id = error_id
        self.markers = markers

    @classmethod
    def classify(cls, message: str) -> EnergySystemErrorId:
        """Decides which catalogue identifier one kernel message corresponds to.

        Example: ``"'heating_load_in_watt' needed by 'boiler' is provided by nobody ..."`` gives ``EF-4A``.

        Args:
            message: The kernel exception's message.

        Returns:
            The matching identifier, or the catch-all ``EF-4X`` when the message matches none
            — a deadlock report, for instance, which is a genuine kernel failure without a
            single offending line.
        """
        for failure in cls:
            if all(marker in message for marker in failure.markers):
                return failure.error_id
        return EnergySystemErrorId.SIZING_FAILED


@enum.unique
class ConsumerPhrasing(enum.Enum):
    """The ways a sizing-kernel message names the configuration that could not be sized.

    The kernel writes for a human reading a traceback, so the consumer appears in prose rather
    than in a field, and it appears differently depending on which condition was
    hit. Collecting the phrasings in one ordered table is what lets a fault point an author
    at the right entry of the file instead of at whichever component the message mentions first.
    """

    NEEDED_BY = re.compile(r"needed by '([^']+)'")
    MANY_FOLD = re.compile(r"read many-fold by '([^']+)'")
    SOURCES_KEY = re.compile(r"sources\['([^']+)'\]")
    CANNOT_SIZE = re.compile(r"'([^']+)' cannot size from it")
    DUPLICATE = re.compile(r"two configs named '([^']+)'")
    STATED_IN_PART = re.compile(r"^\w+ '([^']+)': .* " + SizedTogether.MARKER)

    @property
    def expression(self) -> "re.Pattern[str]":
        """The compiled pattern whose first group is the consumer's name."""
        return self.value

    @classmethod
    def location(cls, message: str, names: Sequence[str]) -> str:
        """Names the entry a kernel failure is about, by reading the consumer out of its message.

        Every kernel message spells the consuming configuration in one of a handful of fixed
        phrasings, and the consumer — not the provider it was pointed at — is the entry whose lines
        an author has to edit. The phrasings are tried first and the plain "first name mentioned"
        rule only as a fallback, because a message that names both ends would otherwise attribute
        the failure to whichever end happens to come first in the file. Example: ``"... needed by
        'boiler' is provided by nobody"`` of a system holding ``boiler`` gives ``components.boiler``.

        Args:
            message: The kernel exception's message.
            names: The component names of the system, in file order.

        Returns:
            A dotted key path into the document; the components block as a whole when the message
            names nobody, which is what a deadlock spanning several entries looks like.
        """
        for pattern in cls:
            match = pattern.expression.search(message)
            if match and match.group(1) in names:
                return f"components.{match.group(1)}"
        for name in names:
            if f"'{name}'" in message:
                return f"components.{name}"
        return "components"


class SizingRule(SetupRule[ConfiguredDocument]):
    """The shared base of the rules of the kernel's failure: each refuses the failure that its condition classifies.

    It declares no ``CODE``, so it is no rule itself. Its rules raise ``EnergySystemSizingError`` and require the
    binding rules of the configured file, since an entry they refuse leaves the kernel nothing to run on.
    """

    VIOLATION = EnergySystemSizingError
    REQUIRES: ClassVar[FrozenSet[str]] = frozenset({"EF-1A", "EF-04"})

    def check(self, subject: ConfiguredDocument) -> Sequence[Fault]:
        """Return the fault of the kernel's failure when the failure is this rule's condition, located at its consumer."""
        sizing = subject.outcome.sizing
        if sizing is None or sizing.failure is None:
            return []
        message = str(sizing.failure)
        error_id = KernelFailure.classify(message)
        if error_id.value != self.CODE:
            return []
        location = ConsumerPhrasing.location(message, sizing.names)
        catalogue_message = EnergySystemCatalogueError.build_message(error_id, location, message)
        return [CatalogueFaults.fault(type(self), catalogue_message, location, subject.file, subject.source_map)]


class FactProvided(SizingRule):
    """What it checks: every fact a sizing law reads is provided by some component of the system.

    Why: a law with nothing to size from would leave its field ``AUTO``, and the sentinel would travel into the
    component and fail there, naming a field rather than the component missing from the file.

    Example of a fault: ``EF-4A at components.boiler: 'heating_load_in_watt' needed by 'boiler' is provided by nobody
    ...``.

    How to fix: add the component that provides the fact (a building provides the heating load), or state the field
    the law would size.
    """

    CODE = "EF-4A"
    NAME = "fact-provided"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class ProviderUnambiguous(SizingRule):
    """What it checks: a fact several components provide is read from the one a ``sizing_sources`` line names.

    Why: which of two houses a boiler is sized for is the author's decision, not the machine's; the kernel names the
    candidates and prints the block to paste.

    Example of a fault: ``EF-4B at components.boiler: 'heating_load_in_watt' needed by 'boiler' is provided by
    house_a, house_b; pass sources={'boiler': {'heating_load_in_watt': '<one of house_a.heating_load_in_watt or
    house_b.heating_load_in_watt>'}}``.

    How to fix: paste the block the message prints into the consumer's entry and keep the provider meant.
    """

    CODE = "EF-4B"
    NAME = "provider-unambiguous"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class SourceIsAProvider(SizingRule):
    """What it checks: a ``sizing_sources`` line names a component that declares the fact.

    Why: the component and the fact both exist, so nothing before the kernel sees the mistake; a plausible but wrong
    provider would otherwise be accepted and ignored.

    Example of a fault: ``EF-4C at components.boiler: sources['boiler']['heating_load_in_watt'] =
    'hds.heating_load_in_watt' but 'hds' does not declare 'heating_load_in_watt' (declares: ...)``.

    How to fix: name a component that provides the fact, as the message lists them.
    """

    CODE = "EF-4C"
    NAME = "source-is-a-provider"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class FactNotNull(SizingRule):
    """What it checks: the provider a fact is read from gives a value, not null.

    Why: a provider whose feature is switched off contributes nothing, and sizing from it would invent a number.

    Example of a fault: ``EF-4D at components.consumer: 'maximal_thermal_power_in_watt' provided as null by
    'provider' (feature off); 'consumer' cannot size from it.``.

    How to fix: read the fact from a provider that has it, or switch the provider's feature on.
    """

    CODE = "EF-4D"
    NAME = "fact-not-null"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class SourceShape(SizingRule):
    """What it checks: a ``sizing_sources`` value has the shape the law's cardinality reads: one reference for a fact read once, a list for a fact read many-fold.

    Why: a list means "aggregate over these"; a law that reads one value cannot aggregate, and taking the first
    element would make the file's meaning depend on its order.

    Example of a fault: ``EF-4E at components.boiler: sources['boiler']['heating_load_in_watt']: expected one
    reference(s) of the form '<name>.<fact>', got []; ...``.

    How to fix: write the shape the message asks for.
    """

    CODE = "EF-4E"
    NAME = "source-shape"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class NoFieldCycle(SizingRule):
    """What it checks: the sizable fields of one configuration that read each other via ``Self(...)`` form no cycle.

    Why: a field reading a sibling that reads it back can never be resolved; the condition is about one class, not
    the file's wiring.

    Example of a fault: ``EF-4F at components: _CyclicConfig: fields first and second read each other via Self``.

    How to fix: state one of the fields in the entry's ``config`` block, which breaks the cycle; a class whose laws
    always cycle needs its laws fixed.
    """

    CODE = "EF-4F"
    NAME = "no-field-cycle"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class OneReadShape(SizingRule):
    """What it checks: a fact is read many-fold or once, not both, and a many-fold read lists its providers, none twice and not none.

    Why: ``Sum(Many(...))`` reads every provider its sources list names; an empty list, which a group switched off may
    leave, would sum to a plausible zero.

    Example of a fault: ``EF-4G at components.aggregator: 'maximal_thermal_power_in_watt' is read many-fold by
    'aggregator', and its providers are none; a many read sums every provider once, and at least one``.

    How to fix: list the providers the consumer aggregates over, each once.
    """

    CODE = "EF-4G"
    NAME = "one-read-shape"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class UniqueInstance(SizingRule):
    """What it checks: every configuration the kernel sizes has an instance name, and no two share one.

    Why: sizing addresses every provider and consumer by its instance name; a duplicate makes every reference to it
    ambiguous. In a file the names are entry keys and cannot collide, which is why the condition is still checked.

    Example of a fault: ``EF-4H at components.twin: two configs named 'twin' (_PowerReaderConfig,
    _PowerReaderConfig); sizing addresses every provider and consumer by its instance name.``.

    How to fix: give every component its own name.
    """

    CODE = "EF-4H"
    NAME = "unique-instance"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class SizedTogetherWhole(SizingRule):
    """What it checks: a group of fields its class sizes together (``SIZED_TOGETHER``) is stated whole or not at all.

    Why: the fields of such a group belong together, as a battery's capacity and its inverter power do; stating one
    and leaving the law to size the other from a different fact gives a device nobody designed.

    Example of a fault: ``EF-4J at components.battery: BatteryConfig 'battery': custom_battery_capacity_generic_in_kilowatt_hour
    and custom_pv_inverter_power_generic_in_watt are sized together, but ... is stated and ... is left AUTO; state
    both or neither.``.

    How to fix: state every field of the group, or none.
    """

    CODE = "EF-4J"
    NAME = "sized-together"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class Resolved(SizingRule):
    """What it checks: the sizing kernel resolved every field, failing in none of the conditions it names.

    Why: a kernel failure that matches no condition of its own, a deadlock between laws or a law that raised, is
    still a failure; reporting it as such, with the kernel's own message, keeps it from being misread as one of the
    named conditions.

    Example of a fault: ``EF-4X at components: sizing made no further progress; the remaining configs wait on each
    other or on facts nobody computed: ...``.

    How to fix: read the kernel's message; it names the fields that wait and the facts they wait for.
    """

    CODE = "EF-4X"
    NAME = "resolved"
    STAGE = RuleStage.BUILD
    ENFORCED = True


class UnconsumedFact(SetupRule[ConfiguredDocument]):
    """What it checks: every fact a component provides is read by some component; one nobody reads is reported in the log.

    Why: a provider nobody reads is legal and sometimes deliberate (a provider added before its consumer, a group
    switched off), but it is also what a misspelled source line looks like from outside, so a run says it.

    Example of a fault: ``'building' provides 'conditioned_floor_area_in_m2', which no component of this energy
    system reads.``.

    How to fix: add the consumer, or correct the source line meant to read it; leave it when nothing is meant to.
    """

    CODE = "EF-4K"
    NAME = "unconsumed-fact"
    STAGE = RuleStage.BUILD
    ENFORCED = False
    REQUIRES: ClassVar[FrozenSet[str]] = frozenset({"EF-4A", "EF-4B", "EF-4C", "EF-4D", "EF-4E", "EF-4F", "EF-4G", "EF-4H", "EF-4J", "EF-4X"})

    def check(self, subject: ConfiguredDocument) -> Sequence[Fault]:
        """Return a fault per fact a component provides and no component reads, in the order of the sizing report."""
        sizing = subject.outcome.sizing
        if sizing is None or sizing.failure is not None:
            return []
        return [
            self.fault(self.message_for(producer, fact), component=producer, key_path=("components", producer), file=subject.file)
            for producer, fact in sizing.report.unconsumed
        ]

    @staticmethod
    def message_for(producer: str, fact: str) -> str:
        """Return the warning about one fact nobody reads, ``'building' provides 'conditioned_floor_area_in_m2', which ...``."""
        return f"'{producer}' provides '{fact}', which no component of this energy system reads."

    @classmethod
    def lines(cls, report: Optional[ResolutionReport]) -> Tuple[str, ...]:
        """Return the warning of every fact nobody read in a sizing run, in the report's order, as the run record keeps them.

        Example: a building whose floor area no component reads gives one line naming ``'building'`` and
        ``'conditioned_floor_area_in_m2'``; ``None`` (no sizing ran) gives none.
        """
        if report is None:
            return ()
        return tuple(cls.message_for(producer, fact) for producer, fact in report.unconsumed)
