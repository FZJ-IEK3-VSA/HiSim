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
then **checked** as a whole — ports exist, their load types and units agree, no input is fed
twice, no mandatory input is left open — and only afterwards are the wires **applied**. A file
that fails therefore leaves no half-wired simulator behind.

Two rules are worth calling out because they are easy to mistake for over-strictness. An
explicit wire may not name a port that feed resolution created, because that port does not
exist in the file the author is reading and its name is derived rather than declared. And a
mandatory input left unconnected is an error rather than a warning, unless the port itself says
its source may legitimately be absent: a half-wired system that starts and produces numbers is
the failure mode this format exists to remove.
"""

# clean

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from hisim import log
from hisim.component import Component
from hisim.energy_system.bindings import ClassBinding
from hisim.energy_system.channels import FeedRequest, TagDecoder
from hisim.energy_system.configure import ConfiguredSystem
from hisim.energy_system.errors import (
    EnergySystemErrorId,
    EnergySystemWiringError,
)
from hisim.energy_system.model import (
    AggregatorFeed,
    ComponentEntry,
    DefaultInputs,
    EnergySystemFile,
    ExplicitWire,
)
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.energy_system.resolution import ResolvedDynamicConnection, ResolvedDynamicWire
from hisim.energy_system.wiring_checks import PlannedWire, WiringChecker, unconsumed_sources
from hisim.simulationparameters import SimulationParameters


@dataclass(frozen=True)
class WiredSystem:
    """Everything one wired energy system consists of, in the order the file declares it.

    The result of :func:`wire_energy_system` and the input of the run: the components by name in
    file order, every wire that was applied, and the resolved feeds per aggregator. The last two
    are what a run record is written from — the wire log a reader compares against the file, and
    the feed resolution that explains where an aggregator's derived ports came from — so they are
    kept rather than thrown away once the connections are made.
    """

    components: Tuple[Tuple[str, Component], ...]
    wires: Tuple[PlannedWire, ...]
    resolved_feeds: Tuple[Tuple[str, Tuple[ResolvedDynamicConnection, ...]], ...]

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

    def build(self) -> Tuple[Tuple[str, Component], ...]:
        """Constructs every component of the system in file order.

        File order decides the order of the global output list and therefore the column order of
        a result frame, so it is preserved exactly.

        Returns:
            The ``(name, component)`` pairs in file order.

        Raises:
            EnergySystemWiringError: ``EF-33`` if a component's constructor raises, or returns
                something that is not a component.
        """
        built: List[Tuple[str, Component]] = []
        for name, config in self.system.configs:
            binding = self.system.bindings[name]
            built.append((name, self._construct(name, binding, config)))
        return tuple(built)

    def _construct(self, name: str, binding: ClassBinding, config: Any) -> Component:
        """Calls one component's constructor and checks that it produced a component.

        Args:
            name: The entry's name in the file.
            binding: The entry resolved against its component and configuration classes.
            config: The complete, sized configuration to hand it.

        Returns:
            The constructed component.

        Raises:
            EnergySystemWiringError: ``EF-33`` if the constructor raises or returns a non-component.
        """
        try:
            component = binding.component_class(
                my_simulation_parameters=self.simulation_parameters, config=config
            )
        except Exception as error:  # pylint: disable=broad-except
            raise EnergySystemWiringError(
                EnergySystemErrorId.COMPONENT_CONSTRUCTION_FAILED,
                f"components.{name}",
                f"'{name}' ({binding.entry.class_path}) could not be constructed: {error!r}",
            ) from error
        if not isinstance(component, Component):
            raise EnergySystemWiringError(
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

    def __init__(self, model: EnergySystemFile, components: Sequence[Tuple[str, Component]]) -> None:
        """Prepares a planner for one file and its already constructed components.

        Args:
            model: The energy system, after group expansion and validation.
            components: The ``(name, component)`` pairs in file order.
        """
        self.model = model
        self.components_by_name: Dict[str, Component] = dict(components)
        self.entries: Dict[str, ComponentEntry] = model.all_components()
        self.resolver = DynamicConnectionResolver(self.components_by_name)
        self.wires: List[PlannedWire] = []
        self.feeds_by_target: Dict[str, List[FeedRequest]] = {}
        self.written_wire_count = 0

    def plan(self) -> List[PlannedWire]:
        """Plans every connection of the file and creates the aggregators' derived ports.

        Returns:
            The wires to be applied, written and default-expanded ones first, then the ones feed
            resolution asks for.

        Raises:
            EnergySystemWiringError: On any connection condition of the error catalogue.
        """
        for name, entry in self.entries.items():
            for item in entry.inputs:
                if isinstance(item, ExplicitWire):
                    self.wires.append(self._wire_from(name, item))
                elif isinstance(item, AggregatorFeed):
                    self._collect_feed(name, self._feed_from(name, item))
                elif isinstance(item, DefaultInputs):
                    self._expand_default_item(name, item)
        self.written_wire_count = len(self.wires)
        for wire in self.resolver.resolve_all(self.feeds_by_target):
            self.wires.append(self._wire_from_resolution(wire))
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

    def _collect_feed(self, consumer: str, feed: FeedRequest) -> None:
        """Files one feed under the aggregator it addresses.

        Grouping by target is what lets resolution sort an aggregator's feeds deterministically
        and check the per-aggregator rules over the complete set rather than feed by feed.

        Args:
            consumer: Name of the aggregator.
            feed: The feed, written or expanded from a declaration.
        """
        self.feeds_by_target.setdefault(consumer, []).append(feed)

    def _feed_from(self, consumer: str, item: AggregatorFeed) -> FeedRequest:
        """Decodes one written feed item into the request the channel machinery works on.

        Args:
            consumer: Name of the aggregator the item sits in.
            item: The written feed.

        Returns:
            The decoded feed request.

        Raises:
            EnergySystemBindingError: ``EF-2A`` if a tag name belongs to no tag enumeration.
        """
        location = f"components.{consumer}.inputs"
        dispatch = item.dispatch
        return FeedRequest(
            consumer=consumer,
            source=item.source,
            output=item.output,
            component_type=TagDecoder.component_type(item.component_type, location),
            flow_tags=TagDecoder.flow_tags(item.tags, location),
            weight=item.weight,
            dispatch_target_input=dispatch.target_input if dispatch is not None else None,
            dispatch_tags=(
                TagDecoder.flow_tags(dispatch.tags, location)
                if dispatch is not None and dispatch.tags
                else None
            ),
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
        spelling has no answer for.

        Args:
            consumer: Name of the consuming component.
            item: The bare item naming the source.

        Raises:
            EnergySystemWiringError: ``EF-23`` if the target declares no defaults for the
                source's class, or declares both kinds.
        """
        source = self.components_by_name[item.source]
        target = self.components_by_name[consumer]
        source_class = source.get_classname()
        static = target.default_connections.get(source_class) or []
        dynamic = self.resolver.default_feeds_of(target, source_class)
        origin = f"the bare item '{item.source}' of '{consumer}'"
        if static and dynamic:
            raise EnergySystemWiringError(
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
        if dynamic:
            for feed in self.resolver.expand_default_item(consumer, item.source):
                self._collect_feed(consumer, feed)
            return
        if not static:
            raise EnergySystemWiringError(
                EnergySystemErrorId.NO_DECLARED_DEFAULTS,
                f"components.{consumer}.inputs",
                f"{origin} cannot be expanded: '{consumer}' ({target.get_full_classname()}) "
                f"declares no default connections for the class '{source_class}'.",
                alternatives=sorted(target.default_connections),
                alternatives_label="source classes",
                offending_value=source_class,
                remedy=(
                    "Write the connection as an explicit '{input, from}' item, or add the "
                    "missing default declaration to the consuming component."
                ),
            )
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
) -> Tuple[WiredSystem, Tuple[str, ...]]:
    """Builds every component of a configured energy system and connects it as the file says.

    The seventh and eighth stages of the lifecycle in one call, because they are one unit from
    outside: a component's ports exist only once it is constructed, so nothing about the
    connections can be decided before construction, and nothing should be constructed that is
    not going to be connected.

    Args:
        model: The energy system, after group expansion and validation.
        system: Its configurations, complete and sized.
        simulation_parameters: Parameters of the run, handed to every component.

    Returns:
        The wired system and the warnings a run should print: one line per component that feeds
        nothing, which is legal but far more often a forgotten input item.

    Raises:
        EnergySystemWiringError: ``EF-21`` … ``EF-33`` for anything wrong with the connections
            or with a component's construction; every message names both ends.
    """
    components = ComponentBuilder(system, simulation_parameters).build()
    planner = WiringPlanner(model, components)
    wires = planner.plan()
    checker = WiringChecker(
        components_by_name=dict(components),
        wires=wires,
        written_wire_count=planner.written_wire_count,
        created_ports=planner.resolver.created_ports,
        system_name=model.name,
    )
    checker.check_all()
    checker.apply()
    log.information(
        f"Wired the energy system '{model.name}': {len(components)} components, "
        f"{len(wires)} connections."
    )
    return (
        WiredSystem(
            components=components,
            wires=tuple(wires),
            resolved_feeds=planner.resolved_feeds(),
        ),
        unconsumed_sources([name for name, _ in components], wires),
    )
