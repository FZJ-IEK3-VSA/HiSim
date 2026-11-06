"""The energy-port rules: what the wiring of a built system must obey for every declared energy port to find its other end.

The rules read the components of a run with their inputs connected (``BuiltSystem``) and need no value of the run,
so the build refuses a breach before the first step. Three of them are the fluid-loop rules of
:mod:`hisim.energy_port` (``EP-01`` to ``EP-03``): the loop ends of a run are resolved by the energy balance's
:class:`~hisim.postprocessing.energy_balance.ports.Wiring`, which the balance after the run reads again, so those
rules report what its resolution found. ``EP-04`` and ``EP-05`` are the two rules of the wiring of an
:class:`~hisim.energy_port.EnergyPort`: one receiver per transfer, and the two ports of one wire pair. ``EP-06`` is
the domain rule that heat released into the heated zone has a receiver, waived for a demo and a plant.

Every rule of the family reads the components' port sets, so each requires ``FL-01`` (a loop tagged whole,
``hisim/setup_rules/fluid_loops.py``) and is skipped when a component tags a loop by half; the loop rules
``EP-01`` and ``EP-02`` follow the loops' wiring and require every fluid-loop rule. Every rule reads the wiring and
requires the connection rules ``CB-04`` to ``CB-06`` (``ConnectionRules``, ``hisim/setup_rules/components.py``), so a
broken connection is refused alone. The faults of these rules alone
raise ``EnergyPortWiringError``, the error the build raised for them before they moved here. The energy balance's
wiring is imported when a rule runs, not with the module, so that collecting the rules imports no component module.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, FrozenSet, List, Optional, Sequence

from hisim import loadtypes as lt
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.components import ConnectionRules
from hisim.setup_rules.rule import Fault, RuleStage, SetupRule, SetupRuleViolation
from hisim.setup_rules.subjects import BuiltSystem

if TYPE_CHECKING:
    from hisim.energy_port import EnergyPort
    from hisim.postprocessing.energy_balance.ports import LoopFault, Wiring


class EnergyPortWiringError(SetupRuleViolation):
    """A run whose wiring breaks a rule of the energy ports, refused after the build and before it simulates.

    The registry of the setup rules raises it when every refusing fault of the build stage comes from the energy-port
    rules of this module, each fault with its code, its message and the component and port it is found at. It is a
    ``ValueError``, as it was before the rules moved here.
    """


class EnergyPortRule(SetupRule[BuiltSystem]):
    """The shared base of the energy-port rules: the subject they read, the error they raise and the wiring they index.

    It declares no ``CODE``, so it is no rule itself. Its rules read the built system's components with their inputs
    connected and raise ``EnergyPortWiringError``.
    """

    VIOLATION = EnergyPortWiringError

    #: The fluid-loop rules (``hisim/setup_rules/fluid_loops.py``), which the loop-wiring rules require: a loop end's
    #: other end and the readers of its flow are found along the loop's wiring, and on a wiring that is no loop they
    #: would report the fluid-loop rules' faults again, as faults of the loop.
    LOOP_RULES: ClassVar[FrozenSet[str]] = frozenset({"FL-01", "FL-02", "FL-03", "FL-04", "FL-05", "FL-06", "FL-07", "FL-08"})

    @staticmethod
    def wiring_of(system: BuiltSystem) -> Wiring:
        """Return the wiring of a built system, with its meters and its exempt components classified.

        Example: in a house whose energy manager reads the heat pump's electricity, the manager is exempt (it
        decides, it moves no energy), so the wiring never names it as the receiver of a port.

        Args:
            system: The built system.

        Returns:
            The wiring, indexed by each connected input and by the output it reads.
        """
        # The wiring imports the component layer, which collecting the rules must not (the module docstring).
        from hisim.postprocessing.energy_balance.ports import DeclaredPorts, Wiring  # pylint: disable=import-outside-toplevel

        metered_carriers, exempt = DeclaredPorts.classify(system.components)
        return Wiring(system.components, exempt, metered_carriers)

    @classmethod
    def faults_of(cls, found: Sequence[LoopFault]) -> List[Fault]:
        """Return the loop faults the wiring found as faults of this rule, located at their component and output.

        Args:
            found: The loop faults of one rule, from :class:`~hisim.postprocessing.energy_balance.ports.LoopWiring`.

        Returns:
            One fault per loop fault, in the same order.
        """
        return [cls.fault(loop_fault.message, component=loop_fault.component, port=loop_fault.port) for loop_fault in found]


class LoopPairing(EnergyPortRule):
    """What it checks: every loop end's other end tags the same loop, and exactly one of the two supplies it.

    A loop end is a component's port set of one fluid loop, its channels tagged with that loop. Its other end is
    the component that reads its mass flow, or whose flow it reads. When that component declares energy ports, it
    must tag a loop on the same three outputs (mass flow, supply and return temperature), and of the two ends exactly
    one states ``supplies=True``. An other end that declares no ports at all is no fault: the loop is drawn to it
    as undeclared.

    Why: both ends book the one heat series the loop carries, by the supplier's sign. An end whose partner books
    nothing, or a loop of two receivers or two suppliers, books the heat once or twice with the wrong sign, and
    the balance of both components cannot close.

    Example of a fault: ``Boiler tags its end (loop 'space_heating') of the loop {...}, but its other end, Tank,
    declares ports and not this loop``; ``the loop {...} between Boiler.space_heating and Tank.space_heating has
    two suppliers: exactly one member of a loop supplies it (supplies=True)``.

    How to fix: tag the other end's channels with a loop on the same outputs, and set ``supplies=True`` on the
    generator's loop and ``supplies=False`` on the receiving end's (``hisim/energy_port.py``, ``FluidLoop``).
    """

    CODE = "EP-01"
    NAME = "loop-pairing"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # An end on an exempt component or a meter is not resolved, so its partner's other end would be the wrong one; and
    # an end's other end is found along the loop's wiring, which the fluid-loop rules guard.
    REQUIRES = frozenset({"EP-03"}) | EnergyPortRule.LOOP_RULES | ConnectionRules.CODES

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per loop end that does not pair with its other end."""
        return self.faults_of(self.wiring_of(subject).loops(subject.components).pairing_faults)


class OneReaderPerFlow(EnergyPortRule):
    """What it checks: a loop's mass flow is read by at most one component, besides its owner, that tags a loop on it.

    Why: the heat of a loop is derived once from its mass flow and its two temperatures. Two loop ends on one
    flow would each book that heat, so the water's heat would be delivered twice. A split of one flow into two
    branches is a valve component with one loop per branch.

    Example of a fault: ``the mass flow Boiler.Flow is read by 2 components that declare a loop on it (Tank, Twin):
    one flow delivers its heat once, a split needs a valve with one loop per branch``.

    How to fix: let one of the readers tag the loop and leave the other's inputs untagged, or put a valve between
    the flow and the two readers.
    """

    CODE = "EP-02"
    NAME = "one-reader-per-flow"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # An end on an exempt component or a meter is not resolved and would be missing from the count; and the readers of
    # a flow are found along the loop's wiring, which the fluid-loop rules guard.
    REQUIRES = frozenset({"EP-03"}) | EnergyPortRule.LOOP_RULES | ConnectionRules.CODES

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per mass flow that several loop ends read."""
        return self.faults_of(self.wiring_of(subject).loops(subject.components).shared_flow_faults)


class NoLoopOnANode(EnergyPortRule):
    """What it checks: no component that is exempt from the energy balance, and no meter, declares a loop end.

    A component is exempt when it moves no energy, a controller that only decides; a meter is a node that counts
    what passes it. Neither has water of its own to move.

    Why: the balance never takes an exempt component or a meter as the peer of a port, so a loop end on one has
    no other end to pair with, and the heat it would carry vanishes from the balance.

    Example of a fault: ``EnergyManager declares the fluid loop 'space_heating', but it is exempt from the energy
    balance or a meter: it moves no water of its own.``

    How to fix: remove the loop tags from the controller's or meter's channels and tag the loop on the device that
    pumps or heats the water.
    """

    CODE = "EP-03"
    NAME = "no-loop-on-a-node"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # The wiring groups every component's port sets, which a loop tagged by half cannot be grouped into.
    REQUIRES = frozenset({"FL-01"}) | ConnectionRules.CODES

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per loop end an exempt component or a meter declares."""
        return self.faults_of(self.wiring_of(subject).loops(subject.components).node_end_faults)


class ReceiverUnambiguous(EnergyPortRule):
    """What it checks: the wiring names one receiver for every output energy port that is not metered and names no environment.

    An output port read through inputs carrying a port it pairs with must be read so by one component. A
    ``PRODUCTION`` port read by more than one component that declares ports must be read by exactly one of them
    through an input carrying a port it pairs with, which is then its receiver (owner decision of 2026-10-02, hydronic
    stage E, rule (a)). A metered port is not affected: its peer is a meter of its carrier first.

    Why: the balance books a transfer between the port and its one receiver. With several candidates it would book
    the energy to the first in the order of the run, silently, or receive one energy twice.

    Example of a fault: ``the energy port Source.Heat (production, space_heating_heat) is read by 2 components that
    declare ports (First, Second) and none of them tags its reading input: tag the receiving input with an energy
    port``.

    How to fix: tag the input of the one component that receives the energy with an ``EnergyPort`` of the opposite
    role and the same carrier, and leave the other readers' inputs untagged.
    """

    CODE = "EP-04"
    NAME = "receiver-unambiguous"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # The wiring groups every component's port sets, which a loop tagged by half cannot be grouped into.
    REQUIRES = frozenset({"FL-01"}) | ConnectionRules.CODES

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per output port whose receiver the wiring cannot tell among several readers."""
        wiring = self.wiring_of(subject)
        faults: List[Fault] = []
        for component in subject.components:
            for output in component.outputs:
                message = self.ambiguity(wiring, component.component_name, output.field_name, output.energy_port)
                if message is not None:
                    faults.append(self.fault(message, component=component.component_name, port=output.field_name))
        return faults

    @staticmethod
    def ambiguity(wiring: Wiring, component_name: str, field_name: str, port: Optional[EnergyPort]) -> Optional[str]:
        """Return why one output's receiver is ambiguous, or ``None`` when the wiring names at most one.

        Example: a distribution system's heat read by the building and by a second heat sink that declares ports,
        neither tagging its reading input, gives the sentence ``the energy port HDS.Heat (...) is read by 2 components
        that declare ports (Building, Sink) and none of them tags its reading input: ...``.

        Args:
            wiring: The wiring of the system.
            component_name: The output's component.
            field_name: The output.
            port: The output's energy port, ``None`` when it declares none.

        Returns:
            The sentence of the fault, or ``None``.
        """
        if port is None or port.is_metered or port.environment is not None:
            return None
        key = (component_name, field_name)
        paired = wiring.paired_readers(component_name, field_name, port)
        if len(paired) > 1:
            return (
                f"the energy port {'.'.join(key)} ({port.role.value}, {port.carrier.value}) is read through "
                f"inputs carrying a matching port by {len(paired)} components ({', '.join(paired)}): one "
                "energy is received once"
            )
        if paired or port.role != lt.EnergyRole.PRODUCTION:
            return None
        readers = [name for name in wiring.readers.get(key, []) if wiring.moves_energy(name) and wiring.declared.get(name, False)]
        if len(readers) > 1:
            return (
                f"the energy port {'.'.join(key)} ({port.role.value}, {port.carrier.value}) is read by "
                f"{len(readers)} components that declare ports ({', '.join(readers)}) and none of them tags "
                "its reading input: tag the receiving input with an energy port"
            )
        return None


class InputPortPairs(EnergyPortRule):
    """What it checks: an input energy port pairs with the energy port of the output it reads, when that output declares one.

    The two ports of one wire book the two ends of one transfer, so they state the same carrier and opposite exchange
    roles (``EnergyPort.pairs_with``). An input port reading an output without a port is no fault here: when that
    output's component declares ports, the balance after the run finds the transfer booked on one side only.

    Why: a building's ``CONSUMPTION`` of space heating reading an output declared as ``PRODUCTION`` of cooling books a
    transfer the sender never made; the balance would pair nothing and both components would fail to close.

    Example of a fault: ``the input Loop.HeatIn (production, space_heating_heat) reads the energy port Tank.HeatOut
    (production, space_heating_heat): the two ports of one wire state the same carrier, one consumption and the other
    production``.

    How to fix: correct the role or the carrier of one of the two ports, or connect the input to the output that
    carries what it books.
    """

    CODE = "EP-05"
    NAME = "input-port-pairs"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # The wiring groups every component's port sets, which a loop tagged by half cannot be grouped into.
    REQUIRES = frozenset({"FL-01"}) | ConnectionRules.CODES

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per input port that reads an output port it does not pair with."""
        wiring = self.wiring_of(subject)
        faults: List[Fault] = []
        for (source, output), readers in wiring.port_readers.items():
            sent = wiring.output_ports.get((source, output))
            if sent is None:
                continue
            for reader, field_name, port in readers:
                if not sent.pairs_with(port):
                    faults.append(
                        self.fault(
                            f"the input {reader}.{field_name} ({port.role.value}, {port.carrier.value}) reads the energy "
                            f"port {source}.{output} ({sent.role.value}, {sent.carrier.value}): the two ports of one wire "
                            "state the same carrier, one consumption and the other production",
                            component=reader,
                            port=field_name,
                        )
                    )
        return faults


class RoomHeatNeedsReceiver(EnergyPortRule):
    """What it checks: every output declaring ``PRODUCTION`` of ``ROOM_HEAT`` is read by a component that moves energy.

    Such a port says its heat went into the heated zone: the residents' body heat and the heat of their devices
    (``HeatingByResidents``, ``HeatingByDevices`` of the occupancy), or a device installed in the heated zone. A port
    that names its ``environment`` is not affected: its boundary node takes the heat. A reader that is exempt from the
    balance or a meter does not count (owner decision D-h of ``roadmap/device_location_spec.md``).

    Why: room heat is a transfer into the heated zone, not a loss. An output nobody reads is a transfer with a missing
    end: the heat would vanish from the balance and from the building it should warm.

    Example of a fault: ``room heat needs a receiver: UTSPConnector.HeatingByResidents declares PRODUCTION of
    ROOM_HEAT and no component reads it; list the component under the inputs of the building whose heated zone it
    heats``.

    How to fix: list the component under the inputs of the building whose heated zone it heats, or install the device
    in an unheated space, so that its heat leaves as a loss. A demo or a plant does not run the rule.
    """

    CODE = "EP-06"
    NAME = "room-heat-needs-receiver"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # An input port must pair with the output it reads before it counts as that output's receiver; and the wiring
    # groups every component's port sets, which a loop tagged by half cannot be grouped into.
    REQUIRES = frozenset({"EP-05", "FL-01"}) | ConnectionRules.CODES
    WAIVED_FOR = MappingProxyType(
        {
            SetupKind.DEMO: "a demo may run an occupancy without a building; the heat it does not place is reported as unlinked",
            SetupKind.PLANT: "a plant has no heated zone; heat a device releases there has no receiver by construction",
        }
    )

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per room-heat output that no component reads, naming the component and the output."""
        wiring = self.wiring_of(subject)
        faults: List[Fault] = []
        for component in subject.components:
            for output in component.outputs:
                port = output.energy_port
                if not self.releases_room_heat(port):
                    continue
                assert port is not None  # releases_room_heat holds only for a port
                if wiring.component_peer(component.component_name, output.field_name, port) is None:
                    faults.append(
                        self.fault(
                            f"room heat needs a receiver: {component.component_name}.{output.field_name} declares "
                            f"{lt.EnergyRole.PRODUCTION.name} of {lt.EnergyBalanceCarrier.ROOM_HEAT.name} and no component "
                            "reads it; list the component under the inputs of the building whose heated zone it heats",
                            component=component.component_name,
                            port=output.field_name,
                        )
                    )
        return faults

    @staticmethod
    def releases_room_heat(port: Optional[EnergyPort]) -> bool:
        """Return whether an output port releases heat into the heated zone: ``PRODUCTION`` of ``ROOM_HEAT``, no environment.

        Example: the occupancy's ``EnergyPort(PRODUCTION, ROOM_HEAT)`` does; the same port naming the environment
        ``OUTDOORS`` does not, nor the building's ``EnergyPort(LOSS, ROOM_HEAT)``, nor an output without a port.

        Args:
            port: The output's energy port, ``None`` when it declares none.

        Returns:
            Whether the rule applies to the output.
        """
        return (
            port is not None
            and port.role == lt.EnergyRole.PRODUCTION
            and port.carrier == lt.EnergyBalanceCarrier.ROOM_HEAT
            and port.environment is None
        )
