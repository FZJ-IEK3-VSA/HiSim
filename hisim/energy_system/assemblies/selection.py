"""Observe and actuate: every observer's selection, run by the wiring on the constructed components (§4).

``assemblies_spec.md`` §4.1-§4.3 and the lean v1 of §13.1. HiSim has no bus: a meter and an energy
manager are dynamic components that declare in their constructor which outputs of which classes
they take, with which tags and at which weight (``add_dynamic_default_connections``). ``observes:
declared`` is what HiSim's ``connect_automatically`` makes of that declaration over the components
present; a list of selectors filters it. So the selection runs where the observer exists, in the
wiring planner, which plans the returned feeds like written ones; the realized record writes them
into the observer's inputs, and a re-run selects nothing.

**Candidates and order.** Every output of every other present component, in file order (site
entries, then the imports, each instance as written), that the observer declares a feed from, in
the order it declares them; an observer never matches its own outputs. A selector that matches
nothing is refused (``EF-7S``), as is an observer whose class declares no feeds at all.

**Control** (§4.4, D11, D21, D27). An observer whose class declares a feed at a weight other than
the monitored-only weight (``FeedRequest.MONITORED_ONLY_WEIGHT``, 999) ranks it: a controller, the
energy manager. Its weights are the class's own (its ``DEFAULT_WEIGHTS``, which it declares its
feeds at); the k-th further participant — a source component — of one component type follows the
first in written order at ``default + k``, and every ranked feed of one participant shares its
offset. A feed declared without a component type counts under its source alone, at its declared
weight. A derived weight (``k > 0``) that reaches the base weight of another component type the
class ranks, or the monitored-only weight, is refused (``EF-7V``, D27): the two participants would
tie; the remedy is to pin the weight on a written feed. A ranked feed's dispatch is what the observed output states:
``controllable: {target_input: …}`` becomes ``dispatch.target_input``, anything else an empty
dispatch. A ``target_input`` output is ranked by exactly one controller (none only with ``optional:
true``); a ``via`` output whose need is bound to a controller is ranked by that one (``EF-7U``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, NoReturn, Optional, Tuple

from hisim.energy_system.channels import FeedRequest
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId, EnergySystemRecordError
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.energy_system.imports_model import Selection
from hisim.energy_system.model import AggregatorFeed, DispatchSpec


@dataclass
class Observer:
    """One component observing, with its selection and, once the wiring selected them, its feeds."""

    component: str
    selection: Selection
    owner: str
    #: The selected feeds; ``None`` until the selection plan ran on the constructed components.
    feeds: Optional[List[AggregatorFeed]] = None

    def to_document(self) -> Dict[str, Any]:
        """The observer as the import record writes it: its selection and every feed it selected.

        Raises:
            EnergySystemRecordError: ``EF-60`` for an observer whose feeds were never selected: the
                record of what it observes is written after the wiring, never before.
        """
        if self.feeds is None:
            raise EnergySystemRecordError(
                EnergySystemErrorId.RECORD_NOT_CONCRETE,
                f"metadata.imports.observers.{self.component}",
                f"the observer '{self.component}' ({self.owner}) is written before the wiring selected its feeds; "
                "the import record is serialized after the wiring.",
            )
        return {
            "observer": self.component,
            "owner": self.owner,
            "selection": self.selection.text(),
            "feeds": [
                f"{feed.source}.{feed.output} [{feed.component_type or '-'}; {', '.join(feed.tags)}] weight "
                f"{feed.weight}"
                + (f", dispatch {feed.dispatch.target_input or '{}'}" if feed.dispatch is not None else "")
                for feed in self.feeds
            ],
        }


@dataclass(frozen=True)
class Controllable:
    """A provided output a controller may actuate (``controllable:``, §4.4).

    Attributes:
        component: The component whose output it is.
        output: The output.
        target_input: The input a controller actuates directly, or ``None`` for ``via``.
        via_partner: For ``via``, the component its need was bound to, or ``None``.
        optional: Whether a ``target_input`` output may stay without a controller.
        label: How a message names the port.
    """

    component: str
    output: str
    target_input: Optional[str]
    via_partner: Optional[str]
    optional: bool
    label: str


@dataclass
class SelectionPlan:
    """Every observer and controllable output of one expanded system; the wiring planner calls it."""

    observers: List[Observer] = field(default_factory=list)
    controllables: List[Controllable] = field(default_factory=list)

    @staticmethod
    def error(error_id: EnergySystemErrorId, component: str, problem: str, **kwargs: Any) -> EnergySystemAssemblyError:
        """One refusal, located at the observer, so the source map names where it came from."""
        return EnergySystemAssemblyError(error_id, f"components.{component}", problem, **kwargs)

    def __call__(self, components: Mapping[str, Any]) -> Dict[str, List[AggregatorFeed]]:
        """Selects, ranks and checks every observer's feeds among the constructed components.

        Args:
            components: Every constructed component by its name in the file, in file order.

        Returns:
            Each observer's feeds, in candidate order.

        Raises:
            EnergySystemAssemblyError: ``EF-7S`` for an observer that cannot observe or a selector
                matching nothing, ``EF-7V`` for a derived weight that ties with another component
                type's, ``EF-7U`` for a controllable output actuated by none or two.
        """
        rankers: Dict[Tuple[str, str], List[str]] = {}
        selected: Dict[str, List[AggregatorFeed]] = {}
        for observer in self.observers:
            observer.feeds = selected[observer.component] = self._select(observer, components)
            for feed in observer.feeds:
                if feed.weight != FeedRequest.MONITORED_ONLY_WEIGHT:
                    rankers.setdefault((feed.source, feed.output or ""), []).append(observer.component)
        for item in self.controllables:
            ranked_by = rankers.get((item.component, item.output), [])
            missing = item.target_input is not None and not ranked_by and not item.optional
            wrong = item.via_partner is not None and ranked_by != [item.via_partner]
            if len(ranked_by) > 1 or missing or wrong:
                raise self.error(
                    EnergySystemErrorId.ACTUATION,
                    item.component,
                    f"the controllable output {item.component}.{item.output} ({item.label}) is ranked by "
                    f"{', '.join(ranked_by) or 'no controller'}"
                    + (f", but its need is bound to {item.via_partner}" if item.via_partner else "")
                    + "; a controllable output is actuated by exactly the one controller it binds (D21).",
                )
        return selected

    def _select(self, observer: Observer, components: Mapping[str, Any]) -> List[AggregatorFeed]:
        """One observer's feeds: its candidates, filtered by its selection, ranked."""
        target = components[observer.component]
        declared = getattr(target, DynamicConnectionResolver.DEFAULT_FEEDS_ATTRIBUTE, None) or {}
        if not any(declared.values()):
            raise self.error(
                EnergySystemErrorId.OBSERVER_SELECTION,
                observer.component,
                f"'{observer.component}' ({type(target).__name__}) observes, but its class declares no dynamic "
                "default connections, so it has nothing to select from.",
            )
        candidates = [
            DynamicConnectionResolver.feed_from_declaration(declaration, observer.component, name)
            for name, component in components.items()
            if name != observer.component
            for declaration in declared.get(component.get_classname(), ())
        ]
        listed = ", ".join(f"{feed.source}.{feed.output}" for feed in candidates) or "none"

        def matches(selector: Any, feed: Any) -> bool:
            kind = feed.component_type.name if feed.component_type is not None else None
            return bool(selector.matches(kind, tuple(tag.name for tag in feed.flow_tags), feed.output or ""))

        chosen = candidates
        if observer.selection.selectors is not None:
            for selector in observer.selection.selectors:
                if not any(matches(selector, feed) for feed in candidates):
                    raise self.error(
                        EnergySystemErrorId.OBSERVER_SELECTION,
                        observer.component,
                        f"the selector {selector.text()} of '{observer.component}' matches no output its class "
                        f"declares a feed from; candidates: {listed}.",
                    )
            chosen = [feed for feed in candidates if any(matches(item, feed) for item in observer.selection.selectors)]
        if not chosen:
            raise self.error(
                EnergySystemErrorId.OBSERVER_SELECTION,
                observer.component,
                f"'{observer.component}' observes nothing: no present component has an output its class declares a "
                "feed from.",
            )
        return self._rank(observer, chosen, declared)

    def _rank(self, observer: Observer, chosen: List[FeedRequest], declared: Mapping[str, Any]) -> List[AggregatorFeed]:
        """The chosen feeds as the observer's feeds: each ranked one at its participant's weight, with its dispatch.

        The k-th further participant (source component) of one component type is ranked at
        ``default + k``; a feed without a component type counts under its source alone.

        Raises:
            EnergySystemAssemblyError: ``EF-7V`` for a derived weight that reaches another component
                type's base weight or the monitored-only weight.
        """
        measured = FeedRequest.MONITORED_ONLY_WEIGHT
        #: Each weight the class declares a ranked feed at, to the component types declared at it.
        bases: Dict[int, List[str]] = {}
        for declaration in (item for items in declared.values() for item in items):
            request = DynamicConnectionResolver.feed_from_declaration(declaration, observer.component, "")
            if request.weight != measured and request.component_type is not None:
                kinds = bases.setdefault(request.weight, [])
                if request.component_type.name not in kinds:
                    kinds.append(request.component_type.name)
        participants: Dict[Tuple[str, str], List[str]] = {}
        feeds: List[AggregatorFeed] = []
        for feed in chosen:
            component_type = feed.component_type.name if feed.component_type is not None else None
            weight, dispatch = feed.weight, None
            if weight != measured:
                kind = ("type", component_type) if component_type is not None else ("source", feed.source)
                ranked = participants.setdefault(kind, [])
                if feed.source not in ranked:
                    ranked.append(feed.source)
                offset = ranked.index(feed.source)
                weight += offset
                others = [item for item in bases.get(weight, []) if item != component_type]
                if (offset and others) or weight >= measured:
                    self._refuse_collision(observer, feed, offset, weight, others, chosen)
                target_input = next(
                    (
                        item.target_input
                        for item in self.controllables
                        if (item.component, item.output) == (feed.source, feed.output)
                    ),
                    None,
                )
                dispatch = DispatchSpec(target_input=target_input)
            feeds.append(
                AggregatorFeed(
                    source=feed.source,
                    output=feed.output,
                    component_type=component_type,
                    tags=tuple(tag.name for tag in feed.flow_tags),
                    weight=weight,
                    dispatch=dispatch,
                )
            )
        return feeds

    def _refuse_collision(
        self,
        observer: Observer,
        feed: FeedRequest,
        offset: int,
        weight: int,
        others: List[str],
        chosen: List[FeedRequest],
    ) -> NoReturn:
        """Refuses a derived weight that ties with another component type's base weight (``EF-7V``, D27)."""
        kind = feed.component_type.name if feed.component_type is not None else feed.source
        if others:
            present = [
                item.source for item in chosen if item.component_type is not None and item.component_type.name in others
            ]
            reached = (
                f"the base weight of {', '.join(others)} ({', '.join(dict.fromkeys(present)) or 'none present'}): "
                "the two would tie"
            )
        else:
            reached = f"at or above the monitored-only weight {FeedRequest.MONITORED_ONLY_WEIGHT}, which is no rank"
        raise self.error(
            EnergySystemErrorId.WEIGHT_COLLISION,
            observer.component,
            f"'{observer.component}' would rank {feed.source}.{feed.output}, participant {offset + 1} of {kind}, at "
            f"{feed.weight} + {offset} = {weight}, which is {reached}. Pin the weight on the feed: "
            f"write {feed.source}.{feed.output} with its weight among the inputs of '{observer.component}' and leave "
            "it out of the selection.",
        )
