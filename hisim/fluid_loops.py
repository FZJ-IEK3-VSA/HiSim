"""The loop walk: the build finds every fluid loop of a run from the wiring and names it after its pump owner.

A component tags the channels of each fluid loop it is a member of (:class:`hisim.energy_port.FluidLoop`); grouped by
loop they are its port sets (:meth:`hisim.component.Component.port_sets`), four channels each: the flow and the
temperature of the fluid arriving (inputs) and of the fluid leaving (outputs). Nothing declares which port sets form
one loop: the build follows the wiring (§3.3 of ``roadmap/fluid_loops_spec.md``, D7). From a port set, its
``flow_out`` output is read by the ``flow_in`` input of the next member; follow it, and on, until the start is reached
again. For example, in a house with a gas boiler and a buffer the boiler's ``space_heating`` set sends its flow to the
buffer's ``generator`` set, which sends it back: one loop of two members, ``CondensingGasBoiler.space_heating``.

:class:`FluidLoopWalk` indexes the wiring and walks it; :meth:`FluidLoopWalk.find_loops` names the loops. What a
wiring that is no loop breaks is judged by the fluid-loop setup rules (``hisim/setup_rules/fluid_loops.py``, ``FL-01``
to ``FL-08``), which read this walk and refuse such a run after the components are connected and before the first step
(:meth:`hisim.simulator.Simulator.run_all_timesteps`): a loop tagged by half, a tagged flow read through or fed from an
untagged channel, a flow read by two port sets, a loop that does not close, a port set wired by half, a temperature
wire that does not follow the flow, members that disagree on the carrier or the fluid, and a loop with no or two pump
owners.

A loop's id is ``<pump owner's component name>.<its loop name>``, for example ``hds.distribution``; it is unique in a
run, since component names are unique and a component pumps each of its loops at most once. The number of suppliers
(exactly one per loop) is checked with the energy ports (the setup rule ``EP-01``).
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from hisim.component import FluidLoopPortSet
from hisim.energy_port import FluidLoopChannel, LoopQuantity


@dataclass(frozen=True)
class FluidLoopWiring:

    """One fluid loop of a run as the build found it: its id and its members' port sets in the order of the flow.

    For example, the loop ``CondensingGasBoiler.space_heating`` has the port sets ``CondensingGasBoiler.space_heating``
    and ``SimpleHotWaterStorage.generator``: the boiler pumps, the buffer passes the flow back.

    Attributes:
        loop_id: The pump owner's ``component.loop``.
        port_sets: The members' port sets, the pump owner's first, then each one whose ``flow_in`` reads the flow out
            of the one before.
    """

    loop_id: str
    port_sets: Tuple[FluidLoopPortSet, ...]

    @property
    def pump_owner(self) -> FluidLoopPortSet:
        """The port set of the member that pumps the loop."""
        return self.port_sets[0]


class FluidLoopWalk:

    """The connections of a run's tagged channels, and the walk that finds its fluid loops from them.

    Built from the components after their inputs are connected: every connected input is indexed by the output it
    reads, every channel by the port set it belongs to. :meth:`find_loops` names the loops; the fluid-loop setup rules
    (``hisim/setup_rules/fluid_loops.py``) read the same index to judge the wiring.
    """

    def __init__(self, components: Sequence[Any]) -> None:
        """Group every component's tagged channels into port sets and index the wiring between them.

        Args:
            components: The components of the run, with their inputs connected.

        Raises:
            FluidLoopError: If a component tags a loop by half, which the setup rule ``FL-01`` refuses first.
        """
        self.port_sets: List[FluidLoopPortSet] = [port_set for component in components for port_set in component.port_sets()]
        #: The fluid-loop tag of every output, ``(component, output) -> tag``; None for an untagged output.
        self.output_tags: Dict[Tuple[str, str], Optional[FluidLoopChannel]] = {}
        #: The output every connected input reads, ``(component, input) -> (component, output)``.
        self.sources: Dict[Tuple[str, str], Tuple[str, str]] = {}
        #: The inputs that read each output, ``(component, output) -> [(component, input, the input's tag)]``.
        self.readers: Dict[Tuple[str, str], List[Tuple[str, str, Optional[FluidLoopChannel]]]] = {}
        #: The port set of every tagged input, ``(component, input) -> port set``.
        self.set_of_input: Dict[Tuple[str, str], FluidLoopPortSet] = {}
        for component in components:
            name = component.component_name
            for output in component.outputs:
                self.output_tags[(name, output.field_name)] = output.fluid_loop
            for component_input in component.inputs:
                if component_input.src_object_name is None or component_input.src_field_name is None:
                    continue
                source = (component_input.src_object_name, component_input.src_field_name)
                self.sources[(name, component_input.field_name)] = source
                self.readers.setdefault(source, []).append((name, component_input.field_name, component_input.fluid_loop))
        for port_set in self.port_sets:
            for quantity in (LoopQuantity.FLOW_IN, LoopQuantity.TEMPERATURE_IN):
                self.set_of_input[(port_set.component_name, port_set.channels[quantity].field_name)] = port_set

    @classmethod
    def find_loops(cls, components: Sequence[Any]) -> Tuple[FluidLoopWiring, ...]:
        """Find every fluid loop of a run from its wiring, each named after its pump owner.

        For example, a house whose boiler charges a buffer and whose buffer feeds a heat distribution system has the
        loops ``CondensingGasBoiler.space_heating`` and ``HeatDistributionSystem.distribution``. The walk judges
        nothing: it reads a wiring the fluid-loop setup rules accepted (``hisim/setup_rules/fluid_loops.py``). Given a
        wiring they refuse, it returns the loops it can name, and a closed loop with no or two pump owners, which has
        no id, is left out (``FL-08`` refuses it).

        Args:
            components: The components of the run, with their inputs connected.

        Returns:
            The loops, in the order of the run's port sets.

        Raises:
            FluidLoopError: If a component tags a loop by half, which the setup rule ``FL-01`` refuses first.
        """
        loops: List[FluidLoopWiring] = []
        for members in cls(components).closed_loops():
            pumping = [member for member in members if member.loop.pumps]
            if len(pumping) != 1:
                continue
            first = members.index(pumping[0])
            loops.append(FluidLoopWiring(pumping[0].label, tuple(members[first:] + members[:first])))
        return tuple(loops)

    def complete_port_sets(self) -> List[FluidLoopPortSet]:
        """The port sets that can be members of a loop: all four channels connected and the flow out read by one flow in.

        For example, a boiler and a tank linked both ways are complete; a third tank that reads the boiler's flow as
        well makes the boiler's set incomplete (its flow is read twice), and a boiler's hot-water side wired to nothing
        is idle, not complete.
        """
        return [port_set for port_set in self.port_sets if self.is_connected(port_set) and len(self.flow_readers(port_set)) <= 1]

    def closed_loops(self) -> List[List[FluidLoopPortSet]]:
        """The members of every closed loop of complete port sets, each in the order of the flow from the first one met.

        Each loop is listed once, starting at its member that comes first among the run's port sets. A walk that
        leaves the complete sets (:meth:`members_from`) is no closed loop and is not listed. For example, a boiler and
        a tank linked both ways give ``[[Boiler.space_heating, Tank.generator]]``.
        """
        complete = self.complete_port_sets()
        loops: List[List[FluidLoopPortSet]] = []
        assigned: Set[str] = set()
        for start in complete:
            if start.label in assigned:
                continue
            members = self.members_from(start, complete)
            if members is None:
                continue
            assigned.update(member.label for member in members)
            loops.append(members)
        return loops

    @staticmethod
    def channel_name(port_set: FluidLoopPortSet, quantity: LoopQuantity) -> str:
        """The channel of ``quantity`` as messages name it, ``component.channel``, for example ``"hds.WaterMassFlowHDS"``."""
        return f"{port_set.component_name}.{port_set.channels[quantity].field_name}"

    @staticmethod
    def members_label(members: Sequence[FluidLoopPortSet]) -> str:
        """The members of a loop as messages name them, for example ``"(boiler.space_heating, hds.distribution)"``."""
        return f"({', '.join(member.label for member in members)})"

    def flow_readers(self, port_set: FluidLoopPortSet) -> List[FluidLoopPortSet]:
        """The port sets whose ``flow_in`` input reads the ``flow_out`` output of ``port_set``."""
        key = (port_set.component_name, port_set.channels[LoopQuantity.FLOW_OUT].field_name)
        return [
            self.set_of_input[(reader, field_name)]
            for reader, field_name, tag in self.readers.get(key, [])
            if tag is not None and tag.quantity is LoopQuantity.FLOW_IN
        ]

    def flow_source(self, port_set: FluidLoopPortSet) -> Optional[Tuple[str, str]]:
        """The output the ``flow_in`` input of ``port_set`` reads, as ``(component, output)``; None if not connected."""
        return self.sources.get((port_set.component_name, port_set.channels[LoopQuantity.FLOW_IN].field_name))

    def is_read_by_a_member(self, port_set: FluidLoopPortSet, quantity: LoopQuantity, reading: LoopQuantity) -> bool:
        """Whether the output of ``quantity`` of ``port_set`` is read by an input tagged with ``reading``."""
        key = (port_set.component_name, port_set.channels[quantity].field_name)
        return any(tag is not None and tag.quantity is reading for _, _, tag in self.readers.get(key, []))

    def connected_channels(self, port_set: FluidLoopPortSet) -> Dict[LoopQuantity, bool]:
        """Whether each of the four channels of ``port_set`` is connected to another member of a loop.

        An input is connected when it reads an output; an output when an input of the matching quantity reads it (a
        ``flow_in`` its ``flow_out``, a ``temperature_in`` its ``temperature_out``). A controller that reads a
        temperature does not connect it.
        """
        name = port_set.component_name
        return {
            LoopQuantity.FLOW_IN: (name, port_set.channels[LoopQuantity.FLOW_IN].field_name) in self.sources,
            LoopQuantity.TEMPERATURE_IN: (name, port_set.channels[LoopQuantity.TEMPERATURE_IN].field_name) in self.sources,
            LoopQuantity.FLOW_OUT: self.is_read_by_a_member(port_set, LoopQuantity.FLOW_OUT, LoopQuantity.FLOW_IN),
            LoopQuantity.TEMPERATURE_OUT: self.is_read_by_a_member(
                port_set, LoopQuantity.TEMPERATURE_OUT, LoopQuantity.TEMPERATURE_IN
            ),
        }

    def is_connected(self, port_set: FluidLoopPortSet) -> bool:
        """Whether all four channels of ``port_set`` are connected, which makes it a member of a loop."""
        return all(self.connected_channels(port_set).values())

    def chain_through(self, port_set: FluidLoopPortSet) -> List[FluidLoopPortSet]:
        """The members a broken loop reaches from ``port_set``: back along its flow sources, then on along its readers.

        For example, a boiler whose flow a buffer reads while nobody reads the buffer's flow back gives
        ``[boiler.space_heating, buffer.generator]``.
        """
        flow_out_of = {
            (other.component_name, other.channels[LoopQuantity.FLOW_OUT].field_name): other for other in self.port_sets
        }
        chain = [port_set]
        current = port_set
        while True:
            source = self.flow_source(current)
            previous = flow_out_of.get(source) if source is not None else None
            if previous is None or previous in chain:
                break
            chain.insert(0, previous)
            current = previous
        current = port_set
        while True:
            readers = self.flow_readers(current)
            if len(readers) != 1 or readers[0] in chain:
                break
            chain.append(readers[0])
            current = readers[0]
        return chain

    def members_from(self, start: FluidLoopPortSet, complete: Sequence[FluidLoopPortSet]) -> Optional[List[FluidLoopPortSet]]:
        """The members of the loop through ``start``, in the order of the flow; None if the walk leaves the complete sets.

        Every complete set's ``flow_out`` is read by exactly one ``flow_in`` and its ``flow_in`` reads one output, so
        the walk returns to ``start``; a walk that reaches a set wired wrong stops, that set's fault being one the
        fluid-loop setup rules report.
        """
        members = [start]
        current = start
        while True:
            readers = self.flow_readers(current)
            if len(readers) != 1 or readers[0] not in complete:
                return None
            following = readers[0]
            if following == start:
                return members
            if following in members:
                return None
            members.append(following)
            current = following
