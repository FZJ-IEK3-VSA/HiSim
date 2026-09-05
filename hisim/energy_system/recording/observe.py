"""Reading a live, wired simulator into the plain data a recorded file is built from.

The recorder never parses Python. It runs the setup, lets the simulator resolve every default
connection, and then *looks* at what is there — which is why group membership, the preset a
still-unconverted class was built from and the question of whether a number was sized or typed can
never be recovered: none of them survives into the objects. What does survive is everything a file
needs, and this module collects exactly that and nothing else.

Observation is strictly read-only, and that is a property worth stating rather than assuming. The
same simulator is normally run afterwards, in the same process, so an observation that touched a
component's state would change the numbers the run produces and the recording would be measuring
its own footprint. Every value taken here is either an attribute read or a fresh tuple built from
one; nothing is set, appended to, sorted in place or copied by reference into a mutable container.

The one thing that is *not* copied is the configuration object. A recorded entry has to be written
from the live configuration — a deep copy would lose the preset provenance stamp, which is a plain
instance attribute — so the observation holds the object itself, and the writer downstream treats
it as read-only for the same reason this module does.
"""

# clean

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from hisim.component import Component
from hisim.dynamic_component import DynamicComponent
from hisim.energy_system.parity import ResolvedWire, WiringSnapshot
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator


@dataclass(frozen=True)
class ObservedFeed:
    """One participant an aggregator grew an input for, as the aggregator's own bookkeeping has it.

    A dynamic component keeps this record for every feed handed to it, whichever of the two
    add-APIs created it, which is what lets the recorder tell an aggregator's grown ports apart
    from its declared ones without knowing how the setup called them. The port label is kept only
    to find the wire that fills it; it is never written, because the two paths name it differently
    and the format derives the name from the participant instead.

    The tags arrive as one list mixing the participant's kind with the flow's semantics, exactly as
    the runtime lookups want them. Splitting them back into the two keys a file writes is the
    builder's job, not this module's: an observation records what is there.
    """

    port_label: str
    source_output: str
    tags: Tuple[Any, ...]
    weight: int


@dataclass(frozen=True)
class ObservedDispatch:
    """One control output an aggregator grew for a participant it does not merely measure.

    An aggregator that ranks a participant has to publish the power it wants that participant to
    draw, and both add-APIs record the created output here. The recorder needs it because a file
    writes the back-channel as part of the feed it belongs to, so the two have to be paired up
    again — by the wire the output feeds where there is one, and by the weight where there is not.

    ``consumer`` and ``target_input`` are filled in from the wiring rather than from the
    bookkeeping, because an output that nobody reads is legal: it is recorded for post-processing,
    and the format spells that case as a dispatch block without a target input.
    """

    port_label: str
    weight: int
    source_component_class: Optional[str]
    consumer: Optional[str]
    target_input: Optional[str]


@dataclass(frozen=True)
class ObservedComponent:
    """One component of the observed system: what it is, how it was configured and how it grew.

    The plain members are what a component entry is made of — the runtime name that becomes the
    file key, the importable path that becomes the ``class`` value, the short class name other
    components' declarations are keyed by, the live configuration the entry states, and whether the
    setup asked the simulator to apply the component's declared default connections. That flag is
    deliberately *not* the criterion for writing a bare input item, because a setup may add
    explicit wires on top of the automatic ones; it is kept because it explains a file to a reader
    of the diff.

    ``default_connections`` is the component's own declaration, flattened to the port pairs it
    stands for and keyed by the class of the source it applies to. Copying it into the observation
    is what lets the build step be a pure function of plain data: deciding whether a set of wires
    may be written as a bare item is a comparison against this table and nothing else.

    The two feed members are empty for every ordinary component and populated for an aggregator,
    which is the only structural distinction the format makes between the two.
    """

    name: str
    class_path: str
    class_name: str
    config: Any
    connect_automatically: bool
    default_connections: Mapping[str, Tuple[Tuple[str, str], ...]]
    feeds: Tuple[ObservedFeed, ...] = ()
    dispatches: Tuple[ObservedDispatch, ...] = ()

    def feed_labels(self) -> Tuple[str, ...]:
        """The aggregator input port names this component grew, in creation order.

        Returns:
            One label per feed; empty for a component that is not an aggregator.
        """
        return tuple(feed.port_label for feed in self.feeds)


@dataclass(frozen=True)
class RecordedSystem:
    """Everything one observation of a wired simulator produced, in registration order.

    The whole point of the type is that it is inert: it holds no simulator, no wrapper and no
    component, so the build step that turns it into a file cannot reach back into the runtime and
    is testable against hand-written data. The one exception is the configuration object each
    component entry carries, which the writer needs live in order to read its preset provenance.

    Registration order is preserved throughout, because it is the order the file writes its
    components in and therefore the order of the result frame's columns; nothing here is ever
    sorted, and no set iteration reaches the output.
    """

    setup: str
    components: Tuple[ObservedComponent, ...]
    wiring: WiringSnapshot
    simulation_parameters: SimulationParameters

    def by_name(self) -> Dict[str, ObservedComponent]:
        """Indexes the observed components by their runtime name.

        Returns:
            A fresh mapping in registration order; mutating it does not affect the observation.
        """
        return {component.name: component for component in self.components}

    def wires_into(self, target: str) -> Tuple[ResolvedWire, ...]:
        """Returns every wire arriving at one component, in the snapshot's canonical order.

        The recorder writes every connection at its consumer and therefore asks this question once
        per component and never the other way round.

        Args:
            target: Runtime name of the consuming component.

        Returns:
            The wires whose target is that component.
        """
        return tuple(wire for wire in self.wiring.wires if wire.target_component == target)


class SystemObserver:
    """Turns one wired simulator into a :class:`RecordedSystem` without touching it.

    Constructed over the simulator and asked once. The class exists rather than a single function
    because pairing an aggregator's dispatch outputs with the participants that read them needs an
    index of the whole wiring, and building that index once per observation instead of once per
    component is the difference between a linear and a quadratic pass over a fleet-sized system.
    """

    @classmethod
    def observe(cls, simulator: Simulator, setup: str = "") -> RecordedSystem:
        """Reads a simulator that has been prepared and connected into plain data.

        Args:
            simulator: The simulator after ``prepare_calculation()`` and
                ``connect_all_components()``; earlier than that the default connections are
                unresolved and the observation would be incomplete rather than wrong.
            setup: Name of the setup module being recorded, carried into messages.

        Returns:
            The observation, in registration order.
        """
        components = [wrapper.my_component for wrapper in simulator.wrapped_components]
        wiring = WiringSnapshot.from_components(components)
        consumers = cls._consumers_of(wiring)
        observed = tuple(
            cls._component(wrapper.my_component, wrapper.connect_automatically, consumers)
            for wrapper in simulator.wrapped_components
        )
        return RecordedSystem(
            setup=setup,
            components=observed,
            wiring=wiring,
            simulation_parameters=simulator.get_simulation_parameters(),
        )

    @classmethod
    def _consumers_of(cls, wiring: WiringSnapshot) -> Dict[Tuple[str, str], Tuple[str, str]]:
        """Indexes, per produced port, the single input that reads it.

        A dispatch output is paired with its participant through the wire it feeds, so the pairing
        needs the wiring read backwards. Only the first reader is kept: an output several inputs
        read is legal in HiSim but has no spelling in the format's dispatch block, and the wiring
        comparison the recorder's own validation performs is what would catch it.

        Args:
            wiring: The snapshot of the whole system.

        Returns:
            A mapping from ``(producing component, output)`` to ``(consuming component, input)``.
        """
        readers: Dict[Tuple[str, str], Tuple[str, str]] = {}
        for wire in wiring.wires:
            readers.setdefault(
                (wire.source_component, wire.source_output), (wire.target_component, wire.target_input)
            )
        return readers

    @classmethod
    def _component(
        cls,
        component: Component,
        connect_automatically: bool,
        consumers: Dict[Tuple[str, str], Tuple[str, str]],
    ) -> ObservedComponent:
        """Reads one component, adding its grown ports when it is an aggregator.

        Args:
            component: The component to read.
            connect_automatically: The wrapper's flag, recorded verbatim.
            consumers: The reverse index of the wiring, for pairing dispatch outputs.

        Returns:
            The observation of that component.
        """
        feeds: Tuple[ObservedFeed, ...] = ()
        dispatches: Tuple[ObservedDispatch, ...] = ()
        if isinstance(component, DynamicComponent):
            feeds = tuple(
                ObservedFeed(
                    port_label=entry.source_component_class,
                    source_output=entry.source_component_field_name,
                    tags=tuple(entry.source_tags),
                    weight=entry.source_weight,
                )
                for entry in component.my_component_inputs
            )
            dispatches = tuple(
                cls._dispatch(component.component_name, entry, consumers)
                for entry in component.my_component_outputs
            )
        return ObservedComponent(
            name=component.component_name,
            class_path=f"{type(component).__module__}.{type(component).__qualname__}",
            class_name=component.get_classname(),
            config=component.config,
            connect_automatically=connect_automatically,
            default_connections={
                source_class: tuple(
                    (connection.target_input_name, connection.source_output_name)
                    for connection in connections
                )
                for source_class, connections in component.default_connections.items()
            },
            feeds=feeds,
            dispatches=dispatches,
        )

    @classmethod
    def _dispatch(
        cls, owner: str, entry: Any, consumers: Dict[Tuple[str, str], Tuple[str, str]]
    ) -> ObservedDispatch:
        """Reads one grown output and finds the input, if any, that reads it.

        Args:
            owner: Runtime name of the aggregator that grew the output.
            entry: The aggregator's own record of that output.
            consumers: The reverse index of the wiring.

        Returns:
            The observation of that dispatch output.
        """
        reader = consumers.get((owner, entry.source_output_field_name))
        return ObservedDispatch(
            port_label=entry.source_output_field_name,
            weight=entry.source_weight,
            source_component_class=entry.source_component_class,
            consumer=reader[0] if reader is not None else None,
            target_input=reader[1] if reader is not None else None,
        )


def observe(simulator: Simulator, setup: str = "") -> RecordedSystem:
    """Reads a wired simulator into the plain data a recorded file is built from.

    The single entry point of the observation stage, kept as a function because that is how the
    recording pipeline reads: observe, build, emit. It changes nothing about the simulator, so a
    caller may observe a system and then run it, and the run produces exactly what it would have
    produced without the observation.

    Args:
        simulator: The simulator after ``prepare_calculation()`` and ``connect_all_components()``.
        setup: Name of the setup module being recorded, carried into messages.

    Returns:
        The observation, components in registration order.
    """
    return SystemObserver.observe(simulator, setup)
