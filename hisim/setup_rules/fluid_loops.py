"""The fluid-loop rules: what the tags and the wiring of a built system must obey for every tagged port set to be a closed loop.

A component tags the four channels of each fluid loop it is a member of, its flow and temperature in and out
(:class:`hisim.energy_port.FluidLoop`); the build follows the wiring from each ``flow_out`` to the ``flow_in`` that
reads it until it is back at the start (:class:`hisim.fluid_loops.FluidLoopWalk`, §3.3 of
``roadmap/fluid_loops_spec.md``). The rules read the components of a run with their inputs connected
(``BuiltSystem``) and need no value of the run, so the build refuses a wiring that is no loop before the first step.
``FL-01`` checks that every loop a component tags has all four channels; every other rule of the family, and every
energy-port rule, groups the channels into port sets and so requires it. ``FL-02`` to ``FL-05`` judge each port set's
wiring, ``FL-06`` to ``FL-08`` each closed loop the walk finds. A port set with none of its four channels connected is
idle and breaks no rule, like the hot-water side of a boiler in a house without a tank.

Every rule but ``FL-01`` follows the wiring and requires the connection rules ``CB-04`` to ``CB-06``
(``ConnectionRules``, ``hisim/setup_rules/components.py``), so a broken connection is refused alone. The faults of
these rules alone raise ``FluidLoopWiringError``, the error the loop walk raised for them before they moved here. The
walk and the loop quantities are imported when a rule runs, not with the module, so that collecting the rules imports
no component module (``hisim/energy_port.py`` imports the components' configuration).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Dict, List, Optional, Sequence, Tuple, Type

from hisim.setup_rules.components import ConnectionRules
from hisim.setup_rules.rule import Fault, RuleStage, SetupRule, SetupRuleViolation
from hisim.setup_rules.subjects import BuiltSystem

if TYPE_CHECKING:
    from hisim.component import FluidLoopPortSet
    from hisim.energy_port import LoopQuantity
    from hisim.fluid_loops import FluidLoopWalk


class FluidLoopWiringError(SetupRuleViolation):
    """A run whose fluid-loop tags or wiring the build cannot make closed loops of, refused before it simulates.

    The registry of the setup rules raises it when every refusing fault of the build stage comes from the fluid-loop
    rules of this module, each fault with its code, its message and the component and channel it is found at. It is a
    ``ValueError``, as it was before the rules moved here.
    """


class FluidLoopRule(SetupRule[BuiltSystem]):
    """The shared base of the fluid-loop rules: the subject they read, the error they raise and the walk they index.

    It declares no ``CODE``, so it is no rule itself. Its rules read the built system's components with their inputs
    connected and raise ``FluidLoopWiringError``.
    """

    VIOLATION = FluidLoopWiringError

    @staticmethod
    def walk_of(system: BuiltSystem) -> FluidLoopWalk:
        """Return the loop walk of a built system: its port sets and the wiring between them, indexed.

        Example: in a house whose boiler charges a buffer, the walk holds the boiler's and the buffer's port sets and
        knows that the buffer's flow in reads the boiler's flow out.

        Args:
            system: The built system, whose components tag every loop whole (``FL-01``).

        Returns:
            The walk.
        """
        # The walk imports the component layer, which collecting the rules must not (the module docstring).
        from hisim.fluid_loops import FluidLoopWalk  # pylint: disable=import-outside-toplevel

        return FluidLoopWalk(system.components)

    @staticmethod
    def loop_quantity() -> Type[LoopQuantity]:
        """Return the enum of the four channels of a port set, ``LoopQuantity``, imported when a rule runs.

        Example: ``FluidLoopRule.loop_quantity().FLOW_OUT`` is the quantity of the channel through which a member sends
        the flow on. ``hisim/energy_port.py`` imports the components' configuration, which collecting the rules must
        not (the module docstring).
        """
        from hisim.energy_port import LoopQuantity  # pylint: disable=import-outside-toplevel

        return LoopQuantity

    @staticmethod
    def partly_connected(walk: FluidLoopWalk, port_set: FluidLoopPortSet) -> Optional[Dict[LoopQuantity, bool]]:
        """Return which channels of a port set are connected, when some are and some are not; ``None`` otherwise.

        A port set whose flow is read by two port sets is ``None`` as well: it breaks ``FL-03``, and the rules of a
        partly connected set (``FL-04``, ``FL-05``) do not judge it again. For example, a boiler whose flow a tank reads
        while nothing comes back gives ``{FLOW_IN: False, TEMPERATURE_IN: False, FLOW_OUT: True, TEMPERATURE_OUT:
        True}``; the same boiler linked both ways, or wired to nothing, gives ``None``.

        Args:
            walk: The walk of the system.
            port_set: One of its port sets.

        Returns:
            Whether each of the four channels is connected, or ``None`` for a set wired whole, idle or read twice.
        """
        if len(walk.flow_readers(port_set)) > 1:
            return None
        connected = walk.connected_channels(port_set)
        if not any(connected.values()) or all(connected.values()):
            return None
        return connected

    @staticmethod
    def open_channels(walk: FluidLoopWalk, port_set: FluidLoopPortSet, connected: Dict[LoopQuantity, bool]) -> str:
        """Return the channels of a port set that are connected to no other member, as a message names them.

        Example: ``"Boiler.FlowIn (flow_in), Boiler.TemperatureIn (temperature_in)"`` for a boiler whose flow and
        temperature in read nothing.

        Args:
            walk: The walk of the system.
            port_set: The port set.
            connected: Whether each channel is connected (:meth:`partly_connected`).

        Returns:
            The open channels, comma-separated, each with its quantity.
        """
        return ", ".join(
            f"{walk.channel_name(port_set, quantity)} ({quantity.value})" for quantity, is_connected in connected.items() if not is_connected
        )


class LoopTaggedWhole(FluidLoopRule):
    """What it checks: every fluid loop a component tags has all four channels, a flow in, a flow out, a temperature in and a temperature out.

    The channels are grouped by the loop their tags name (:meth:`hisim.component.FluidLoopPortSet.half_tagged`); a
    component without loop tags is not affected.

    Why: a member of a loop receives the fluid and sends it on. A port set without its flow out cannot pass the flow
    to the next member, and one without a temperature cannot tell the heat it carries; the walk cannot follow such a
    loop, and no rule after this one can group the component's port sets.

    Example of a fault: ``Tank tags the loop 'generator' by half: it tags FlowIn (flow_in), TemperatureIn
    (temperature_in), TemperatureOut (temperature_out) and lacks flow_out; a member of a fluid loop has a flow in, a
    flow out, a temperature in and a temperature out.``

    How to fix: tag the missing channel with the loop (``self.loop.flow_out`` and its siblings on ``add_input`` and
    ``add_output``), or remove the loop's tags from the component.
    """

    CODE = "FL-01"
    NAME = "loop-tagged-whole"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per loop a component tags by half, at the component."""
        # The port sets live with the component layer, which collecting the rules must not import (the module docstring).
        from hisim.component import FluidLoopPortSet  # pylint: disable=import-outside-toplevel

        return [
            self.fault(sentence, component=component.component_name)
            for component in subject.components
            for sentence in FluidLoopPortSet.half_tagged(component.component_name, [*component.inputs, *component.outputs])
        ]


class FarEndTagged(FluidLoopRule):
    """What it checks: a tagged flow leaves only for a ``flow_in`` channel of a loop, and arrives only from a ``flow_out`` channel of a loop.

    The flow out of every port set is read only by inputs tagged as a loop's flow in, and the flow in of every port
    set reads an output tagged as a loop's flow out.

    Why: the walk follows a loop along its tagged flows. A flow that reaches a channel which does not say it belongs
    to a loop leaves the walk there, so the loop could neither be found nor its heat be booked at both ends.

    Example of a fault: ``the loop Boiler.space_heating leaves Boiler.FlowOut for Meter.Flow, which carries no flow_in
    tag of a fluid loop``; ``the loop Tank.generator arrives at Tank.FlowIn from Meter.FlowCopy, which carries no
    flow_out tag of a fluid loop``.

    How to fix: tag the far end with the loop it belongs to, or read the flow from the member of the loop instead of
    from a component that is none.
    """

    CODE = "FL-02"
    NAME = "far-end-tagged"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # The walk groups every component's port sets, which a loop tagged by half cannot be grouped into.
    # The walk follows the wiring, which a broken connection (ConnectionRules) would mislead.
    REQUIRES = frozenset({"FL-01"}) | ConnectionRules.CODES

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per untagged reader of a tagged flow out and per untagged source of a tagged flow in."""
        walk = self.walk_of(subject)
        quantity = self.loop_quantity()
        faults: List[Fault] = []
        for port_set in walk.port_sets:
            name = port_set.component_name
            flow_out = port_set.channels[quantity.FLOW_OUT].field_name
            for reader, field_name, tag in walk.readers.get((name, flow_out), []):
                if tag is None or tag.quantity is not quantity.FLOW_IN:
                    faults.append(
                        self.fault(
                            f"the loop {port_set.label} leaves {name}.{flow_out} for {reader}.{field_name}, which carries no "
                            "flow_in tag of a fluid loop",
                            component=name,
                            port=flow_out,
                        )
                    )
            source = walk.flow_source(port_set)
            if source is None:
                continue
            source_tag = walk.output_tags.get(source)
            if source_tag is None or source_tag.quantity is not quantity.FLOW_OUT:
                faults.append(
                    self.fault(
                        f"the loop {port_set.label} arrives at {walk.channel_name(port_set, quantity.FLOW_IN)} from "
                        f"{'.'.join(source)}, which carries no flow_out tag of a fluid loop",
                        component=name,
                        port=port_set.channels[quantity.FLOW_IN].field_name,
                    )
                )
        return faults


class FlowReadOnce(FluidLoopRule):
    """What it checks: the flow out of a port set is read by the flow in of at most one port set.

    Why: one flow delivers its heat once. Two members reading one flow would each receive the full mass flow, so the
    water, and the heat it carries, would arrive twice; a split of one flow into branches is a valve component with one
    loop per branch.

    Example of a fault: ``the flow Boiler.FlowOut of the loop Boiler.space_heating is read by 2 port sets,
    Tank.generator, Twin.generator: one flow delivers its heat once; a split is a valve component with one loop per
    branch``.

    How to fix: connect the flow to one member, or put a valve between the flow and the branches, with one loop per
    branch.
    """

    CODE = "FL-03"
    NAME = "flow-read-once"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # The walk groups every component's port sets, which a loop tagged by half cannot be grouped into.
    # The walk follows the wiring, which a broken connection (ConnectionRules) would mislead.
    REQUIRES = frozenset({"FL-01"}) | ConnectionRules.CODES

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per flow out that the flow in of more than one port set reads, at that flow out."""
        walk = self.walk_of(subject)
        quantity = self.loop_quantity()
        faults: List[Fault] = []
        for port_set in walk.port_sets:
            readers = walk.flow_readers(port_set)
            if len(readers) > 1:
                faults.append(
                    self.fault(
                        f"the flow {walk.channel_name(port_set, quantity.FLOW_OUT)} of the loop {port_set.label} is read "
                        f"by {len(readers)} port sets, {', '.join(reader.label for reader in readers)}: one flow delivers its "
                        "heat once; a split is a valve component with one loop per branch",
                        component=port_set.component_name,
                        port=port_set.channels[quantity.FLOW_OUT].field_name,
                    )
                )
        return faults


class LoopCloses(FluidLoopRule):
    """What it checks: a port set that is wired at all has its flow out read by the next member and its flow in fed by the one before.

    A port set with none of its channels connected is idle and not affected; one whose flow is read twice is
    ``FL-03``'s.

    Why: the fluid of a loop goes round. A member whose flow goes nowhere, or that receives no flow, ends the loop
    open, and the heat its water carries would leave or enter the system through a channel no component books.

    Example of a fault: ``the loop through (Boiler.space_heating, Tank.generator) does not close: Boiler.FlowIn
    (flow_in) of its member Boiler.space_heating is connected to no other member of a loop; a flow_out is read by the
    flow_in of the next member and the flow returns to the first``.

    How to fix: connect the open flow channel to the member that sends or receives the flow, so that the flow returns
    to the first member, or disconnect the port set entirely if the loop is not used in this system.
    """

    CODE = "FL-04"
    NAME = "closes"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # The walk groups every component's port sets, which a loop tagged by half cannot be grouped into.
    # The walk follows the wiring, which a broken connection (ConnectionRules) would mislead.
    REQUIRES = frozenset({"FL-01"}) | ConnectionRules.CODES

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per partly connected port set whose flow out or flow in is open, at its component."""
        walk = self.walk_of(subject)
        quantity = self.loop_quantity()
        faults: List[Fault] = []
        for port_set in walk.port_sets:
            connected = self.partly_connected(walk, port_set)
            if connected is None or (connected[quantity.FLOW_OUT] and connected[quantity.FLOW_IN]):
                continue
            faults.append(
                self.fault(
                    f"the loop through {walk.members_label(walk.chain_through(port_set))} does not close: "
                    f"{self.open_channels(walk, port_set, connected)} of its member {port_set.label} is connected to no "
                    "other member of a loop; a flow_out is read by the flow_in of the next member and the flow returns to "
                    "the first",
                    component=port_set.component_name,
                )
            )
        return faults


class WiredWhole(FluidLoopRule):
    """What it checks: a port set whose two flows are connected has its two temperatures connected as well.

    A port set connects its four channels or none; one whose flows are open is ``FL-04``'s, one whose flow is read
    twice ``FL-03``'s.

    Why: a member books the heat of its loop from the flow and the two temperatures. With a temperature unconnected
    it would book heat on a supply or a return it never reads.

    Example of a fault: ``Boiler.space_heating is wired by half: its flows are connected and Boiler.TemperatureIn
    (temperature_in) is not; a member of a fluid loop connects its four channels or none``.

    How to fix: connect the temperature in to the temperature out of the member whose flow it reads, and the
    temperature out to the temperature in of the member that reads its flow.
    """

    CODE = "FL-05"
    NAME = "wired-whole"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # The walk groups every component's port sets, which a loop tagged by half cannot be grouped into.
    # The walk follows the wiring, which a broken connection (ConnectionRules) would mislead.
    REQUIRES = frozenset({"FL-01"}) | ConnectionRules.CODES

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per port set whose flows are connected and a temperature is not, at its component."""
        walk = self.walk_of(subject)
        quantity = self.loop_quantity()
        faults: List[Fault] = []
        for port_set in walk.port_sets:
            connected = self.partly_connected(walk, port_set)
            if connected is None or not (connected[quantity.FLOW_OUT] and connected[quantity.FLOW_IN]):
                continue
            faults.append(
                self.fault(
                    f"{port_set.label} is wired by half: its flows are connected and {self.open_channels(walk, port_set, connected)} "
                    "is not; a member of a fluid loop connects its four channels or none",
                    component=port_set.component_name,
                )
            )
        return faults


class TemperatureFollowsFlow(FluidLoopRule):
    """What it checks: in every closed loop, a member's temperature in reads the temperature out of the member whose flow it reads.

    Why: the fluid arriving at a member has the temperature it left the member before with. A temperature read from
    another member pairs one water's flow with another water's temperature, and the heat booked at both ends is wrong.

    Example of a fault: ``the temperature wire of the loop (Boiler.space_heating, Tank.generator) does not follow its
    flow: Tank.TemperatureIn reads Other.TemperatureOut, but the flow arrives from Boiler.space_heating, so it reads
    Boiler.TemperatureOut``.

    How to fix: connect the temperature in to the temperature out of the component that sends the flow, as the flow
    in is connected to its flow out.
    """

    CODE = "FL-06"
    NAME = "temperature-follows-flow"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # The walk groups every component's port sets, which a loop tagged by half cannot be grouped into.
    # The walk follows the wiring, which a broken connection (ConnectionRules) would mislead.
    REQUIRES = frozenset({"FL-01"}) | ConnectionRules.CODES

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per member of a closed loop whose temperature in reads another output, at that input."""
        walk = self.walk_of(subject)
        quantity = self.loop_quantity()
        faults: List[Fault] = []
        for members in walk.closed_loops():
            loop = walk.members_label(members)
            for position, member in enumerate(members):
                previous = members[position - 1]
                expected = (previous.component_name, previous.channels[quantity.TEMPERATURE_OUT].field_name)
                temperature_in = (member.component_name, member.channels[quantity.TEMPERATURE_IN].field_name)
                actual = walk.sources.get(temperature_in)
                if actual != expected:
                    faults.append(
                        self.fault(
                            f"the temperature wire of the loop {loop} does not follow its flow: "
                            f"{'.'.join(temperature_in)} reads {'.'.join(actual) if actual else 'nothing'}, but the flow "
                            f"arrives from {previous.label}, so it reads {'.'.join(expected)}",
                            component=member.component_name,
                            port=temperature_in[1],
                        )
                    )
        return faults


class OneCarrierOneFluid(FluidLoopRule):
    """What it checks: every member of a closed loop states the same carrier and the same fluid.

    Why: the members of one loop move one fluid that carries one kind of heat. A space-heating end wired to a hot-water
    end, or a water end to a brine end, is a wiring mistake that the flow alone cannot show, and the heat would be
    booked under two carriers or with two specific heats.

    Example of a fault: ``the loop (Boiler.space_heating, Tank.generator) is water at Boiler.space_heating and brine at
    Tank.generator: every member of a loop states the same fluid``.

    How to fix: connect the member to the loop of its own carrier and fluid, or correct the ``FluidLoop`` the
    component declares.
    """

    CODE = "FL-07"
    NAME = "one-carrier-one-fluid"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # The walk groups every component's port sets, which a loop tagged by half cannot be grouped into.
    # The walk follows the wiring, which a broken connection (ConnectionRules) would mislead.
    REQUIRES = frozenset({"FL-01"}) | ConnectionRules.CODES

    #: The statements of a ``FluidLoop`` every member of one loop agrees on, in the order the faults name them.
    AGREED: ClassVar[Tuple[str, ...]] = ("carrier", "fluid")

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per closed loop and statement its members disagree on, at the loop's first member."""
        walk = self.walk_of(subject)
        faults: List[Fault] = []
        for members in walk.closed_loops():
            loop = walk.members_label(members)
            for attribute in self.AGREED:
                stated = {member.label: getattr(member.loop, attribute).value for member in members}
                if len(set(stated.values())) > 1:
                    faults.append(
                        self.fault(
                            f"the loop {loop} is {' and '.join(f'{value} at {label}' for label, value in stated.items())}: "
                            f"every member of a loop states the same {attribute}",
                            component=members[0].component_name,
                        )
                    )
        return faults


class OnePumpOwner(FluidLoopRule):
    """What it checks: exactly one member of every closed loop pumps it (its ``FluidLoop`` states ``pumps=True``).

    Why: the pump owner sets the loop's mass flow, which every other member passes on, and the loop is named after it.
    Two pump owners would each set the flow, and a loop without one has no flow and no id.

    Example of a fault: ``the loop (Boiler.space_heating, Tank.generator) has 2 pumping port sets,
    Boiler.space_heating (Boiler.FlowOut) and Tank.generator (Tank.FlowOut); exactly one member of a loop pumps
    (pumps=True)``.

    How to fix: state ``pumps=True`` on the loop of the one member that moves the fluid, the generator or the heat
    distribution system, and ``pumps=False`` on every other member's.
    """

    CODE = "FL-08"
    NAME = "one-pump-owner"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # The walk groups every component's port sets, which a loop tagged by half cannot be grouped into.
    # The walk follows the wiring, which a broken connection (ConnectionRules) would mislead.
    REQUIRES = frozenset({"FL-01"}) | ConnectionRules.CODES

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per closed loop with no or more than one pumping member, at the loop's first member."""
        walk = self.walk_of(subject)
        quantity = self.loop_quantity()
        faults: List[Fault] = []
        for members in walk.closed_loops():
            pumping = [member for member in members if member.loop.pumps]
            if len(pumping) == 1:
                continue
            named = (
                ", " + " and ".join(f"{member.label} ({walk.channel_name(member, quantity.FLOW_OUT)})" for member in pumping)
                if pumping
                else f" (flows {', '.join(walk.channel_name(member, quantity.FLOW_OUT) for member in members)})"
            )
            faults.append(
                self.fault(
                    f"the loop {walk.members_label(members)} has {len(pumping)} pumping port sets{named}; exactly one member "
                    "of a loop pumps (pumps=True)",
                    component=members[0].component_name,
                )
            )
        return faults
