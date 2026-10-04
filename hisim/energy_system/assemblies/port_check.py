"""The post-construction port check: what the lowered ports mean, confirmed on the constructed components.

``assemblies_spec.md`` §3.2 and §3.3. The expansion of imports binds every port from the files
alone — every entry states its class, so which component is a candidate partner, whether a port has
none or several, and whether a verb decides it, are known before anything is built — and lowers a
need to a bare partner name or to explicit wires. What a bare name *means*, the member's default
connections from the partner's class, and whether a wire's input and output exist, only the
component itself says: HiSim creates a component's inputs, outputs and default connections in its
constructor, and there is no second declaration of them.

:meth:`EnergySystemExecutor.build <hisim.energy_system.executor.EnergySystemExecutor.build>`
therefore runs this check as a stage of its own, after every component is constructed and before
any wire is planned or connected. It reads the port-provenance table the expansion wrote
(:class:`~hisim.energy_system.assemblies.record.PortProvenance`) — from the import record on a run,
from the realized record's metadata on a re-run — and the constructed instances, never constructing
anything a second time:

- a **bare name** (``default``) needs the member to declare default connections, or default feeds
  of an aggregator, from the partner's class (``EF-7H``), exactly what the wiring stage expands the
  bare name through;
- a **wire** needs the named input on the member and the named output on the partner (``EF-7J``);
- a **provided output** needs the named output on the member providing it (``EF-7J``);
- a **circuit** (its items are bare names of the other end's members at every member carrying the
  circuit's placeholder) needs each of the circuit's three outputs owned by exactly one member of
  the two ends, read by a member of the other end of the same load type and unit, every reader
  carrying the placeholder and declaring default connections from the owner's class (``EF-7H``),
  and every bare name naming an owner of an output its reader reads (``EF-7N``);
- an **observe** item (one output an observer's selection matched) needs the output on the
  participant, a channel of the observer whose tags the feed carries and whose load type and unit
  accept the output (``EF-7J``, ``EF-7Q``), and, for a dispatch target, that input on the participant
  on a channel that allows a dispatch (``EF-7J``, ``EF-7U``);
- a **feed** (a carrier need's consuming output) needs the output on the consumer, an energy port
  of the need's carrier on it (``EF-7Q``) and, for a fuel, a default feed of exactly the named
  outputs on the provider's meter from the consumer's class, through which the meter's bare name
  of the consumer is expanded (``EF-7H``, ``EF-7J``).

Every refusal is an :class:`~hisim.energy_system.errors.EnergySystemAssemblyError` whose message
names the import (and instance), the port, the member, the partner by name and class, the files and
lines the port came from, the ports or classes the component does declare, and ends in the same
paste-ready verb lines the expansion offers. Whether the two ends of a wire agree in load type and
unit stays where it was, in the wiring stage's ``EF-30`` check, which every lowered wire passes
through. Nothing simulates after a refusal.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Mapping, Sequence, Set, Tuple

from hisim import loadtypes as lt
from hisim.component import Component
from hisim.config.channels import ConnectionTag, DispatchRule, PortTypeCompatibility
from hisim.energy_system.assemblies.record import LoweredKind, LoweredPort
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.energy_system.imports_model import CircuitNaming
from hisim.energy_system.wiring_checks import find_input, find_output


class LoweredPortCheck:
    """Checks every entry of a port-provenance table against the constructed components."""

    def __init__(self, provenance: Sequence[LoweredPort], components: Mapping[str, Component]) -> None:
        """Prepares the check.

        Args:
            provenance: The port-provenance table, in lowering order.
            components: The constructed components by their name in the expanded file.
        """
        self.provenance = provenance
        self.components = components
        self._circuits_checked: Set[Tuple[str, FrozenSet[str]]] = set()
        self._feeds_checked: Set[Tuple[str, str]] = set()

    def check(self) -> None:
        """Checks every entry, in lowering order, and raises at the first that does not fit.

        Raises:
            EnergySystemAssemblyError: ``EF-7H`` for a bare name, a circuit item or a fuel feed the
                receiving component declares no defaults for, ``EF-7J`` for a wire, a provided output
                or a consuming output naming a port that does not exist, for a meter that would observe
                outputs no need names and for an entry naming a component the system does not hold,
                ``EF-7N`` for circuit ends that do not own and read the circuit's outputs, ``EF-7Q``
                for a consuming output of another carrier or an observed output of another load type
                or unit than its channel's, ``EF-7U`` for a dispatch on a channel that forbids one.
        """
        for entry in self.provenance:
            if entry.kind == LoweredKind.FEED:
                self._check_feed(entry)
                continue
            member = self._component(entry, entry.member)
            if entry.kind == LoweredKind.PROVIDED:
                self._check_provided(entry, member)
                continue
            partner = self._component(entry, entry.partner)
            if entry.kind == LoweredKind.DEFAULT:
                self._check_default(entry, member, partner)
            elif entry.kind == LoweredKind.CIRCUIT:
                self._check_circuit(entry)
                self._check_circuit_item(entry, member, partner)
            elif entry.kind == LoweredKind.OBSERVE:
                self._check_observe(entry, member, partner)
            else:
                self._check_wire(entry, member, partner)

    def _component(self, entry: LoweredPort, name: str) -> Component:
        """The constructed component an entry names; a table naming another is a defect, refused."""
        component = self.components.get(name)
        if component is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"port '{entry.port}' lowered an item for '{name}', which the built system does not hold "
                f"{entry.source_text()}.",
                alternatives=sorted(self.components),
                alternatives_label="components",
                offending_value=name,
            )
        return component

    @staticmethod
    def _class_name(component: Component) -> str:
        """The class name default connections are keyed by."""
        return str(component.get_classname())

    def _check_default(self, entry: LoweredPort, member: Component, partner: Component) -> None:
        """A bare name: the member declares default connections (or default feeds) from the partner's class."""
        partner_class = self._class_name(partner)
        if member.default_connections.get(partner_class) or DynamicConnectionResolver.default_feeds_of(
            member, partner_class
        ):
            return
        declared = sorted(
            set(member.default_connections)
            | set(getattr(member, DynamicConnectionResolver.DEFAULT_FEEDS_ATTRIBUTE, None) or {})
        )
        raise EnergySystemAssemblyError(
            EnergySystemErrorId.PARTNER_WITHOUT_DEFAULT_CONNECTIONS,
            entry.owner,
            f"port '{entry.port}' is bound to {entry.partner} ({partner_class}), but {entry.member} "
            f"({self._class_name(member)}) declares no default connections from {partner_class} "
            f"{entry.source_text()}.",
            alternatives=declared,
            alternatives_label="classes it declares default connections from",
            remedy="Bind a partner of one of those classes, or add the default connection to the class. "
            + entry.remedy,
        )

    def _check_wire(self, entry: LoweredPort, member: Component, partner: Component) -> None:
        """A wire: the member has the input, the partner has the output."""
        if find_input(member, entry.input) is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"port '{entry.port}' wires '{entry.input}', which is no input of {entry.member} "
                f"({self._class_name(member)}) {entry.source_text()}.",
                alternatives=[port.field_name for port in member.inputs],
                alternatives_label="inputs",
                offending_value=entry.input,
                remedy=entry.remedy or None,
            )
        if find_output(partner, entry.output) is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"port '{entry.port}' wires '{entry.input}' from '{entry.output}', which is no output of "
                f"{entry.partner} ({self._class_name(partner)}) {entry.source_text()}.",
                alternatives=[port.field_name for port in partner.outputs],
                alternatives_label="outputs",
                offending_value=entry.output,
                remedy=entry.remedy or None,
            )

    def _check_provided(self, entry: LoweredPort, member: Component) -> None:
        """A provided output: the member providing it has it."""
        if find_output(member, entry.output) is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"port '{entry.port}' provides '{entry.output}', which is no output of {entry.member} "
                f"({self._class_name(member)}) {entry.source_text()}.",
                alternatives=[port.field_name for port in member.outputs],
                alternatives_label="outputs",
                offending_value=entry.output,
            )

    # ----------------------------------------------------------------------------------------- circuits

    def _check_circuit(self, entry: LoweredPort) -> None:
        """Checks one bound circuit as a whole, once: ownership, readers, types, placeholders, defaults."""
        key = (entry.circuit, frozenset((entry.end, entry.other_end)))
        if key in self._circuits_checked:
            return
        self._circuits_checked.add(key)
        ends: Dict[str, Tuple[str, ...]] = {entry.end: entry.end_members, entry.other_end: entry.other_members}
        landed: Dict[str, Set[str]] = {entry.end: set(), entry.other_end: set()}
        for other in self.provenance:
            if other.kind == LoweredKind.CIRCUIT and (other.circuit, frozenset((other.end, other.other_end))) == key:
                landed[other.end].add(other.member)
        between = f"the circuit {entry.circuit} between {entry.end} and {entry.other_end}"
        owners: Dict[str, Tuple[str, str]] = {}
        for name in CircuitNaming.outputs(entry.circuit):
            owning = [
                (label, member)
                for label, members in ends.items()
                for member in members
                if find_output(self._component(entry, member), name) is not None
            ]
            if len(owning) != 1:
                owning_names = " and ".join(member for _label, member in owning)
                raise self._circuit_error(
                    entry,
                    f"{between} needs exactly one owner of the output {name}, but "
                    + (
                        "no member of either end declares it"
                        if not owning
                        else f"{owning_names} {'both' if len(owning) == 2 else 'all'} declare it"
                    ),
                    remedy=f"Each of {', '.join(CircuitNaming.outputs(entry.circuit))} is owned by one end and read "
                    "by the other (hydronic spec §3.1).",
                )
            owners[name] = owning[0]
        for name, (owner_end, owner) in owners.items():
            reader_end = entry.other_end if owner_end == entry.end else entry.end
            owner_component = self._component(entry, owner)
            readers = [
                member for member in ends[reader_end] if find_input(self._component(entry, member), name) is not None
            ]
            if not readers:
                raise self._circuit_error(
                    entry,
                    f"the circuit {entry.circuit}: {owner} at {owner_end} owns the output {name}, but no member of "
                    f"{reader_end} ({', '.join(ends[reader_end])}) reads it",
                )
            owner_output = find_output(owner_component, name)
            for reader in readers:
                reader_component = self._component(entry, reader)
                reader_input = find_input(reader_component, name)
                assert owner_output is not None and reader_input is not None  # nosec - found above
                if (owner_output.load_type, owner_output.unit) != (reader_input.loadtype, reader_input.unit):
                    raise self._circuit_error(
                        entry,
                        f"the circuit {entry.circuit}: {owner}.{name} is {owner_output.load_type.name} in "
                        f"{owner_output.unit.name}, but {reader} reads {name} as {reader_input.loadtype.name} in "
                        f"{reader_input.unit.name}",
                    )
                if reader not in landed[reader_end]:
                    raise self._circuit_error(
                        entry,
                        f"the circuit {entry.circuit}: {reader} reads {name} from {owner} at {owner_end} but carries "
                        "no placeholder of the circuit port for the items to land at",
                        remedy=f"Add the circuit port's placeholder to {reader}'s inputs.",
                    )
                owner_class = self._class_name(owner_component)
                if not reader_component.default_connections.get(owner_class):
                    raise EnergySystemAssemblyError(
                        EnergySystemErrorId.PARTNER_WITHOUT_DEFAULT_CONNECTIONS,
                        entry.owner,
                        f"the circuit {entry.circuit}: {reader} ({self._class_name(reader_component)}) reads {name} "
                        f"from {owner}, but declares no default connections from {owner_class} {entry.source_text()}.",
                        alternatives=sorted(reader_component.default_connections),
                        alternatives_label="classes it declares default connections from",
                    )

    def _check_circuit_item(self, entry: LoweredPort, member: Component, partner: Component) -> None:
        """One circuit item: the bare name names an owner of a circuit output its reader reads."""
        read = [
            name
            for name in CircuitNaming.outputs(entry.circuit)
            if find_input(member, name) is not None and find_output(partner, name) is not None
        ]
        if not read:
            raise self._circuit_error(
                entry,
                f"the circuit {entry.circuit}: {entry.member} at {entry.end} carries the placeholder of port "
                f"'{entry.port}' and so takes a bare name of {entry.partner} at {entry.other_end}, but reads none of "
                f"{', '.join(CircuitNaming.outputs(entry.circuit))} from it",
                remedy=f"Remove the placeholder from {entry.member}, or take {entry.partner} out of the members of "
                f"{entry.other_end}: every member of one end lowers a bare name into each reader at the other.",
            )

    @staticmethod
    def _circuit_error(entry: LoweredPort, problem: str, remedy: str = "") -> EnergySystemAssemblyError:
        """An ``EF-7N`` refusal of a circuit, with the source map of the end that bound it."""
        return EnergySystemAssemblyError(
            EnergySystemErrorId.CIRCUIT_ENDS_DO_NOT_FIT,
            entry.owner,
            f"{problem} {entry.source_text()}.",
            remedy=remedy or None,
        )

    # ------------------------------------------------------------------------------------------ feeds

    def _check_feed(self, entry: LoweredPort) -> None:
        """One consuming output: it exists, carries the need's carrier, and the meter feeds exactly it."""
        consumer = self._component(entry, entry.partner)
        output = find_output(consumer, entry.output)
        if output is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"carrier need '{entry.port}' names '{entry.output}', which is no output of {entry.partner} "
                f"({self._class_name(consumer)}) {entry.source_text()}.",
                alternatives=[port.field_name for port in consumer.outputs],
                alternatives_label="outputs",
                offending_value=entry.output,
            )
        carrier = getattr(getattr(output.energy_port, "carrier", None), "value", None)
        if carrier != entry.carrier:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.CARRIER_MISMATCH,
                entry.owner,
                f"carrier need '{entry.port}' is of {entry.carrier}, but {entry.partner}.{entry.output} "
                + (f"carries {carrier} by its energy port" if carrier else "declares no energy port")
                + f" {entry.source_text()}.",
                remedy="Name an output whose energy port carries the need's carrier, or configure the component for "
                "that carrier.",
            )
        if not entry.member:
            return
        meter = self._component(entry, entry.member)
        consumer_class = self._class_name(consumer)
        declared = DynamicConnectionResolver.default_feeds_of(meter, consumer_class)
        fed = [connection.source_component_field_name for connection in declared]
        if entry.output not in fed:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PARTNER_WITHOUT_DEFAULT_CONNECTIONS,
                entry.owner,
                f"carrier need '{entry.port}' (carrier {entry.carrier}) would feed {entry.partner}.{entry.output} "
                f"into the meter {entry.member} ({self._class_name(meter)}), which declares no default feed from "
                f"{consumer_class}.{entry.output} {entry.source_text()}.",
                alternatives=[f"{consumer_class}.{name}" for name in fed],
                alternatives_label="default feeds it declares from that class",
                remedy="Add the default feed (add_dynamic_default_connections) to the meter's class, or name an "
                "output it feeds.",
            )
        key = (entry.member, entry.partner)
        if key in self._feeds_checked:
            return
        self._feeds_checked.add(key)
        named: List[str] = [
            other.output
            for other in self.provenance
            if other.kind == LoweredKind.FEED and (other.member, other.partner) == key
        ]
        unnamed = [name for name in fed if name not in named]
        if unnamed:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"the meter {entry.member} ({self._class_name(meter)}) takes a bare name of {entry.partner}, which it "
                f"expands into every default feed it declares from {consumer_class}: {', '.join(fed)}; no carrier "
                f"need bound to it names {', '.join(unnamed)} {entry.source_text()}.",
                remedy=f"Name {', '.join(unnamed)} in the consumer's carrier need as well, or drop the feed from the "
                "meter's class.",
            )

    # ---------------------------------------------------------------------------------------- observe

    def _check_observe(self, entry: LoweredPort, observer: Component, participant: Component) -> None:
        """One observed output: it exists, a channel of the observer accepts it, a dispatch target is real."""
        output = find_output(participant, entry.output)
        if output is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"{entry.member} observes '{entry.output}' of {entry.partner} ({self._class_name(participant)}), "
                f"which is no output of the constructed {entry.partner} {entry.source_text()}.",
                alternatives=[port.field_name for port in participant.outputs],
                alternatives_label="outputs",
                offending_value=entry.output,
                remedy=f"{entry.member}'s constructor declares a dynamic default connection from that output; fix the "
                "declaration or the participant.",
            )
        tags = [self._tag(name) for name in entry.tags]
        channels = [channel for channel in DynamicConnectionResolver.channels_of(observer) if channel.matches(tags)]
        if not channels:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"{entry.member} ({self._class_name(observer)}) observes {entry.partner}.{entry.output} with the tags "
                f"{', '.join(entry.tags) or 'none'}, which no channel of it accepts {entry.source_text()}.",
                alternatives=[channel.describe() for channel in DynamicConnectionResolver.channels_of(observer)],
                alternatives_label="channels",
            )
        fitting = [
            channel
            for channel in channels
            if channel.accepts_load_type(output.load_type) and channel.accepts_unit(output.unit)
        ]
        if not fitting:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.CARRIER_MISMATCH,
                entry.owner,
                f"{entry.member} ({self._class_name(observer)}) observes {entry.partner}.{entry.output}, which is "
                f"{output.load_type.name} in {output.unit.name}, but its channel "
                f"{', '.join(channel.describe() for channel in channels)} takes "
                f"{', '.join(f'{channel.load_type.name} in {channel.unit.name}' for channel in channels)} "
                f"{entry.source_text()}.",
            )
        if not entry.input:
            return
        target = find_input(participant, entry.input)
        if target is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"{entry.member} dispatches to '{entry.input}' of {entry.partner} ({self._class_name(participant)}), "
                f"which is no input of the constructed {entry.partner} {entry.source_text()}.",
                alternatives=[port.field_name for port in participant.inputs],
                alternatives_label="inputs",
                offending_value=entry.input,
            )
        if all(channel.dispatch == DispatchRule.FORBIDDEN for channel in fitting):
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.ACTUATION,
                entry.owner,
                f"{entry.member} ({self._class_name(observer)}) would dispatch to {entry.partner}.{entry.input}, but "
                f"its channel {', '.join(channel.describe() for channel in fitting)} forbids a dispatch: the "
                f"controller actuates only what its channels allow (D21) {entry.source_text()}.",
            )
        dispatching = [channel for channel in fitting if channel.dispatch != DispatchRule.FORBIDDEN]
        if not any(
            PortTypeCompatibility.load_types_agree(channel.load_type, target.loadtype)
            and PortTypeCompatibility.units_agree(channel.unit, target.unit)
            for channel in dispatching
        ):
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.ACTUATION,
                entry.owner,
                f"{entry.member} ({self._class_name(observer)}) would dispatch "
                f"{', '.join(f'{channel.load_type.name} in {channel.unit.name}' for channel in dispatching)} to "
                f"{entry.partner}.{entry.input}, which takes {target.loadtype.name} in {target.unit.name}: the "
                f"controller actuates only L1 modifiers and an input of the quantity it dispatches (D21) "
                f"{entry.source_text()}.",
                remedy=f"State the output controllable via the L1's modifier need (controllable: {{via: <need>}}), or "
                f"name the input of {entry.partner} the controller's dispatch feeds.",
            )

    @staticmethod
    def _tag(name: str) -> ConnectionTag:
        """A component type or flow tag by its member name."""
        if name in lt.ComponentType.__members__:
            return lt.ComponentType[name]
        return lt.InandOutputType[name]


def check_lowered_ports(provenance: Sequence[LoweredPort], components: Mapping[str, Component]) -> None:
    """Checks every lowered port item against the constructed components (see :class:`LoweredPortCheck`).

    Args:
        provenance: The port-provenance table.
        components: The constructed components by name.

    Raises:
        EnergySystemAssemblyError: ``EF-7H`` or ``EF-7J`` at the first item that does not fit.
    """
    LoweredPortCheck(provenance, components).check()
