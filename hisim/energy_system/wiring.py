"""Constructing the components of an energy system and connecting them as the file says.

By the time this stage runs, every configuration is complete and sized, so the components can
be built — and only then can the connections be checked at all, because HiSim creates a
component's ports inside its constructor. That is the whole reason wiring is a stage of its own
rather than part of validation: whether ``TemperatureOutside`` exists on a building is not a
property of the class as written down, it is a property of the object the configuration
produced, and a configuration that switches a feature off removes the ports that go with it.

The work happens in four passes, and nothing is connected until all four have succeeded.
Components are **constructed** in file order. Every input item is **planned** into concrete
wires: a bare item expands through the target's declared default connections for the source's
class, an explicit wire names both ports itself, and a feed is handed to the aggregator's
channel machinery, which creates the ports and reports the wires that fill them. The plan is
then **checked** as a whole by the wiring rules of the setup rules (``hisim/setup_rules/wiring.py``)
— ports exist, their load types and units agree, no input is fed twice, no mandatory input is
left open — and only afterwards are the wires **applied** (:meth:`WiredSystem.apply`). A file
that fails therefore leaves no half-wired simulator behind.

Nothing here raises a wiring fault. What a pass cannot do — a constructor that raises, a bare
item without declared defaults, a tag no vocabulary knows, a feed the aggregator cannot take — it
records as a :class:`~hisim.energy_system.errors.WiringRefusal` and goes on, so that one build
reports every fault of the file at once; the rules refuse the recorded ones together with the
ones they find in the plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim import log
from hisim.component import Component
from hisim.energy_system.bindings import ClassBinding
from hisim.energy_system.channels import FeedRequest, TagDecoder
from hisim.energy_system.configure import ConfiguredSystem
from hisim.energy_system.errors import EnergySystemErrorId, WiringRefusal
from hisim.energy_system.model import (
    AggregatorFeed,
    ComponentEntry,
    ConsumingOutput,
    DefaultInputs,
    EnergySystemFile,
    ExplicitWire,
)
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.energy_system.resolution import ResolvedDynamicConnection, ResolvedDynamicWire
from hisim.simulationparameters import SimulationParameters

#: What selects observers' feeds among the constructed components: an expansion of imports'
#: selection plan (``assemblies_spec.md`` §4.2), which the planner plans like written feeds. It returns
#: the feeds per observer and what it refused, which the assembly rules refuse.
FeedSelection = Callable[[Mapping[str, Component]], Tuple[Mapping[str, Sequence[AggregatorFeed]], Sequence[WiringRefusal]]]


@dataclass(frozen=True)
class PlannedWire:
    """One field-level connection the wiring stage intends to make, before anything is mutated.

    Bare items, explicit wires and aggregator feeds all reduce to a list of these, which is what
    lets the wiring rules run over expansions and written items in exactly the same way. The wire
    keeps both the names the file uses — so a message points an author at a line they wrote — and
    the runtime component name of its source, which is what a connection is actually matched on
    and which differs from the file's name as soon as a configuration carries a building or a
    unit.

    ``origin`` records which item produced the wire, so a duplicate-feed message can name the two
    items that collide even when one of them came from a default expansion.
    """

    source_name: str
    source_runtime_name: str
    source_output: str
    target_name: str
    target_runtime_name: str
    target_input: str
    origin: str

    def describe(self) -> str:
        """Builds the one-line rendering of the wire that messages and the log name it by.

        Returns:
            A string such as ``'weather.TemperatureOutside' -> 'building.TemperatureOutside'
            (from the bare item 'weather' of 'building')``.
        """
        return (
            f"'{self.source_name}.{self.source_output}' -> "
            f"'{self.target_name}.{self.target_input}' (from {self.origin})"
        )


@dataclass(frozen=True)
class WiredSystem:
    """Everything one wired energy system consists of, in the order the file declares it.

    The result of :func:`wire_energy_system`, the subject of the wiring rules and the input of the
    run: the components by name in file order, every wire the file asks for, the resolved feeds per
    aggregator and the feeds a selection chose per observer. The last three are what a run record
    is written from — the wire log a reader compares against the file, the feed resolution that
    explains where an aggregator's derived ports came from, and the selected feeds the record writes
    into the observers' inputs — so they are kept rather than thrown away once the connections are
    made.

    The rest is what the wiring rules read (``hisim/setup_rules/wiring.py``): how many of the wires
    an author wrote or a bare item expanded (``written_wire_count``; the others feed resolution
    derived), the ports feed resolution created per aggregator, the feeds per aggregator as planned,
    the consuming outputs of the carrier needs, and every refusal the wiring recorded instead of
    raising. The wires are connected by :meth:`apply`, once the rules found nothing.
    """

    components: Tuple[Tuple[str, Component], ...]
    wires: Tuple[PlannedWire, ...]
    resolved_feeds: Tuple[Tuple[str, Tuple[ResolvedDynamicConnection, ...]], ...]
    selected_feeds: Tuple[Tuple[str, Tuple[AggregatorFeed, ...]], ...] = ()
    written_wire_count: int = 0
    created_ports: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    feeds_by_target: Mapping[str, Tuple[FeedRequest, ...]] = field(default_factory=dict)
    consuming: Tuple[ConsumingOutput, ...] = ()
    refusals: Tuple[WiringRefusal, ...] = ()

    def component_of(self, name: str) -> Component:
        """Returns one component by the name the file gives it.

        Args:
            name: The component's key in the file.

        Returns:
            The constructed component.

        Raises:
            KeyError: If the system holds no component of that name.
        """
        for component_name, component in self.components:
            if component_name == name:
                return component
        raise KeyError(name)

    def construction_cause(self) -> Optional[BaseException]:
        """The exception the first constructor that failed raised, or ``None`` when every component was built.

        Example: a heater whose constructor raises ``ConfigurationRefusedError`` gives that error; the build
        raises its refusal ``from`` it, so the refusal's ``__cause__`` is the constructor's own error.
        """
        failed = EnergySystemErrorId.COMPONENT_CONSTRUCTION_FAILED
        return next((refusal.cause for refusal in self.refusals if refusal.error_id is failed and refusal.cause is not None), None)

    def apply(self, system_name: str) -> None:
        """Connects every planned wire to its target component, after the wiring rules found nothing.

        The only mutating step of the whole wiring stage, so a component is either fully wired or
        untouched. The wires are applied in plan order, which is file order over the entries, making
        the wire log a run writes deterministic. A mandatory input its component allows to stay open
        (a heat pump's hot-water input when hot-water preparation is switched off) and that no wire
        feeds is reported in the log.

        Args:
            system_name: The name of the energy system, for the log.
        """
        connected = {(wire.target_name, wire.target_input) for wire in self.wires}
        for name, component in self.components:
            for component_input in component.inputs:
                if component_input.is_mandatory and component_input.allow_unconnected_mandatory and (name, component_input.field_name) not in connected:
                    log.information(
                        f"The mandatory input '{name}.{component_input.field_name}' is "
                        "unconnected, which its component declares as allowed."
                    )
        components_by_name = dict(self.components)
        for wire in self.wires:
            components_by_name[wire.target_name].connect_input(
                input_fieldname=wire.target_input,
                src_object_name=wire.source_runtime_name,
                src_field_name=wire.source_output,
            )
        log.information(f"Wired the energy system '{system_name}': {len(self.components)} components, {len(self.wires)} connections.")


class ComponentBuilder:
    """Constructs one component per entry from the configuration the configuring stage produced.

    Construction is the first irreversible thing a run does — a component reads weather files,
    builds load profiles and allocates its ports — so it happens only once every configuration is
    complete and sized. The builder itself is thin: HiSim components take their simulation
    parameters and their configuration and nothing else, so the only judgement here is turning a
    constructor that raises into a message that names the entry rather than a bare traceback out
    of a component module.
    """

    def __init__(self, system: ConfiguredSystem, simulation_parameters: SimulationParameters) -> None:
        """Prepares the builder for one configured system.

        Args:
            system: The configured system, its configurations complete and sized.
            simulation_parameters: Parameters of the run, handed to every component.
        """
        self.system = system
        self.simulation_parameters = simulation_parameters

    def build(self) -> Tuple[Tuple[Tuple[str, Component], ...], Tuple[WiringRefusal, ...]]:
        """Constructs every component of the system in file order.

        File order decides the order of the global output list and therefore the column order of
        a result frame, so it is preserved exactly. A component whose constructor raises is
        recorded and left out, and the others are still built, so that one build names every
        component that cannot be constructed.

        Returns:
            The ``(name, component)`` pairs in file order, and one ``EF-33`` refusal per entry whose
            constructor raised or returned something that is not a component.
        """
        built: List[Tuple[str, Component]] = []
        refusals: List[WiringRefusal] = []
        for name, config in self.system.configs:
            binding = self.system.bindings[name]
            constructed = self._construct(name, binding, config)
            if isinstance(constructed, WiringRefusal):
                refusals.append(constructed)
            else:
                built.append((name, constructed))
        return tuple(built), tuple(refusals)

    def _construct(self, name: str, binding: ClassBinding, config: Any) -> Any:
        """Calls one component's constructor and checks that it produced a component.

        Args:
            name: The entry's name in the file.
            binding: The entry resolved against its component and configuration classes.
            config: The complete, sized configuration to hand it.

        Returns:
            The constructed component, or the ``EF-33`` refusal if the constructor raised (the
            refusal keeps the exception as its ``cause``) or returned a non-component.
        """
        try:
            component = binding.component_class(
                my_simulation_parameters=self.simulation_parameters, config=config
            )
        except Exception as error:  # pylint: disable=broad-except
            return WiringRefusal(
                EnergySystemErrorId.COMPONENT_CONSTRUCTION_FAILED,
                f"components.{name}",
                f"'{name}' ({binding.entry.class_path}) could not be constructed: {error!r}",
                cause=error,
            )
        if not isinstance(component, Component):
            return WiringRefusal(
                EnergySystemErrorId.COMPONENT_CONSTRUCTION_FAILED,
                f"components.{name}",
                f"'{binding.entry.class_path}' produced a {type(component).__name__} rather "
                f"than a component for the entry '{name}'.",
            )
        return component


class WiringPlanner:
    """Turns every ``inputs`` item of a file into the concrete wires that realize it.

    One planner serves one build. It walks the entries in file order, classifies each item by its
    shape and records what it implies: a bare item expands through the target's declared defaults,
    an explicit wire is one connection, and a feed is filed under the aggregator it addresses and
    resolved once every feed of that aggregator is known. Nothing is connected here — the planner
    only produces a list — but feed resolution does create the aggregator's ports, because the
    ports it creates are exactly what the final checks then verify.

    Bare items pointing at an aggregator are the one case where the two mechanisms meet: a
    component may declare *either* static default connections *or* default feeds for a given
    source class, never both, since a bare item would otherwise mean two different things.
    """

    def __init__(
        self,
        model: EnergySystemFile,
        components: Sequence[Tuple[str, Component]],
        selection: Optional[FeedSelection] = None,
    ) -> None:
        """Prepares a planner for one file and its already constructed components.

        Args:
            model: The energy system, after group expansion and validation.
            components: The ``(name, component)`` pairs in file order.
            selection: Selects observers' feeds among the constructed components and lands the metered
                consuming outputs; ``None`` for a file with no observer, controllable or landed meter feed.
        """
        self.model = model
        self.selection = selection
        self.selected: Dict[str, Tuple[AggregatorFeed, ...]] = {}
        self.components_by_name: Dict[str, Component] = dict(components)
        self.entries: Dict[str, ComponentEntry] = model.all_components()
        self.resolver = DynamicConnectionResolver(self.components_by_name)
        self.wires: List[PlannedWire] = []
        self.feeds_by_target: Dict[str, List[FeedRequest]] = {}
        self.written_wire_count = 0
        self.refusals: List[WiringRefusal] = []

    def plan(self) -> List[PlannedWire]:
        """Plans every connection of the file and creates the aggregators' derived ports.

        Returns:
            The wires to be applied, written and default-expanded ones first, then the ones feed
            resolution asks for. What an item, a feed or an aggregator cannot be planned for is in
            :attr:`refusals`, its own and the resolver's, and contributes no wire.
        """
        for name, entry in self.entries.items():
            for item in entry.inputs:
                if isinstance(item, ExplicitWire):
                    self.wires.append(self._wire_from(name, item))
                elif isinstance(item, AggregatorFeed):
                    self._collect_feed(name, self._feed_from(name, item))
                elif isinstance(item, DefaultInputs):
                    self._expand_default_item(name, item)
        selected, refused = self.selection(self.components_by_name) if self.selection else ({}, ())
        self.refusals.extend(refused)
        for observer, feeds in selected.items():
            self.selected[observer] = tuple(feeds)
            for feed in feeds:
                self._collect_feed(observer, self._feed_from(observer, feed))
        self.written_wire_count = len(self.wires)
        for wire in self.resolver.resolve_all(self.feeds_by_target):
            self.wires.append(self._wire_from_resolution(wire))
        self.refusals.extend(self.resolver.refusals)
        return self.wires

    def resolved_feeds(self) -> Tuple[Tuple[str, Tuple[ResolvedDynamicConnection, ...]], ...]:
        """The resolved feeds per aggregator, for the run record.

        Returns:
            One entry per aggregator that received feeds, sorted by name, each holding its
            resolved connections in the deterministic order they were applied in.
        """
        return tuple(
            (name, tuple(connections))
            for name, connections in sorted(self.resolver.resolved_by_target.items())
        )

    def _collect_feed(self, consumer: str, feed: Optional[FeedRequest]) -> None:
        """Files one feed under the aggregator it addresses.

        Grouping by target is what lets resolution sort an aggregator's feeds deterministically
        and check the per-aggregator rules over the complete set rather than feed by feed.

        Args:
            consumer: Name of the aggregator.
            feed: The feed, written or expanded from a declaration; ``None`` for a written feed
                whose tags could not be decoded, which is refused and planned no further.
        """
        if feed is not None:
            self.feeds_by_target.setdefault(consumer, []).append(feed)

    def _feed_from(self, consumer: str, item: AggregatorFeed) -> Optional[FeedRequest]:
        """Decodes one written feed item into the request the channel machinery works on.

        Args:
            consumer: Name of the aggregator the item sits in.
            item: The written feed.

        Returns:
            The decoded feed request, or ``None`` when a tag name belongs to no tag enumeration;
            each such name is recorded in :attr:`refusals` (``EF-2A``).
        """
        location = f"components.{consumer}.inputs"
        dispatch = item.dispatch
        unknown: List[WiringRefusal] = []
        component_type = TagDecoder.component_type(item.component_type, location, unknown)
        flow_tags = TagDecoder.flow_tags(item.tags, location, unknown)
        dispatch_tags = TagDecoder.flow_tags(dispatch.tags, location, unknown) if dispatch is not None and dispatch.tags else None
        if unknown:
            self.refusals.extend(unknown)
            return None
        return FeedRequest(
            consumer=consumer,
            source=item.source,
            output=item.output,
            component_type=component_type,
            flow_tags=flow_tags,
            weight=item.weight,
            dispatch_target_input=dispatch.target_input if dispatch is not None else None,
            dispatch_tags=dispatch_tags,
            has_dispatch=dispatch is not None,
            origin=f"the feed on '{item.source}' of '{consumer}'",
        )

    def _wire_from(self, consumer: str, item: ExplicitWire) -> PlannedWire:
        """Builds the planned wire of one explicit item.

        Args:
            consumer: Name of the component the item sits in.
            item: The written wire.

        Returns:
            The planned wire.
        """
        return self._planned(
            source_name=item.source,
            source_output=item.output,
            target_name=consumer,
            target_input=item.input,
            origin=f"the explicit wire '{item.input}' of '{consumer}'",
        )

    def _wire_from_resolution(self, wire: ResolvedDynamicWire) -> PlannedWire:
        """Turns one wire reported by feed resolution into a planned wire.

        Args:
            wire: The wire resolution derived from a feed.

        Returns:
            The planned wire.
        """
        return self._planned(
            source_name=wire.source_name,
            source_output=wire.source_output,
            target_name=wire.target_name,
            target_input=wire.target_input,
            origin=wire.origin,
        )

    def _planned(
        self, source_name: str, source_output: str, target_name: str, target_input: str, origin: str
    ) -> PlannedWire:
        """Builds a planned wire, resolving both ends to their runtime component names.

        Args:
            source_name: The producing component's name in the file.
            source_output: Name of its output port.
            target_name: The consuming component's name in the file.
            target_input: Name of its input port.
            origin: Description of the item this wire came from.

        Returns:
            The planned wire.
        """
        return PlannedWire(
            source_name=source_name,
            source_runtime_name=self.components_by_name[source_name].component_name,
            source_output=source_output,
            target_name=target_name,
            target_runtime_name=self.components_by_name[target_name].component_name,
            target_input=target_input,
            origin=origin,
        )

    def _expand_default_item(self, consumer: str, item: DefaultInputs) -> None:
        """Expands a bare item through the target's declared defaults for the source's class.

        Two registries are consulted, both keyed by the source's class name and both belonging to
        the target: the static default connections a component fills in its constructor, and the
        default feeds an aggregator declares. A target that declares both for one source class
        makes the bare item ambiguous, and a target that declares neither is the case the bare
        spelling has no answer for; either is recorded in :attr:`refusals` (``EF-23``) and the
        item expands into nothing.

        Args:
            consumer: Name of the consuming component.
            item: The bare item naming the source.
        """
        source = self.components_by_name[item.source]
        target = self.components_by_name[consumer]
        source_class = source.get_classname()
        static = target.default_connections.get(source_class) or []
        dynamic = self.resolver.default_feeds_of(target, source)
        origin = f"the bare item '{item.source}' of '{consumer}'"
        if static and dynamic:
            self.refusals.append(
                WiringRefusal(
                    EnergySystemErrorId.NO_DECLARED_DEFAULTS,
                    f"components.{consumer}.inputs",
                    f"{origin} is ambiguous: '{consumer}' ({target.get_full_classname()}) declares "
                    f"both static default connections and default feeds for the class "
                    f"'{source_class}'.",
                    remedy=(
                        "A component declares one kind of default per source class; fix the "
                        "component, or write the connections out."
                    ),
                )
            )
            return
        if dynamic:
            for feed in self.resolver.expand_default_item(consumer, item.source):
                self._collect_feed(consumer, feed)
            return
        if not static:
            self.refusals.append(
                WiringRefusal(
                    EnergySystemErrorId.NO_DECLARED_DEFAULTS,
                    f"components.{consumer}.inputs",
                    f"{origin} cannot be expanded: '{consumer}' ({target.get_full_classname()}) "
                    f"declares no default connections for the class '{source_class}'.",
                    alternatives=tuple(sorted(target.default_connections)),
                    alternatives_label="source classes",
                    offending_value=source_class,
                    remedy=(
                        "Write the connection as an explicit '{input, from}' item, or add the "
                        "missing default declaration to the consuming component."
                    ),
                )
            )
            return
        for connection in target.get_default_connections(source_component=source):
            self.wires.append(
                self._planned(
                    source_name=item.source,
                    source_output=connection.source_output_name,
                    target_name=consumer,
                    target_input=connection.target_input_name,
                    origin=origin,
                )
            )


def wire_energy_system(
    model: EnergySystemFile,
    system: ConfiguredSystem,
    simulation_parameters: SimulationParameters,
    consuming: Sequence[ConsumingOutput] = (),
    selection: Optional[FeedSelection] = None,
) -> WiredSystem:
    """Builds every component of a configured energy system and plans its connections as the file says.

    The seventh and eighth stages of the lifecycle in one call, because they are one unit from
    outside: a component's ports exist only once it is constructed, so nothing about the
    connections can be decided before construction, and nothing should be constructed that is
    not going to be connected. The connections are planned, not made: the wiring rules of the setup
    rules judge the plan and what the wiring recorded (``hisim/setup_rules/wiring.py``), and
    :meth:`WiredSystem.apply` makes them once the rules found nothing. A component that cannot be
    constructed leaves nothing to plan; its refusal is the only one recorded.

    Args:
        model: The energy system, after group expansion and validation.
        system: Its configurations, complete and sized.
        simulation_parameters: Parameters of the run, handed to every component.
        consuming: The consuming outputs of the carrier needs an expansion of imports lowered, or
            a re-run record states, each checked by the rules for its carrier and its meter's feeds.
        selection: Selects observers' feeds among the constructed components and lands the metered
            consuming outputs (an expansion of imports' selection plan); ``None`` for a file with no
            observer, controllable or landed meter feed.

    Returns:
        The wired system: its components, the planned wires and every refusal the wiring recorded, those of the
        selection (``EF-7S``, ``EF-7U``, ``EF-7V``) among them.
    """
    components, construction_refusals = ComponentBuilder(system, simulation_parameters).build()
    if construction_refusals:
        return WiredSystem(components=components, wires=(), resolved_feeds=(), consuming=tuple(consuming), refusals=construction_refusals)
    planner = WiringPlanner(model, components, selection)
    wires = planner.plan()
    return WiredSystem(
        components=components,
        wires=tuple(wires),
        resolved_feeds=planner.resolved_feeds(),
        selected_feeds=tuple(planner.selected.items()),
        written_wire_count=planner.written_wire_count,
        created_ports={name: tuple(ports) for name, ports in planner.resolver.created_ports.items()},
        feeds_by_target={name: tuple(feeds) for name, feeds in planner.feeds_by_target.items()},
        consuming=tuple(consuming),
        refusals=tuple(planner.refusals),
    )
