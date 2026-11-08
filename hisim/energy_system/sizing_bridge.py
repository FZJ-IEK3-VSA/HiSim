"""The bridge into the sizing kernel: what it is told, and what it did.

The sizing kernel resolves cross-component sizing for a whole scenario at once. It takes the
configurations, and — wherever a fact has more than one provider and the choice is the
author's — a mapping saying which provider each consumer reads. An energy-system file already
carries exactly that information, spread across the entries that read the facts, so this
module's first job is a plain translation: it collects the ``sizing_sources`` blocks into the
one mapping the kernel expects, inventing nothing and reordering nothing.

Its second job is the return direction. The kernel stops at its first failure, which it states
in prose; the bridge records that failure, with the component names a message is located by,
instead of raising it. The sizing rules of the setup rules (:mod:`hisim.setup_rules.sizing`)
classify it into the catalogue and refuse it with the file location that caused it, the
kernel's own sentence kept verbatim — it already names the candidate providers and prints the
block an author can paste, and no rewording could improve on that. The facts nobody read, which
are legal and sometimes deliberate, are reported by the same rules as warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from hisim.config.engine import SizingFactEngine
from hisim.config.laws import SizingError
from hisim.config.report import ResolutionReport
from hisim.energy_system.model import EnergySystemFile, SourceReference

#: One entry of the kernel's per-consumer sources mapping: a qualified reference or a list of
#: them. Spelled here so the bridge's signature says what it produces.
KernelSourceValue = Union[str, List[str]]


@dataclass(frozen=True)
class SizingOutcome:
    """What one run of the sizing kernel over a system did: the sized configurations, or the failure it stopped at.

    ``configs`` are the resolved configurations in input order, empty when the kernel failed; ``report`` is the
    kernel's report of the run, as far as it got; ``names`` are the component names in the order of the
    configurations, which a failure is located by; ``failure`` is the kernel's exception, ``None`` when every field
    was resolved. Example: a boiler alone gives no configurations and the failure ``'heating_load_in_watt' needed by
    'boiler' is provided by nobody ...``, which the sizing rule ``EF-4A`` refuses.
    """

    configs: Tuple[Any, ...]
    report: ResolutionReport
    names: Tuple[str, ...]
    failure: Optional[SizingError] = None


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


def resolve_sizing(configs: Sequence[Any], sources: Mapping[str, Mapping[str, KernelSourceValue]], names: Sequence[str]) -> SizingOutcome:
    """Runs the sizing kernel over the whole system and records what it resolved or the failure it stopped at.

    The engine is driven directly rather than through the module-level convenience function,
    because the report it fills is part of this stage's result: it is what a record writer
    turns into per-field provenance and what a discoverability command prints before a run.
    Nothing is raised for a failure of the kernel: the sizing rules refuse it, with every other
    fault of the stage.

    Args:
        configs: The built configurations, in file order.
        sources: The bridged ``sizing_sources`` mapping of the enabled set.
        names: The component names, in the same order as the configurations, used to name the
            file location of a failure that mentions one of them.

    Returns:
        The resolved configurations in input order and the report of the run, or the kernel's failure.
    """
    engine = SizingFactEngine(seed=None, sources=sources)
    try:
        resolved = engine.resolve_all(list(configs))
    except SizingError as error:
        return SizingOutcome(configs=(), report=engine.report, names=tuple(names), failure=error)
    return SizingOutcome(configs=tuple(resolved), report=engine.report, names=tuple(names))
