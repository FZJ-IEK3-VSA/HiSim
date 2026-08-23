"""The resolution report: a structured record of every decision one engine run makes.

The sizing-fact engine (:mod:`hisim.config.engine`) was designed error-first — anything
ambiguous fails hard — but between the happy path and the hard error lie decisions that
a reviewer or a bug hunter needs to *see*: which sweep of the fixed point resolved which
config, which source won each fact lookup (and by which rule), what every config
contributed, and where a file-level pre-seed overrode a computed value. This module
holds the typed records of those decisions plus the report that collects them.

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
    """How one fact lookup found its source, named after the rule that decided it.

    Plain class-scoped string constants rather than an Enum, like ``FactScope``: the
    values are report vocabulary, not wire format. ``PRESEED`` — a file-level
    ``sizing_facts`` entry won outright. ``CONNECTED_ADJACENT`` — the adjacency narrowed
    the declared providers to a direct neighbor. ``CONNECTED_POOL`` — no direct neighbor
    declares the fact (or no adjacency was given), so the flat-pool uniqueness rule
    applied. ``GLOBAL`` — the fact came from the seed context or a global contribution.
    """

    PRESEED: ClassVar[str] = "PRESEED"
    CONNECTED_ADJACENT: ClassVar[str] = "CONNECTED_ADJACENT"
    CONNECTED_POOL: ClassVar[str] = "CONNECTED_POOL"
    GLOBAL: ClassVar[str] = "GLOBAL"


@dataclass(frozen=True)
class FactLookupRecord:
    """One successful fact lookup: who read which fact from whom, and by which rule.

    Recorded only when the consumer actually resolves (speculative sweeps that still
    miss facts are not lookups, they are waiting). ``candidates`` names the declared
    providers that were in play before the mode narrowed them — when it has more than
    one entry, the mode explains why the choice was still unambiguous.
    """

    consumer: str
    fact: str
    source: str
    value: Any
    mode: str
    candidates: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ContributionRecord:
    """One fact a config contributed into the pool: producer, value and scope.

    Null values are recorded as-is — the set of facts a class contributes is static, so
    a boiler without DHW contributes its DHW fact as ``None`` rather than omitting it,
    and the record is where that deliberate null stays visible after the run.
    """

    producer: str
    fact: str
    value: Any
    scope: str


@dataclass(frozen=True)
class OverrideRecord:
    """A computed contribution that a file-level pre-seed shadowed.

    Pre-seeded facts win over contributions, but never silently: both values are kept
    so the audit can show what the scenario forced and what the config would have
    computed — the single most useful line when a forced value turns out wrong.
    """

    fact: str
    producer: str
    computed_value: Any
    preseeded_value: Any


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
    debugging questions: :attr:`sweeps` — *when* did each config resolve and what was
    it waiting for; :attr:`lookups` — *where* did each consumed fact come from and by
    which rule; :attr:`overrides` — *which* computed values did the file force aside.
    """

    sweeps: List[SweepRecord] = field(default_factory=list)
    lookups: List[FactLookupRecord] = field(default_factory=list)
    overrides: List[OverrideRecord] = field(default_factory=list)

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
                    f" by '{contribution.producer}' ({contribution.scope})"
                )
            for consumer, missing in sweep.waiting:
                lines.append(f"  waiting: '{consumer}' on {list(missing)}")
        for lookup in self.lookups:
            candidates = f" of candidates {sorted(lookup.candidates)}" if len(lookup.candidates) > 1 else ""
            lines.append(
                f"lookup: '{lookup.consumer}' read {lookup.fact}={lookup.value!r}"
                f" from '{lookup.source}' [{lookup.mode}]{candidates}"
            )
        for override in self.overrides:
            lines.append(
                f"override: sizing_facts forced {override.fact}={override.preseeded_value!r},"
                f" shadowing {override.computed_value!r} computed by '{override.producer}'"
            )
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
                            "scope": entry.scope,
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
            "overrides": [
                {
                    "fact": entry.fact,
                    "producer": entry.producer,
                    "computed_value": entry.computed_value,
                    "preseeded_value": entry.preseeded_value,
                }
                for entry in self.overrides
            ],
        }
