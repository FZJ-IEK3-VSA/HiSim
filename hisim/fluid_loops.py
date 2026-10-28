"""The loop walk: the build finds every fluid loop of a run from the wiring and refuses one that is no loop.

A component tags the channels of each fluid loop it is a member of (:class:`hisim.energy_port.FluidLoop`); grouped by
loop they are its port sets (:meth:`hisim.component.Component.port_sets`), four channels each: the flow and the
temperature of the fluid arriving (inputs) and of the fluid leaving (outputs). Nothing declares which port sets form
one loop: the build follows the wiring (§3.3 of ``roadmap/fluid_loops_spec.md``, D7). From a port set, its
``flow_out`` output is read by the ``flow_in`` input of the next member; follow it, and on, until the start is reached
again. For example, in a house with a gas boiler and a buffer the boiler's ``space_heating`` set sends its flow to the
buffer's ``generator`` set, which sends it back: one loop of two members, ``CondensingGasBoiler.space_heating``.

:meth:`FluidLoopWalk.find_loops` runs after the components are connected and before the first step
(:meth:`hisim.simulator.Simulator.run_all_timesteps`) and refuses, with :class:`hisim.hydronics.FluidLoopWiringError`,
every wiring that is no loop:

* a tagged flow that leaves through or arrives from a channel without a fluid-loop tag;
* a flow read by the ``flow_in`` of two port sets (one flow delivers its heat once; a split is a valve component with
  one loop per branch);
* a loop that does not close: a port set wired at all whose ``flow_out`` nobody reads or whose ``flow_in`` is not
  connected;
* a port set wired by half: its flows connected and a temperature not. A port set with none of its four channels
  connected is idle and allowed, like the hot-water side of a boiler in a house without a tank;
* a temperature wire that does not follow the flow wire;
* members of one loop that state different carriers or different fluids;
* a loop with no or two pump owners (members whose loop states ``pumps=True``).

A loop's id is ``<pump owner's component name>.<its loop name>``, for example ``hds.distribution``; it is unique in a
run, since component names are unique and a component pumps each of its loops at most once. The number of suppliers
(exactly one per loop) is checked with the energy ports (:meth:`hisim.postprocessing.energy_balance.ports.Wiring.circuits`).
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from hisim import hydronics
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
    reads, every channel by the port set it belongs to. :meth:`find_loops` is the entry point.
    """

    def __init__(self, components: Sequence[Any]) -> None:
        """Group every component's tagged channels into port sets and index the wiring between them.

        Args:
            components: The components of the run, with their inputs connected.

        Raises:
            FluidLoopWiringError: If a component tags a loop by half.
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
        """Find every fluid loop of a run from its wiring, or refuse the run naming every wiring that is no loop.

        For example, a house whose boiler charges a buffer and whose buffer feeds a heat distribution system has the
        loops ``CondensingGasBoiler.space_heating`` and ``HeatDistributionSystem.distribution``.

        Args:
            components: The components of the run, with their inputs connected.

        Returns:
            The loops, in the order of the run's port sets.

        Raises:
            FluidLoopWiringError: Naming every breach of the rules of the module docstring, each with the loop's
                members and the channel concerned.
        """
        walk = cls(components)
        faults = walk.untagged_far_end_faults()
        complete: List[FluidLoopPortSet] = []
        for port_set in walk.port_sets:
            fault = walk.connection_fault(port_set)
            if fault is not None:
                faults.append(fault)
            elif walk.is_connected(port_set):
                complete.append(port_set)
        loops: List[FluidLoopWiring] = []
        assigned: Set[str] = set()
        for start in complete:
            if start.label in assigned:
                continue
            members = walk.members_from(start, complete)
            if members is None:
                continue
            assigned.update(member.label for member in members)
            loop_faults = walk.loop_faults(members)
            if loop_faults:
                faults.extend(loop_faults)
                continue
            (pump_owner,) = [member for member in members if member.loop.pumps]
            first = members.index(pump_owner)
            loops.append(FluidLoopWiring(pump_owner.label, tuple(members[first:] + members[:first])))
        if faults:
            raise hydronics.FluidLoopWiringError(
                "The fluid loops of this run are wired wrong (§3.3 of roadmap/fluid_loops_spec.md):\n"
                + "\n".join(f"- {fault}" for fault in dict.fromkeys(faults))
            )
        return tuple(loops)

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

    def untagged_far_end_faults(self) -> List[str]:
        """A fault for every tagged flow that leaves through or arrives from a channel without a fluid-loop tag.

        For example, a heat distribution system's ``flow_out`` read by a boiler's input that carries no tag: the
        loop's flow would reach a member that does not say it is one, so the walk could not follow it.
        """
        faults: List[str] = []
        for port_set in self.port_sets:
            flow_out = port_set.channels[LoopQuantity.FLOW_OUT].field_name
            for reader, field_name, tag in self.readers.get((port_set.component_name, flow_out), []):
                if tag is None or tag.quantity is not LoopQuantity.FLOW_IN:
                    faults.append(
                        f"the loop {port_set.label} leaves {port_set.component_name}.{flow_out} for "
                        f"{reader}.{field_name}, which carries no flow_in tag of a fluid loop"
                    )
            source = self.flow_source(port_set)
            if source is None:
                continue
            source_tag = self.output_tags.get(source)
            if source_tag is None or source_tag.quantity is not LoopQuantity.FLOW_OUT:
                faults.append(
                    f"the loop {port_set.label} arrives at {self.channel_name(port_set, LoopQuantity.FLOW_IN)} from "
                    f"{'.'.join(source)}, which carries no flow_out tag of a fluid loop"
                )
        return faults

    def connection_fault(self, port_set: FluidLoopPortSet) -> Optional[str]:
        """Why ``port_set`` cannot be a member of a loop, or None when it is wired whole or not at all.

        Its ``flow_out`` read by two port sets, a flow link missing (the loop does not close) and a temperature
        missing beside connected flows (wired by half) are faults; a set with no channel connected is idle.
        """
        readers = self.flow_readers(port_set)
        if len(readers) > 1:
            return (
                f"the flow {self.channel_name(port_set, LoopQuantity.FLOW_OUT)} of the loop {port_set.label} is read "
                f"by {len(readers)} port sets, {', '.join(reader.label for reader in readers)}: one flow delivers its "
                "heat once; a split is a valve component with one loop per branch"
            )
        connected = self.connected_channels(port_set)
        if not any(connected.values()) or all(connected.values()):
            return None
        open_channels = ", ".join(
            f"{self.channel_name(port_set, quantity)} ({quantity.value})"
            for quantity, is_connected in connected.items()
            if not is_connected
        )
        if not (connected[LoopQuantity.FLOW_OUT] and connected[LoopQuantity.FLOW_IN]):
            return (
                f"the loop through {self.members_label(self.chain_through(port_set))} does not close: {open_channels} "
                f"of its member {port_set.label} is connected to no other member of a loop; a flow_out is read by "
                "the flow_in of the next member and the flow returns to the first"
            )
        return (
            f"{port_set.label} is wired by half: its flows are connected and {open_channels} is not; a member of a "
            "fluid loop connects its four channels or none"
        )

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
        the walk returns to ``start``; a walk that reaches a set wired wrong stops, that set's fault being reported
        already.
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

    def loop_faults(self, members: Sequence[FluidLoopPortSet]) -> List[str]:
        """The faults of one closed loop: its temperature wires, the agreement of its members, its pump owner.

        Each member's ``temperature_in`` reads the ``temperature_out`` of the member whose flow it reads; every
        member states the same carrier and the same fluid; exactly one member pumps (rule 3 of §3.3).

        Args:
            members: The loop's port sets in the order of the flow, as :meth:`members_from` returns them.

        Returns:
            One sentence per fault, empty for a loop that is wired right.
        """
        faults: List[str] = []
        loop = self.members_label(members)
        for position, member in enumerate(members):
            previous = members[position - 1]
            expected = (previous.component_name, previous.channels[LoopQuantity.TEMPERATURE_OUT].field_name)
            temperature_in = (member.component_name, member.channels[LoopQuantity.TEMPERATURE_IN].field_name)
            actual = self.sources.get(temperature_in)
            if actual != expected:
                faults.append(
                    f"the temperature wire of the loop {loop} does not follow its flow: "
                    f"{'.'.join(temperature_in)} reads {'.'.join(actual) if actual else 'nothing'}, but the flow "
                    f"arrives from {previous.label}, so it reads {'.'.join(expected)}"
                )
        for attribute in ("carrier", "fluid"):
            stated = {member.label: getattr(member.loop, attribute).value for member in members}
            if len(set(stated.values())) > 1:
                faults.append(
                    f"the loop {loop} is {' and '.join(f'{value} at {label}' for label, value in stated.items())}: "
                    f"every member of a loop states the same {attribute}"
                )
        pumping = [member for member in members if member.loop.pumps]
        if len(pumping) != 1:
            named = (
                ", " + " and ".join(f"{member.label} ({self.channel_name(member, LoopQuantity.FLOW_OUT)})" for member in pumping)
                if pumping
                else f" (flows {', '.join(self.channel_name(member, LoopQuantity.FLOW_OUT) for member in members)})"
            )
            faults.append(
                f"the loop {loop} has {len(pumping)} pumping port sets{named}; exactly one member of a loop pumps "
                "(pumps=True)"
            )
        return faults
