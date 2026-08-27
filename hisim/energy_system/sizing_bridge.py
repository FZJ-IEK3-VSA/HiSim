"""The bridge into the sizing kernel: what it is told, and how its refusals are reported.

The sizing kernel resolves cross-component sizing for a whole scenario at once. It takes the
configurations, and — wherever a fact has more than one provider and the choice is the
author's — a mapping saying which provider each consumer reads. An energy-system file already
carries exactly that information, spread across the entries that read the facts, so this
module's first job is a plain translation: it collects the ``sizing_sources`` blocks into the
one mapping the kernel expects, inventing nothing and reordering nothing.

Its second job is the return direction. The kernel distinguishes eight failure modes and
spells the difference between them only in prose, which is right for a Python setup where the
message is read by whoever wrote the setup, but too coarse for a file: a caller wants to know
which condition it hit without parsing English, and an author wants to be told which line of
which entry to edit. So each kernel message is classified into the catalogue and re-raised
with the file location that caused it, the kernel's own sentence kept verbatim — it already
names the candidate providers and prints the block an author can paste, and no rewording here
could improve on that.

The one non-error outcome the kernel reports is a fact that nobody read. That is legal and
sometimes deliberate, so it comes back as a warning line rather than as a refusal.
"""

# clean

from __future__ import annotations

import enum
import re
from typing import Any, Dict, List, Mapping, Sequence, Tuple, Union

from hisim.config.engine import SizingFactEngine
from hisim.config.laws import SizingError
from hisim.config.report import ResolutionReport
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemSizingError
from hisim.energy_system.model import EnergySystemFile, SourceReference

#: One entry of the kernel's per-consumer sources mapping: a qualified reference or a list of
#: them. Spelled here so the bridge's signature says what it produces.
KernelSourceValue = Union[str, List[str]]


@enum.unique
class KernelFailure(enum.Enum):
    """The eight failure modes of the sizing kernel, recognized by their message.

    The kernel raises one exception type for every binding problem and distinguishes the cases
    only in prose, which is right for a Python setup — the message *is* the diagnosis — but too
    coarse for a file, where a caller wants to know which of the eight it hit without reading
    English. Each member pairs the catalogue identifier of the wrapped condition with the
    fragments of the kernel's own message that identify it uniquely.

    Recognition is by substring rather than by a code the kernel carries, so the members are
    ordered from the most specific message to the least and matched in that order; the tests
    provoke all eight through the real kernel, which is what keeps this table honest when a
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
    AMBIGUOUS_MANY = (EnergySystemErrorId.SIZING_AMBIGUOUS, ("is read many-fold by",))

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
class _ConsumerPhrasing(enum.Enum):
    """The ways a sizing-kernel message names the configuration that could not be sized.

    The kernel writes for a human reading a traceback, so the consumer appears in prose rather
    than in a field, and it appears differently depending on which of the eight conditions was
    hit. Collecting the phrasings in one ordered table is what lets the wrapper point an author
    at the right entry of the file instead of at whichever component the message mentions first.
    """

    NEEDED_BY = re.compile(r"needed by '([^']+)'")
    MANY_FOLD = re.compile(r"read many-fold by '([^']+)'")
    SOURCES_KEY = re.compile(r"sources\['([^']+)'\]")
    CANNOT_SIZE = re.compile(r"'([^']+)' cannot size from it")
    DUPLICATE = re.compile(r"two configs named '([^']+)'")

    @property
    def expression(self) -> "re.Pattern[str]":
        """The compiled pattern whose first group is the consumer's name.

        Returns:
            The regular expression this phrasing is recognized by.
        """
        return self.value


def sizing_sources_bridge(model: EnergySystemFile) -> Dict[str, Dict[str, KernelSourceValue]]:
    """Renders every entry's ``sizing_sources`` block in the shape the sizing kernel reads.

    The kernel takes one mapping for the whole system — consumer, then fact, then the qualified
    reference or the list of them — while the file keeps each block on the entry that reads the
    fact, which is the direction the format insists on. This function is the bridge, and it is
    deliberately a plain translation: nothing is invented, dropped or reordered, so a fact that
    resolves does so because the file said it should.

    The mapping is built from the components the model holds, so passing an expanded file — the
    normal case — yields the enabled set and nothing else, which is what makes the kernel's
    uniqueness rule an enabled-set rule.

    Args:
        model: The energy system, normally after group expansion.

    Returns:
        Consumer name to fact mapping; a consumer with no sizing sources is absent rather than
        present with an empty mapping.
    """
    bridge: Dict[str, Dict[str, KernelSourceValue]] = {}
    for name, entry in model.all_components().items():
        if not entry.sizing_sources:
            continue
        per_fact: Dict[str, KernelSourceValue] = {}
        for fact, value in entry.sizing_sources.items():
            if isinstance(value, SourceReference):
                per_fact[fact] = value.text
            else:
                per_fact[fact] = [reference.text for reference in value]
        bridge[name] = per_fact
    return bridge


def unconsumed_warnings(report: ResolutionReport) -> Tuple[str, ...]:
    """Renders the facts nobody read as one warning line each.

    A component that computes a fact no other component consumes is legal and sometimes
    deliberate — a provider added ahead of its consumer, a group switched off — but it is also
    what a mis-spelled source line looks like from the outside, so a run says it out loud
    instead of staying silent. It is emphatically not an error: nothing about the system is
    wrong, and stopping the run would make an author delete a component they will need again.

    Args:
        report: The report of the sizing run that just finished.

    Returns:
        One line per unconsumed fact, sorted as the report sorted them.
    """
    return tuple(
        f"'{producer}' provides '{fact}', which no component of this energy system reads."
        for producer, fact in report.unconsumed
    )


def resolve_sizing(
    configs: Sequence[Any],
    sources: Mapping[str, Mapping[str, KernelSourceValue]],
    names: Sequence[str],
) -> Tuple[List[Any], ResolutionReport]:
    """Runs the sizing kernel over the whole system and wraps whatever it raises.

    The engine is driven directly rather than through the module-level convenience function,
    because the report it fills is part of this stage's result: it is what a record writer
    turns into per-field provenance and what a discoverability command prints before a run.

    Args:
        configs: The built configurations, in file order.
        sources: The bridged ``sizing_sources`` mapping of the enabled set.
        names: The component names, in the same order as the configurations, used to name the
            file location of a failure that mentions one of them.

    Returns:
        The resolved configurations in input order, and the report of the run.

    Raises:
        EnergySystemSizingError: For every kernel failure, classified into ``EF-4A`` …
            ``EF-4H`` and carrying the kernel's own message.
    """
    engine = SizingFactEngine(seed=None, sources=sources)
    try:
        resolved = engine.resolve_all(list(configs))
    except NotImplementedError as error:
        raise EnergySystemSizingError(
            EnergySystemErrorId.SIZING_MANY_UNSUPPORTED,
            "components",
            f"the sizing kernel cannot evaluate a many-cardinality read yet: {error}",
        ) from error
    except SizingError as error:
        message = str(error)
        raise EnergySystemSizingError(
            KernelFailure.classify(message),
            _failure_location(message, names),
            message,
        ) from error
    return resolved, engine.report


def _failure_location(message: str, names: Sequence[str]) -> str:
    """Names the entry a kernel failure is about, by reading the consumer out of its message.

    Every kernel message spells the consuming configuration in one of a handful of fixed
    phrasings, and the consumer — not the provider it was pointed at — is the entry whose lines
    an author has to edit. The phrasings are tried first and the plain "first name mentioned"
    rule only as a fallback, because a message that names both ends would otherwise attribute
    the failure to whichever end happens to come first in the file.

    Args:
        message: The kernel exception's message.
        names: The component names of the system, in file order.

    Returns:
        A dotted key path into the document; the components block as a whole when the message
        names nobody, which is what a deadlock spanning several entries looks like.
    """
    for pattern in _ConsumerPhrasing:
        match = pattern.expression.search(message)
        if match and match.group(1) in names:
            return f"components.{match.group(1)}"
    for name in names:
        if f"'{name}'" in message:
            return f"components.{name}"
    return "components"
