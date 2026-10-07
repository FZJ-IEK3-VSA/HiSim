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

**Control** (§4.4, D11, D21). An observer whose class declares a feed at a weight other than 999
ranks it: a controller, the energy manager. Its weights are the class's own (its
``DEFAULT_WEIGHTS``); the k-th further participant of one component type follows the first in
written order at ``default + k``. A ranked feed's dispatch is what the observed output states:
``controllable: {target_input: …}`` becomes ``dispatch.target_input``, anything else an empty
dispatch. A ``target_input`` output is ranked by exactly one controller (none only with ``optional:
true``); a ``via`` output whose need is bound to a controller is ranked by that one (``EF-7U``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.energy_system.imports_model import Selection
from hisim.energy_system.model import AggregatorFeed, DispatchSpec


@dataclass
class Observer:
    """One component observing, with its selection and the record of what it selected."""

    component: str
    selection: Selection
    owner: str
    feeds: List[AggregatorFeed] = field(default_factory=list)

    def to_document(self) -> Dict[str, Any]:
        """The observer as the import record writes it: its selection and every feed it selected."""
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

    #: The weight of a feed an observer only measures; any other weight is a rank.
    MEASURED: ClassVar[int] = 999

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
                matching nothing, ``EF-7U`` for a controllable output actuated by none or two.
        """
        rankers: Dict[Tuple[str, str], List[str]] = {}
        for observer in self.observers:
            observer.feeds = self._select(observer, components)
            for feed in observer.feeds:
                if feed.weight != self.MEASURED:
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
        return {observer.component: observer.feeds for observer in self.observers}

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
        counts: Dict[str, int] = {}
        feeds: List[AggregatorFeed] = []
        for feed in chosen:
            weight, dispatch = feed.weight, None
            if weight != self.MEASURED:
                kind = feed.component_type.name if feed.component_type is not None else f"{feed.source}.{feed.output}"
                weight += counts.get(kind, 0)
                counts[kind] = counts.get(kind, 0) + 1
                target_input = next(
                    (
                        item.target_input
                        for item in self.controllables
                        if (item.component, item.output) == (feed.source, feed.output)
                    ),
                    None,
                )
                dispatch = DispatchSpec(target_input=target_input)
                if weight >= self.MEASURED:
                    raise self.error(
                        EnergySystemErrorId.OBSERVER_SELECTION,
                        observer.component,
                        f"'{observer.component}' would rank {feed.source}.{feed.output} at {weight}, which marks a "
                        "measured feed; a class ranks fewer participants of one type.",
                    )
            feeds.append(
                AggregatorFeed(
                    source=feed.source,
                    output=feed.output,
                    component_type=feed.component_type.name if feed.component_type is not None else None,
                    tags=tuple(tag.name for tag in feed.flow_tags),
                    weight=weight,
                    dispatch=dispatch,
                )
            )
        return feeds
