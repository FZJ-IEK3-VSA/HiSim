"""The sizing-fact engine: cross-component sizing resolved to a fixed point over configs.

Cross-component sizing dependencies nest deeply (building → HDS controller → HDS;
building → boiler → boiler controller), and the incumbent mechanism — the global
``SingletonSimRepository`` with untyped enum-keyed entries and silent fallbacks — has
already rotted in production. This engine is its typed, hard-erroring successor, in three
phases: **registration** reads every config's inputs from its laws' ``facts_read`` and
its outputs from the :class:`~hisim.config.contributions.FactContribution` declarations
on its class; **validation** rejects a fact nobody provides before anything is computed;
**resolution** sweeps the unresolved configs, sizing each one whose facts are bound and
folding its contributions into the provider pool, until none remain.

**The binding rule.** Every config instance is addressed by its instance name
(``config.component_id.name``), and every fact its class declares is addressable as
``"<name>.<fact>"``. The seed context participates as a provider named ``<seed>``, so
seeding a fact a present config also declares is an ambiguity, not a silent preference.
A bare fact in a law binds if and only if *exactly one* config in the resolved set
declares it; with two or more the engine refuses to guess and names every candidate
together with a paste-ready ``sources`` snippet, and with none it names the consumer and
the fact. The consumer's ``sources`` entry — ``{consumer: {fact: "provider.fact"}}`` —
decides every other case; it may only *redirect* an input, never compute one, so its
values are qualified references and nothing else. Providership comes from the
declarations alone and is fixed before the first sweep: a provider whose value happens
to be ``None`` (its feature is off) still counts, so toggling a feature flag never
silently re-binds a consumer to a different component.

Like every module of the package it imports nothing from the rest of HiSim except
``hisim.log`` (see the layering rule in ``hisim/config/__init__.py``). Every decision is
also recorded in a :class:`~hisim.config.report.ResolutionReport`, readable as
``engine.report`` after a run.
"""

# clean

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

from hisim import log
from hisim.config import sizing
from hisim.config.context import SizingContext
from hisim.config.contributions import FactContribution
from hisim.config.laws import Cardinality, ConfigSizingError, SizingError, SizingLaw
from hisim.config.report import (
    ContributionRecord,
    FactLookupRecord,
    LookupMode,
    ResolutionReport,
    SweepRecord,
)

#: One entry of a consumer's sources mapping: a qualified reference or a list of them.
SourceReference = Union[str, Sequence[str]]


@dataclass
class _Node:
    """The engine's per-config bookkeeping: identity, needs, offers, and progress state.

    ``needed_facts`` pairs every fact the config's pending laws read with the cardinality
    they read it at, because the binding rule treats a one-read and a many-read
    differently. ``contribution_reads`` are the facts the config needs to *compute* its
    own contributions, a readiness condition separate from being sizable.
    """

    name: str
    config: Any
    needed_facts: Tuple[Tuple[str, Cardinality], ...]
    contribution_reads: Tuple[str, ...]
    contributions: Tuple[FactContribution, ...]
    resolved: bool = False
    contributed: bool = False


@dataclass
class _Binding:
    """The outcome of binding one needed fact of one consumer to a provider.

    ``pending`` is the only non-failure state carrying no value: the bound provider has
    not folded its contributions yet, so the fixed point retries the consumer later.
    Everything genuinely wrong — no provider, several, a mapping to a non-provider, a
    null value — has been raised before a binding is ever built.
    """

    provider: str
    value: Any
    mode: str
    candidates: Tuple[str, ...]
    pending: bool = False


class SizingFactEngine:
    """Registration, validation and fixed-point resolution over one scenario's configs.

    Instantiate per resolution run (the engine is stateful bookkeeping, never shared),
    seed it with a :class:`SizingContext` and optionally the per-consumer ``sources``
    mapping, then call :meth:`resolve_all`; the audit-facing result is
    :attr:`resolution_order` plus each resolved config's ``sizing_record``.
    """

    #: The provider name under which the seed context's facts enter the pool: a provider
    #: like any other, so a seeded fact a present config also declares is an ambiguity.
    SEED_PROVIDER: ClassVar[str] = "<seed>"

    def __init__(
        self,
        seed: Optional[SizingContext] = None,
        sources: Optional[Mapping[str, Mapping[str, SourceReference]]] = None,
    ) -> None:
        """Prepares an engine run.

        Args:
            seed: Starting facts (a setup's ``SizingContext.for_building`` result);
                ``None`` means an empty context.
            sources: Per consumer name, per fact, the qualified reference(s) it shall
                read. Needed exactly where two or more configs declare one fact.
        """
        base = seed if seed is not None else SizingContext()
        seed_facts = {
            entry.name: getattr(base, entry.name)
            for entry in dataclasses.fields(SizingContext)
            if getattr(base, entry.name) is not None
        }
        self._providers: Dict[str, Set[str]] = {fact: {self.SEED_PROVIDER} for fact in seed_facts}
        self._pool: Dict[str, Dict[str, Any]] = {self.SEED_PROVIDER: dict(seed_facts)}
        self._sources: Dict[str, Dict[str, SourceReference]] = {
            consumer: dict(entries) for consumer, entries in (sources or {}).items()
        }
        self.resolution_order: List[str] = []
        #: The structured record of every decision this run makes (sweeps, fact lookups,
        #: contributions); pure data, for tests, logs and the audit artifact.
        self.report = ResolutionReport()

    @staticmethod
    def _needed_facts(config: Any) -> Tuple[Tuple[str, Cardinality], ...]:
        """Collects the facts a config's unresolved fields will read, with cardinalities."""
        laws = sizing.sizable_fields(type(config))
        needed: List[Tuple[str, Cardinality]] = []
        for field_name in sizing.auto_fields(config):
            value = getattr(config, field_name)
            effective = value if isinstance(value, SizingLaw) else laws.get(field_name)
            if effective is not None:
                needed.extend(effective.facts_read())
        return tuple(dict.fromkeys(needed))

    @staticmethod
    def _instance_name(config: Any) -> str:
        """Returns the config's instance name, which is how providers are addressed.

        Without a name the config can neither be pointed at by a sources mapping nor be
        blamed in an error message, so a missing one is a :class:`SizingError`.
        """
        name = getattr(getattr(config, "component_id", None), "name", None)
        if not isinstance(name, str) or not name:
            raise SizingError(
                f"{type(config).__name__} has no component_id.name; sizing addresses "
                "every provider and consumer by its instance name."
            )
        return name

    def register(self, configs: Sequence[Any]) -> List[_Node]:
        """Phase 1: builds the per-config nodes and the fact → provider-names table.

        The provider table is derived from the class declarations of exactly the given
        configs, never from what has been computed so far, which is what makes the binding
        — ambiguity errors included — independent of the order the fixed point happens to
        resolve the nodes in. Returns the nodes in input order; raises
        :class:`SizingError` if a config has no instance name or two share one.
        """
        nodes: List[_Node] = []
        seen: Dict[str, str] = {}
        for config in configs:
            name = self._instance_name(config)
            if name in seen:
                raise SizingError(
                    f"two configs named '{name}' ({seen[name]}, {type(config).__name__}); "
                    "sizing addresses every provider and consumer by its instance name."
                )
            seen[name] = type(config).__name__
            contributions = tuple(getattr(type(config), FactContribution.CLASS_ATTRIBUTE, ()))
            for contribution in contributions:
                for fact in contribution.facts:
                    self._providers.setdefault(fact, set()).add(name)
            nodes.append(_Node(
                name=name,
                config=config,
                needed_facts=self._needed_facts(config),
                contribution_reads=tuple(dict.fromkeys(
                    fact for contribution in contributions for fact in contribution.reads)),
                contributions=contributions,
            ))
        return nodes

    def validate(self, nodes: Sequence[_Node]) -> None:
        """Phase 2: rejects facts nobody provides, before anything is computed.

        Cycles are not detected here — they surface in :meth:`resolve_all` as a
        no-progress state, diagnosed with the full who-waits-for-whom picture, which beats
        a bare cycle check. The error names the consumer, the fact and the provider table.
        """
        for node in nodes:
            needed = [fact for fact, _ in node.needed_facts] + list(node.contribution_reads)
            for fact in needed:
                if not self._providers.get(fact):
                    raise ConfigSizingError(
                        f"'{fact}' needed by '{node.name}' is provided by nobody; providers "
                        f"of other facts: {self._describe_providers()}. Add the contributing "
                        "component or seed the fact via the SizingContext."
                    )

    def _describe_providers(self) -> str:
        """Renders the whole provider table for the unprovided-fact error message."""
        if not self._providers:
            return "<none>"
        return ", ".join(
            f"{fact} <- {', '.join(sorted(names))}" for fact, names in sorted(self._providers.items())
        )

    def _reference_provider(self, consumer: str, fact: str, reference: Any) -> str:
        """Validates one ``"<name>.<fact>"`` reference and returns the provider it names.

        Raises:
            ConfigSizingError: If the value is not a reference of that shape (a number, an
                expression, a reference to a different fact), or names a config that does
                not declare the fact — the message then lists what that config does declare.
        """
        if not isinstance(reference, str) or reference != f"{reference.split('.', 1)[0]}.{fact}":
            raise self._shape_error(consumer, fact, "one", reference)
        provider = reference.split(".", 1)[0]
        if provider not in self._providers.get(fact, set()):
            declared = sorted(name for name, names in self._providers.items() if provider in names)
            raise ConfigSizingError(
                f"sources['{consumer}']['{fact}'] = '{reference}' but '{provider}' does not "
                f"declare '{fact}' (declares: {', '.join(declared) or '<nothing>'})"
            )
        return provider

    @staticmethod
    def _shape_error(consumer: str, fact: str, shape: str, value: Any) -> ConfigSizingError:
        """Builds the error for a sources value whose shape does not match the law's read.

        The message quotes the offending value so a number or an expression string — the
        two ways authors try to *compute* in a mapping that may only redirect — is
        recognizable at a glance.
        """
        return ConfigSizingError(
            f"sources['{consumer}']['{fact}']: expected {shape} reference(s) of the form "
            f"'<name>.<fact>', got {value!r}"
        )

    def _unbindable_error(
        self, consumer: str, fact: str, cardinality: Cardinality, candidates: Tuple[str, ...]
    ) -> ConfigSizingError:
        """Builds the error for a bare fact that no single provider can satisfy.

        With no provider at all it names the consumer and lists the whole provider table,
        so a missing component is obvious; with several it lists every candidate plus the
        ``sources`` line that resolves the choice — a list for a many-read, one reference
        for a scalar read — so the fix can be pasted.
        """
        references = [f"{name}.{fact}" for name in candidates]
        if cardinality is Cardinality.MANY:
            listed = ", ".join(f"'{reference}'" for reference in references)
            return ConfigSizingError(
                f"'{fact}' is read many-fold by '{consumer}' and is provided by "
                f"{', '.join(candidates) or 'nobody'}; write "
                f"sources={{'{consumer}': {{'{fact}': [{listed}]}}}} (an empty list for none)"
            )
        if not candidates:
            return ConfigSizingError(
                f"'{fact}' needed by '{consumer}' is provided by nobody; providers of other "
                f"facts: {self._describe_providers()}"
            )
        return ConfigSizingError(
            f"'{fact}' needed by '{consumer}' is provided by {', '.join(candidates)}; pass "
            f"sources={{'{consumer}': {{'{fact}': '<one of {' or '.join(references)}>'}}}}"
        )

    def _bind(self, consumer: str, fact: str, cardinality: Cardinality) -> _Binding:
        """Binds one needed fact of one consumer to its provider, or reports it pending.

        Implements the binding rule of the module docstring: an explicit mapping wins and
        is validated against the provider table, a bare fact needs exactly one declared
        provider, and everything else raises naming what the author must write — for an
        unprovided, ambiguous, wrongly mapped, mis-shaped or null-valued fact alike.
        """
        candidates = tuple(sorted(self._providers.get(fact, set())))
        mapped = self._sources.get(consumer, {}).get(fact)
        if mapped is None:
            if cardinality is Cardinality.MANY and len(candidates) == 1:
                return self._bind_many(fact, candidates, candidates)
            if cardinality is Cardinality.MANY or len(candidates) != 1:
                raise self._unbindable_error(consumer, fact, cardinality, candidates)
            provider = candidates[0]
            mode = LookupMode.SEED if provider == self.SEED_PROVIDER else LookupMode.UNIQUE
            return self._bind_one(consumer, fact, provider, mode, candidates)
        if cardinality is Cardinality.MANY:
            if isinstance(mapped, str) or not isinstance(mapped, (list, tuple)):
                raise self._shape_error(consumer, fact, "a list of", mapped)
            chosen = tuple(self._reference_provider(consumer, fact, entry) for entry in mapped)
            return self._bind_many(fact, chosen, candidates)
        if not isinstance(mapped, str):
            raise self._shape_error(consumer, fact, "one", mapped)
        provider = self._reference_provider(consumer, fact, mapped)
        return self._bind_one(consumer, fact, provider, LookupMode.EXPLICIT, candidates)

    def _bind_one(self, consumer: str, fact: str, provider: str, mode: str,
                  candidates: Tuple[str, ...]) -> _Binding:
        """Reads one provider's value for a scalar fact, or reports the read as pending.

        A provider that has not folded its contributions yet is not an error — the fixed
        point comes back to it — but one that computed ``None`` is: its feature is off,
        and sizing from a switched-off component would invent a number.
        """
        contributed = self._pool.get(provider, {})
        if fact not in contributed:
            return _Binding(provider=provider, value=None, mode=mode, candidates=candidates, pending=True)
        value = contributed[fact]
        if value is None:
            raise ConfigSizingError(
                f"'{fact}' provided as null by '{provider}' (feature off); '{consumer}' "
                "cannot size from it."
            )
        return _Binding(provider=provider, value=value, mode=mode, candidates=candidates)

    def _bind_many(self, fact: str, chosen: Tuple[str, ...], candidates: Tuple[str, ...]) -> _Binding:
        """Collects several providers' values into a tuple for a many-cardinality read.

        The tuple is assembled in the order the author wrote (or, for the single-provider
        shortcut, the one candidate) so the eventual aggregation has a defined input
        order. Evaluating a many term still raises: this value is a hook, not a result.
        """
        label = ", ".join(chosen) or "<none>"
        if any(fact not in self._pool.get(provider, {}) for provider in chosen):
            return _Binding(provider=label, value=None, mode=LookupMode.EXPLICIT,
                            candidates=candidates, pending=True)
        return _Binding(provider=label, value=tuple(self._pool[name][fact] for name in chosen),
                        mode=LookupMode.EXPLICIT, candidates=candidates)

    def _visible_context(
        self, node: _Node
    ) -> Tuple[Optional[SizingContext], List[str], Dict[str, str], List[FactLookupRecord]]:
        """Assembles the fact view of one consumer, or reports what is still missing.

        Returns a ``(context, missing, fact_sources, lookups)`` quadruple: the context
        with an empty missing list, the fact → provider-name map the sizing record needs,
        and one :class:`FactLookupRecord` per fact read; or ``None`` with the missing
        facts (an incomplete view has not *read* anything, so it records no lookups).
        """
        values: Dict[str, Any] = {}
        providers: Dict[str, str] = {}
        missing: List[str] = []
        lookups: List[FactLookupRecord] = []
        for fact, cardinality in node.needed_facts:  # binding order is irrelevant
            binding = self._bind(node.name, fact, cardinality)
            if binding.pending:
                missing.append(fact)
                continue
            values[fact] = binding.value
            providers[fact] = binding.provider
            lookups.append(FactLookupRecord(
                consumer=node.name, fact=fact, source=binding.provider, value=binding.value,
                mode=binding.mode, candidates=binding.candidates))
        if missing:
            return None, missing, {}, []
        return SizingContext(**values), [], providers, lookups

    def _fold_contributions(self, node: _Node) -> Optional[List[ContributionRecord]]:
        """Computes a resolved node's contributions and folds them into the provider pool.

        Returns the contribution records for the current sweep's report entry, or ``None``
        while a fact the contributions themselves read is still pending. Values are stored
        under the contributing instance's own name, so two instances of one class never
        overwrite each other and a consumer always reads the provider it was bound to.
        """
        read_values: Dict[str, Any] = {}
        for fact in node.contribution_reads:
            binding = self._bind(node.name, fact, Cardinality.ONE)
            if binding.pending:
                return None
            read_values[fact] = binding.value
        view = SizingContext(**read_values)
        records: List[ContributionRecord] = []
        for contribution in node.contributions:
            computed = dict(contribution.compute(node.config, view))
            if set(computed) != set(contribution.facts):
                raise SizingError(
                    f"{type(node.config).__name__} '{node.name}' computed the facts "
                    f"{sorted(computed)} but declared {sorted(contribution.facts)}; output "
                    "names are static per class, so the dependency graph stays checkable up front."
                )
            for fact, value in computed.items():
                self._pool.setdefault(node.name, {})[fact] = value
                records.append(ContributionRecord(producer=node.name, fact=fact, value=value))
                log.debug(f"Sizing: '{node.name}' contributed {fact}={value!r}.")
        node.contributed = True
        return records

    def resolve_all(self, configs: Sequence[Any]) -> List[Any]:
        """Runs all three phases and returns the resolved configs, input order preserved.

        Configs without sizable fields pass through untouched (they may still
        contribute); configs with sizable-but-concrete fields are re-emitted as fresh
        resolved copies, exactly like ``ConfigBase.resolve``. A no-progress state raises
        with the full who-waits-for-what picture, which covers both genuine cycles and
        starvation through null facts; every binding failure raises too.
        """
        nodes = self.register(configs)
        self.validate(nodes)
        results: Dict[str, Any] = {}
        progress, sweep_number = True, 0
        while progress:
            progress = False
            sweep_number += 1
            resolved_this_sweep: List[str] = []
            contributed_this_sweep: List[ContributionRecord] = []
            waiting: Dict[str, Tuple[str, ...]] = {}  # config name -> facts still missing
            for node in nodes:
                if not node.resolved:
                    context, missing, providers, lookups = self._visible_context(node)
                    if context is None:
                        # Not an error yet; retried until no pass makes progress. The
                        # waits are kept for this sweep's report entry.
                        waiting[node.name] = tuple(missing)
                        continue
                    if sizing.sizable_fields(type(node.config)):
                        results[node.name] = sizing.resolve_config(node.config, context, providers)
                    else:
                        results[node.name] = node.config
                    node.config = results[node.name]
                    node.resolved = True
                    self.resolution_order.append(node.name)
                    self.report.lookups.extend(lookups)
                    resolved_this_sweep.append(node.name)
                    progress = True
                if node.resolved and not node.contributed:
                    folded = self._fold_contributions(node)
                    if folded is not None:
                        contributed_this_sweep.extend(folded)
                        progress = True
            if progress:
                self.report.sweeps.append(SweepRecord(
                    number=sweep_number, resolved=tuple(resolved_this_sweep),
                    contributed=tuple(contributed_this_sweep),
                    waiting=tuple(sorted(waiting.items()))))
                log.debug(
                    f"Sizing sweep {sweep_number}: resolved "
                    f"{', '.join(resolved_this_sweep) or '<contributions only>'}."
                )
        stuck = [node for node in nodes if not node.resolved]
        if stuck:
            lines = [
                f"  {type(node.config).__name__} '{node.name}' waits for "
                f"{self._visible_context(node)[1]}" for node in stuck]
            raise ConfigSizingError(
                "sizing made no further progress; the remaining configs wait on each other "
                "or on facts nobody computed:\n" + "\n".join(lines)
                + "\n\nResolution history up to the deadlock:\n" + self.report.render()
            )
        consumed = {(entry.source, entry.fact) for entry in self.report.lookups}
        self.report.unconsumed = sorted(
            (producer, fact)
            for producer, facts in self._pool.items()
            for fact in facts
            if (producer, fact) not in consumed
        )
        log.information(
            f"Sizing: resolved {len(nodes)} configs in {len(self.report.sweeps)} sweep(s); "
            f"order: {', '.join(self.resolution_order)}."
        )
        return [results[node.name] for node in nodes]


def resolve_all(
    configs: Sequence[Any],
    seed: Optional[SizingContext] = None,
    sources: Optional[Mapping[str, Mapping[str, SourceReference]]] = None,
) -> List[Any]:
    """Resolves every config of a scenario against the shared provider pool.

    The one entry point both worlds use: the v2 executor passes the ``sizing_sources``
    mapping it parsed from the file, Python setups pass their seed context (typically
    ``SizingContext.for_building(...)``) and the same mapping in Python form where two
    configs declare one fact — so the template path and the setup path are structurally
    identical rather than parity-tested into agreement.

    Args:
        configs: Every config of the scenario, order-independent; each must carry a
            unique ``component_id.name``.
        seed: Starting facts; ``None`` for an empty context.
        sources: Per consumer name, per fact, the qualified ``"<provider>.<fact>"``
            reference(s) that consumer shall read.

    Returns:
        The resolved configs, in the input order.
    """
    engine = SizingFactEngine(seed=seed, sources=sources)
    return engine.resolve_all(configs)
