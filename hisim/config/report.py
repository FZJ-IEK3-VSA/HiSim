"""The resolution report: a structured record of every decision one engine run makes.

The sizing-fact engine (:mod:`hisim.config.engine`) was designed error-first — anything
ambiguous fails hard — but between the happy path and the hard error lie decisions that
a reviewer or a bug hunter needs to *see*: which sweep of the fixed point resolved which
config, which provider won each fact lookup and by which rule, which providers were
candidates, and what every config contributed. This module holds the typed records of
those decisions plus the report that collects them.

The report is pure data, filled by the engine as it runs and exposed as
``engine.report``: tests assert on it, :meth:`ResolutionReport.render` turns it into a
human-readable block for logs and error messages, and :meth:`ResolutionReport.to_dict`
serializes it for the audit artifact written next to simulation results. Like every
module of the ``hisim.config`` package it imports nothing from the rest of HiSim (the
package-wide ``hisim.log`` exception is not needed here — the report never logs, it
*is* the record).
"""

# clean

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Tuple


class LookupMode:
    """How one fact lookup found its provider, named after the rule that decided it.

    Plain class-scoped string constants rather than an Enum: the values are report
    vocabulary, not wire format. ``UNIQUE`` — exactly one config in the resolved set
    declares the fact, so the bare fact name bound without anything being written.
    ``EXPLICIT`` — the consumer's sources mapping named the provider. ``SEED`` — the fact
    came from the seed context, which participates in the binding as a provider named
    ``<seed>``.
    """

    UNIQUE: ClassVar[str] = "UNIQUE"
    EXPLICIT: ClassVar[str] = "EXPLICIT"
    SEED: ClassVar[str] = "SEED"


@dataclass(frozen=True)
class FactLookupRecord:
    """One successful fact lookup: who read which fact from whom, and by which rule.

    Recorded only when the consumer actually resolves (speculative sweeps that still
    miss facts are not lookups, they are waiting). ``candidates`` names every config in
    the resolved set that declares the fact, sorted, before the mode picked one of them —
    with more than one entry the mode is necessarily ``EXPLICIT``, which is what makes a
    finished run show *why* a multi-provider fact was unambiguous.
    """

    consumer: str
    fact: str
    source: str
    value: Any
    mode: str
    candidates: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ContributionRecord:
    """One fact a config contributed into the provider pool: producer, fact and value.

    Null values are recorded as-is — the set of facts a class contributes is static, so
    a boiler without DHW contributes its DHW fact as ``None`` rather than omitting it,
    and the record is where that deliberate null stays visible after the run.
    """

    producer: str
    fact: str
    value: Any


@dataclass(frozen=True)
class SweepRecord:
    """One sweep of the fixed point: what resolved, what contributed, who still waits.

    ``waiting`` maps each still-unresolved config key to the facts it was missing at
    the end of the sweep — the trajectory of these entries across sweeps is the
    dependency structure of the scenario, made visible.
    """

    number: int
    resolved: Tuple[str, ...]
    contributed: Tuple[ContributionRecord, ...]
    waiting: Tuple[Tuple[str, Tuple[str, ...]], ...]


@dataclass
class ResolutionReport:
    """Everything one engine run decided, in the order it was decided.

    Filled by :class:`hisim.config.engine.SizingFactEngine` during ``resolve_all`` and
    readable afterwards as ``engine.report``. The three views answer the three
    debugging questions: :attr:`sweeps` — *when* did each config resolve and what was it
    waiting for; :attr:`lookups` — *where* did each consumed fact come from, by which
    rule and against which candidates; :attr:`unconsumed` — which contributed facts
    nobody read, the one non-error condition worth warning about (a provider added for
    nothing, or a consumer bound elsewhere than its author assumed).
    """

    sweeps: List[SweepRecord] = field(default_factory=list)
    lookups: List[FactLookupRecord] = field(default_factory=list)
    unconsumed: List[Tuple[str, str]] = field(default_factory=list)

    def render(self) -> str:
        """Renders the whole run as an indented human-readable block.

        Used verbatim in the engine's no-progress error (so a deadlock message shows
        how far resolution got before it stuck) and suitable for dumping into a log at
        debug level.
        """
        lines: List[str] = []
        for sweep in self.sweeps:
            lines.append(f"sweep {sweep.number}: resolved {', '.join(sweep.resolved) or '<nothing>'}")
            for contribution in sweep.contributed:
                lines.append(
                    f"  contributed {contribution.fact}={contribution.value!r}"
                    f" by '{contribution.producer}'"
                )
            for consumer, missing in sweep.waiting:
                lines.append(f"  waiting: '{consumer}' on {list(missing)}")
        for lookup in self.lookups:
            candidates = f" of candidates {sorted(lookup.candidates)}" if len(lookup.candidates) > 1 else ""
            lines.append(
                f"lookup: '{lookup.consumer}' read {lookup.fact}={lookup.value!r}"
                f" from '{lookup.source}' [{lookup.mode}]{candidates}"
            )
        for producer, fact in self.unconsumed:
            lines.append(f"unconsumed: '{producer}' provided {fact}, which nobody read")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the report for the audit artifact in the results directory.

        Values are emitted as-is: they are sizing facts (numbers, enum members, strings),
        and the artifact writer owns any further encoding, exactly as it does for the
        ``sizing_record`` entries it emits alongside this.
        """
        return {
            "sweeps": [
                {
                    "number": sweep.number,
                    "resolved": list(sweep.resolved),
                    "contributed": [
                        {
                            "producer": entry.producer,
                            "fact": entry.fact,
                            "value": entry.value,
                        }
                        for entry in sweep.contributed
                    ],
                    "waiting": {consumer: list(missing) for consumer, missing in sweep.waiting},
                }
                for sweep in self.sweeps
            ],
            "lookups": [
                {
                    "consumer": entry.consumer,
                    "fact": entry.fact,
                    "source": entry.source,
                    "value": entry.value,
                    "mode": entry.mode,
                    "candidates": list(entry.candidates),
                }
                for entry in self.lookups
            ],
            "unconsumed": [
                {"producer": producer, "fact": fact} for producer, fact in self.unconsumed
            ],
        }
