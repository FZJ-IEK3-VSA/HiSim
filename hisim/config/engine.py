"""The sizing-fact engine: cross-component sizing resolved to a fixed point over configs.

Implements §8.4 of ``system_docs/config_defaults_spec.md``. Cross-component sizing
dependencies nest deeply (building → HDS controller → HDS; building → boiler → boiler
controller), and the incumbent mechanism — the global ``SingletonSimRepository`` with
untyped enum-keyed entries and silent ``entry_exists`` fallbacks — has already rotted
silently in production (dead keys, fallback-only reads). This engine is its typed,
hard-erroring successor for the construction-time half, in three phases:

1. **Registration** — every config's sizing *inputs* come from its laws' declared
   ``facts_read``; its *outputs* are the :class:`FactContribution` declarations on the
   config class. Output fact names are static per class; values are computed later and
   may legitimately be ``None`` when a feature is off.
2. **Graph validation** — a fact nobody provides, and a dependency cycle, are precise
   hard errors raised before anything is computed.
3. **Fixed-point resolution** — iterate over the unresolved configs, resolving every one
   whose facts are all visible and folding its contributions into the fact pool, until
   none remain. Components are constructed afterwards, from fully concrete configs.

Fact scoping (spec §8.4, decided 2026-08-19, refined 2026-08-20): scope-global facts
(building physics) live in a flat pool where a double contribution is a hard error;
sibling facts (``scope=FactScope.CONNECTED``) resolve as a **hybrid**. Direct adjacency
is consulted first when the v2 executor passes the scenario's parsed connections — this
is what keeps two boilers in one scenario unambiguous — and when *no direct neighbor
declares* the fact, the lookup falls back to the flat-pool rule that governs the
no-adjacency mode (Python setups): exactly one declared provider in the whole pool wins,
two or more are a hard error naming the providers and the consumer. The with-adjacency
behavior is thereby a strict refinement of the without-adjacency behavior instead of a
different rule, so a consumer two wiring hops away from its only provider (the battery
sizing from the PV through the EMS) still resolves. Providership is decided from the
*declared* contributions, never from what happens to be computed yet, so the outcome is
independent of resolution order. Anything genuinely ambiguous fails hard naming the
components involved — the engine never guesses.

The engine lives in its own module of the ``hisim.config`` package so that the
machinery (:mod:`hisim.config.sizing`), the engine, and its consumers (the v2 executor,
setups via :func:`resolve_all`) stay separately reviewable. Like every module of the
package it imports nothing from the rest of HiSim.
"""

# clean

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from hisim.config import sizing
from hisim.config.context import SizingContext
from hisim.config.laws import ConfigSizingError, SizingError, SizingLaw


class FactScope:
    """How a contributed fact is looked up by its consumers.

    ``GLOBAL`` facts describe the scope itself (building physics) and live in a flat
    per-scope pool — contributing the same global fact twice is a hard error.
    ``CONNECTED`` facts describe one component (a boiler's power band) and are resolved
    along the connection graph where one is available, so that two boilers in one
    scenario stay unambiguous. Plain class-scoped string constants rather than an Enum,
    because the values never travel through a file.
    """

    GLOBAL: ClassVar[str] = "GLOBAL"
    CONNECTED: ClassVar[str] = "CONNECTED"


@dataclass(frozen=True)
class FactContribution:
    """One declared output of a config class: which facts it computes, from what.

    The ``facts`` names are **static per class** (spec §8.4): the set never depends on
    resolution, only the values do — ``compute`` may return ``None`` for a fact whose
    feature is off, and a consumer reading such a null fact fails hard with a
    "provided as null by X" attribution. ``compute`` receives the (by then fully
    resolved) config and the context view and must return exactly the declared keys.
    """

    facts: Tuple[str, ...]
    compute: Callable[[Any, SizingContext], Mapping[str, Any]]
    reads: Tuple[str, ...] = ()
    scope: str = FactScope.GLOBAL

    def __post_init__(self) -> None:
        """Validates the declared fact names against the SizingContext registry.

        Every engine fact must be a ``SizingContext`` field, so laws can read it and the
        single-registry invariant of spec §4.1 extends to contributions.
        """
        known = {field.name for field in dataclasses.fields(SizingContext)}
        unknown = [name for name in self.facts if name not in known]
        if unknown:
            raise SizingError(
                f"FactContribution declares unknown fact(s) {unknown}; every fact must be "
                "a SizingContext field (add the field and its Size term first)."
            )


#: Class attribute under which a config class declares its contributions.
CONTRIBUTIONS_ATTRIBUTE = "SIZING_CONTRIBUTIONS"


@dataclass
class _Node:
    """The engine's per-config bookkeeping: identity, needs, offers, and progress state."""

    key: str
    config: Any
    needed_facts: Tuple[str, ...]
    contributions: Tuple[FactContribution, ...]
    resolved: bool = False
    contributed: bool = False


class SizingFactEngine:
    """Registration, validation and fixed-point resolution over one scenario's configs.

    Instantiate per resolution run (the engine is stateful bookkeeping, never shared),
    seed it with a :class:`SizingContext` and optionally the connection adjacency, then
    call :meth:`resolve_all`. The audit-facing result is :attr:`resolution_order` plus
    each resolved config's own ``sizing_record``.
    """

    def __init__(
        self,
        seed: Optional[SizingContext] = None,
        adjacency: Optional[Mapping[str, Set[str]]] = None,
        preseeded_facts: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Prepares an engine run.

        Args:
            seed: Starting facts (a setup's ``SizingContext.for_building`` result, for
                example); ``None`` means an empty context.
            adjacency: Component key → set of connected component keys, from the parsed
                scenario connections. When given, ``CONNECTED``-scoped facts resolve
                along it; when absent (Python setups), the flat-pool uniqueness rule
                applies to them too.
            preseeded_facts: File-level ``sizing_facts`` overrides; they win over any
                contribution, loudly (recorded in the audit trail).
        """
        base = seed if seed is not None else SizingContext()
        self._global_facts: Dict[str, Tuple[str, Any]] = {
            field.name: ("<seed>", getattr(base, field.name))
            for field in dataclasses.fields(SizingContext)
            if getattr(base, field.name) is not None
        }
        self._preseeded: Dict[str, Any] = dict(preseeded_facts or {})
        for fact, value in self._preseeded.items():
            self._global_facts[fact] = ("<sizing_facts>", value)
        self._connected_facts: Dict[str, Dict[str, Any]] = {}  # producer key -> {fact: value}
        self._declared_connected_providers: Dict[str, Set[str]] = {}  # fact -> declaring keys
        self._adjacency = {key: set(neighbors) for key, neighbors in (adjacency or {}).items()}
        self.resolution_order: List[str] = []

    # ------------------------------------------------------------------ registration

    @staticmethod
    def _needed_facts(config: Any) -> Tuple[str, ...]:
        """Collects the facts a config's unresolved fields will read, from their laws."""
        laws = sizing.sizable_fields(type(config))
        needed: List[str] = []
        for field_name in sizing.auto_fields(config):
            value = getattr(config, field_name)
            effective = value if isinstance(value, SizingLaw) else laws.get(field_name)
            if effective is not None:
                needed.extend(effective.facts_read())
        return tuple(dict.fromkeys(needed))

    @staticmethod
    def _contributions(config: Any) -> Tuple[FactContribution, ...]:
        """Reads a config class's declared contributions (empty when none declared)."""
        declared = getattr(type(config), CONTRIBUTIONS_ATTRIBUTE, ())
        return tuple(declared)

    def register(self, configs: Sequence[Any]) -> List[_Node]:
        """Phase 1: builds the per-config nodes with their needs and offers.

        Besides the nodes themselves this records, per CONNECTED-scoped fact, which
        component keys *declare* it as an output. The provider sets of the hybrid lookup
        (see :meth:`_visible_context`) are derived from these declarations rather than
        from the values contributed so far, which is what makes the lookup — including
        its ambiguity errors — independent of the order in which the fixed point happens
        to resolve the nodes.

        Args:
            configs: Every config of the scenario, order-independent.

        Returns:
            The engine nodes, keyed by each config's derived component key.

        Raises:
            SizingError: If two configs share a component key (the engine could not
                attribute facts or errors to either).
        """
        nodes: List[_Node] = []
        seen: Dict[str, str] = {}
        for config in configs:
            key = config.component_id.key
            if key in seen:
                raise SizingError(
                    f"two configs share the component key '{key}' "
                    f"({seen[key]} and {type(config).__name__}); sizing cannot attribute facts."
                )
            seen[key] = type(config).__name__
            contributions = self._contributions(config)
            for contribution in contributions:
                if contribution.scope != FactScope.CONNECTED:
                    continue
                for fact in contribution.facts:
                    self._declared_connected_providers.setdefault(fact, set()).add(key)
            nodes.append(
                _Node(
                    key=key,
                    config=config,
                    needed_facts=self._needed_facts(config),
                    contributions=contributions,
                )
            )
        return nodes

    # ------------------------------------------------------------------ validation

    def validate(self, nodes: Sequence[_Node]) -> None:
        """Phase 2: rejects unprovidable facts before anything is computed.

        A fact is providable when the seed carries it, it is pre-seeded, or some present
        config declares it as an output. Cycles are not detected here — they surface in
        :meth:`resolve_all` as a no-progress state and are then diagnosed with the full
        who-waits-for-whom picture, which is more informative than a bare cycle check.

        Raises:
            ConfigSizingError: Naming the consumer, the fact, and — when a present
                config class *could* contribute it — the provider hint.
        """
        providable: Set[str] = set(self._global_facts)
        for node in nodes:
            for contribution in node.contributions:
                providable.update(contribution.facts)
        for node in nodes:
            missing = [fact for fact in node.needed_facts if fact not in providable]
            if missing:
                raise ConfigSizingError(
                    f"no config in this scenario contributes the fact(s) {missing}, needed by "
                    f"{type(node.config).__name__} '{node.key}'. Add the contributing component, "
                    "seed the fact via the SizingContext, or pre-seed it in 'sizing_facts'."
                )

    # ------------------------------------------------------------------ resolution

    def _visible_context(self, node: _Node) -> Tuple[Optional[SizingContext], List[str]]:
        """Assembles the fact view of one consumer, or reports what is still missing.

        Pre-seeded facts win outright, and global facts are visible to everyone.
        CONNECTED facts resolve as the hybrid of spec §8.4: when an adjacency exists and
        a *declared* provider of the fact sits among the consumer's direct neighbors, the
        lookup is restricted to those neighbors (this is what disambiguates two boilers);
        when no direct neighbor declares the fact — or no adjacency was given — the whole
        pool applies under the uniqueness rule, so a consumer two wiring hops from its
        only provider still resolves. Two remaining sources of one fact — two declared
        providers, or a declared provider next to a seeded global value — are a hard
        error naming every source and the consumer: the engine refuses to guess. Because
        providership comes from the declarations, a provider that has not contributed its
        value yet merely makes the fact *missing* (the fixed point retries), never
        silently narrows the source set.

        Returns:
            The context and an empty list, or ``None`` and the facts still missing.
        """
        values: Dict[str, Any] = {}
        missing: List[str] = []
        for fact in node.needed_facts:
            producer, value, still_missing = self._look_up_fact(node, fact)
            if still_missing:
                missing.append(fact)
                continue
            if value is None:
                raise ConfigSizingError(
                    f"the fact '{fact}' needed by '{node.key}' was provided as null by "
                    f"'{producer}' (its feature is off); the consumer cannot be sized from it."
                )
            values[fact] = value
        if missing:
            return None, missing
        return SizingContext(**values), []

    def _look_up_fact(self, node: _Node, fact: str) -> Tuple[str, Any, bool]:
        """Resolves one needed fact of one consumer to its unique source (spec §8.4).

        Implements the source selection of :meth:`_visible_context`: pre-seed first,
        then the hybrid CONNECTED lookup over the declared providers with the global
        pool as the shared baseline. Returns the producer label and value, or a
        missing marker when the unique source has not contributed its value yet.

        Args:
            node: The consumer whose fact is looked up.
            fact: The fact name, a ``SizingContext`` field.

        Returns:
            A ``(producer, value, missing)`` triple; ``missing`` is ``True`` when the
            fact has no value yet and the fixed point must retry.

        Raises:
            ConfigSizingError: If more than one source could provide the fact.
        """
        if fact in self._preseeded:
            producer, value = self._global_facts[fact]
            return producer, value, False
        providers = set(self._declared_connected_providers.get(fact, ()))
        neighborhood = self._adjacency.get(node.key) if self._adjacency else None
        if neighborhood and providers & neighborhood:
            providers &= neighborhood
        global_entry = self._global_facts.get(fact)
        sources = sorted(providers)
        if global_entry is not None:
            sources.append(global_entry[0])
        if len(sources) > 1:
            raise ConfigSizingError(
                f"the fact '{fact}' needed by '{node.key}' is provided by more than one "
                f"source ({', '.join(sorted(sources))}); sizing refuses to guess. Connect "
                "the consumer to exactly one provider, or pre-seed the fact in "
                "'sizing_facts'."
            )
        if providers:
            (producer,) = providers
            contributed = self._connected_facts.get(producer, {})
            if fact not in contributed:
                return producer, None, True
            return producer, contributed[fact], False
        if global_entry is not None:
            return global_entry[0], global_entry[1], False
        return "<nobody>", None, True

    def _contribution_reads_visible(self, node: _Node) -> bool:
        """True when every fact a node's contributions read is available in the global pool."""
        return all(
            fact in self._global_facts
            for contribution in node.contributions
            for fact in contribution.reads
        )

    def _fold_contributions(self, node: _Node) -> None:
        """Computes a resolved node's contributions and folds them into the fact pools."""
        view = SizingContext(**{
            fact: self._global_facts[fact][1]
            for contribution in node.contributions
            for fact in contribution.reads
        })
        for contribution in node.contributions:
            computed = dict(contribution.compute(node.config, view))
            if set(computed) != set(contribution.facts):
                raise SizingError(
                    f"{type(node.config).__name__} '{node.key}' computed the facts "
                    f"{sorted(computed)} but declared {sorted(contribution.facts)}; output "
                    "names are static per class (spec §8.4)."
                )
            for fact, value in computed.items():
                if fact in self._preseeded:
                    continue  # pre-seeded facts win, loudly: the audit sees <sizing_facts>
                if contribution.scope == FactScope.GLOBAL:
                    if fact in self._global_facts and self._global_facts[fact][0] != "<seed>":
                        raise ConfigSizingError(
                            f"the global fact '{fact}' is contributed twice: by "
                            f"'{self._global_facts[fact][0]}' and by '{node.key}'."
                        )
                    self._global_facts[fact] = (node.key, value)
                else:
                    self._connected_facts.setdefault(node.key, {})[fact] = value
        node.contributed = True

    def resolve_all(self, configs: Sequence[Any]) -> List[Any]:
        """Runs all three phases and returns the resolved configs, input order preserved.

        Configs without sizable fields pass through untouched (they may still
        contribute); configs with sizable-but-concrete fields are re-emitted as fresh
        resolved copies, exactly like ``ConfigBase.resolve``. A no-progress state is
        diagnosed with the full who-waits-for-what picture, which covers both genuine
        cycles and starvation through null/withheld facts.

        Raises:
            ConfigSizingError: On unprovidable facts, ambiguous or null providers, or a
                no-progress deadlock.
        """
        nodes = self.register(configs)
        self.validate(nodes)
        results: Dict[str, Any] = {}
        progress = True
        while progress:
            progress = False
            for node in nodes:
                if not node.resolved:
                    context, missing = self._visible_context(node)
                    if context is None:
                        del missing  # not an error yet; retried until no pass makes progress
                        continue
                    if sizing.sizable_fields(type(node.config)):
                        results[node.key] = node.config.resolve(context)
                    else:
                        results[node.key] = node.config
                    node.config = results[node.key]
                    node.resolved = True
                    self.resolution_order.append(node.key)
                    progress = True
                if node.resolved and not node.contributed and self._contribution_reads_visible(node):
                    self._fold_contributions(node)
                    progress = True
        stuck = [node for node in nodes if not node.resolved]
        if stuck:
            lines = []
            for node in stuck:
                _, missing = self._visible_context(node)
                lines.append(f"  {type(node.config).__name__} '{node.key}' waits for {missing}")
            raise ConfigSizingError(
                "sizing made no further progress; the remaining configs wait on each other "
                "or on facts nobody computed:\n" + "\n".join(lines)
            )
        return [results[node.key] for node in nodes]


def resolve_all(
    configs: Sequence[Any],
    seed: Optional[SizingContext] = None,
    adjacency: Optional[Mapping[str, Set[str]]] = None,
    preseeded_facts: Optional[Mapping[str, Any]] = None,
) -> List[Any]:
    """Resolves every config of a scenario against the shared fact pool (spec §8.4).

    The one entry point both worlds use: the v2 executor passes the parsed connection
    adjacency and any file-level ``sizing_facts``; Python setups pass their seed context
    (typically ``SizingContext.for_building(...)``) — so the template path and the setup
    path are structurally identical rather than parity-tested into agreement.

    Args:
        configs: Every config of the scenario, order-independent.
        seed: Starting facts; ``None`` for an empty context.
        adjacency: Component key → connected keys, for CONNECTED-fact scoping.
        preseeded_facts: File-level fact overrides; they win over contributions.

    Returns:
        The resolved configs, in the input order.
    """
    engine = SizingFactEngine(seed=seed, adjacency=adjacency, preseeded_facts=preseeded_facts)
    return engine.resolve_all(configs)
