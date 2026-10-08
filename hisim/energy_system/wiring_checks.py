"""The record of one planned connection, the checks a whole plan must pass, and its application.

Everything a connection can be wrong about is decided here, over the complete plan rather than
item by item, and before a single wire is made. That ordering is the point of the module: a file
that is wrong leaves the simulator untouched instead of half connected, and a message can name
both ends of a connection because both components already exist.

Five rules, in the order in which their failures are most useful to an author. An explicit wire
may not reach into a port that feed resolution created, because that port is not in the file the
author is reading and its name is derived rather than declared. Every wire must name ports that
exist, which is the check that catches a renamed output and a port a configuration switched off.
The two ends of a wire must agree on load type and unit, unless one of them is declared as the
wildcard that means "whatever the other carries". No input may be fed twice, which the format
has no way of merging. And no mandatory input may be left open, unless the port itself says its
source may legitimately be absent — a half-wired system that starts and produces numbers is the
failure mode the whole format exists to remove.

Applying the plan is the last thing that happens and is deliberately trivial: one call per wire,
in plan order, so that the wire log a run writes down is the order the connections were made in.

The record of a single planned connection lives here too, next to the code that reads it: the
planner in :mod:`hisim.energy_system.wiring` produces these records, hands the finished list back
here to be checked, and never needs to know how a check is written.

Three checks see more than the wires. Two cover what an assembly's carrier need states and no
wire shows (``assemblies_spec.md`` §3.3): a consuming output's energy port carries the need's
carrier, and a provider's meter declares a feed for every consuming output named, which is the
feed the output lands as (:func:`check_consuming_outputs`, :func:`meter_feed_of`; D30). The third
refuses a flow counted twice: two aggregators of which one reads any output of the other and both
observe one flow, a source's output, by its resolved name (:func:`check_double_count`, §4.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim import log
from hisim.component import Component, ComponentInput, ComponentOutput
from hisim.config.channels import PortTypeCompatibility
from hisim.energy_system.channels import FeedRequest
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemWiringError
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.energy_system.model import ConsumingOutput


@dataclass(frozen=True)
class PlannedWire:
    """One field-level connection the wiring stage intends to make, before anything is mutated.

    Bare items, explicit wires and aggregator feeds all reduce to a list of these, which is what
    lets the final checks run over expansions and written items in exactly the same way. The wire
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


class WiringChecker:
    """Applies the connection half of the error catalogue to a complete wiring plan.

    Built from the system's components and the plan, so that every check can look a port up on
    the object that owns it. The checks are separate methods because each has its own failure
    mode and its own message, and :meth:`check_all` fixes the order they run in. Whether the two
    ends of a wire agree on load type and unit is not decided here but asked of
    :class:`~hisim.config.channels.PortTypeCompatibility`, which is the same predicate the channel
    matcher uses, so a wire and a feed can never disagree about what the wildcard permits.
    """

    def __init__(
        self,
        components_by_name: Mapping[str, Component],
        wires: Sequence[PlannedWire],
        written_wire_count: int,
        created_ports: Mapping[str, Sequence[str]],
        system_name: str,
        consuming: Sequence["ConsumingOutput"] = (),
    ) -> None:
        """Prepares the checker for one planned wiring.

        Args:
            components_by_name: Every component of the system, keyed by its name in the file.
            wires: The planned wires, written and default-expanded ones first.
            written_wire_count: How many of them come from a written or default-expanded item;
                the rest were derived by feed resolution and are exempt from the first check.
            created_ports: The ports feed resolution created, keyed by aggregator name.
            system_name: Name of the energy system, used in the message for an open input.
            consuming: The consuming outputs of the carrier needs an expansion of imports lowered.
        """
        self.components_by_name = dict(components_by_name)
        self.wires = list(wires)
        self.written_wire_count = written_wire_count
        self.created_ports = {name: list(ports) for name, ports in created_ports.items()}
        self.system_name = system_name
        self.consuming = tuple(consuming)

    def check_all(self) -> None:
        """Runs every connection check, stopping at the first violation.

        Raises:
            EnergySystemWiringError: On the first rule broken, naming both ends of the
                connection and, where the set is closed, the ports that were available.
        """
        self._check_written_wires_avoid_derived_ports()
        self._check_ports_exist()
        self._check_port_types_agree()
        self._check_no_input_is_fed_twice()
        self._check_mandatory_inputs_are_connected()
        check_consuming_outputs(self.components_by_name, self.consuming)

    def apply(self) -> None:
        """Connects every planned wire to its target component.

        The only mutating step of the whole wiring stage, run after all checks, so a component is
        either fully wired or untouched. The wires are applied in plan order, which is file order
        over the entries, making the wire log a run writes deterministic.
        """
        for wire in self.wires:
            self.components_by_name[wire.target_name].connect_input(
                input_fieldname=wire.target_input,
                src_object_name=wire.source_runtime_name,
                src_field_name=wire.source_output,
            )

    def _check_written_wires_avoid_derived_ports(self) -> None:
        """Rejects a written wire that reaches into a port feed resolution created.

        Such a port does not exist when the file is written, so naming it is always a mistake,
        and the dispatch block of a feed already expresses the back-channel an author would
        otherwise be tempted to wire by hand. Without this check the wire would simply work,
        which would make the derived port names part of the hand-written surface of every file.

        Raises:
            EnergySystemWiringError: ``EF-21``/``EF-22`` if a written wire names a derived port.
        """
        for wire in self.wires[: self.written_wire_count]:
            for name, port, error_id, side in (
                (wire.source_name, wire.source_output, EnergySystemErrorId.UNKNOWN_OUTPUT_PORT, "output"),
                (wire.target_name, wire.target_input, EnergySystemErrorId.UNKNOWN_INPUT_PORT, "input"),
            ):
                if port in self.created_ports.get(name, []):
                    raise EnergySystemWiringError(
                        error_id,
                        f"components.{wire.target_name}.inputs",
                        f"the connection {wire.describe()} names the {side} "
                        f"'{name}.{port}', which the file does not contain: it is created by "
                        "resolving that aggregator's feeds.",
                        remedy=(
                            "A written wire may only name a port a component declares itself; "
                            "use the feed's 'dispatch' block for the back-channel."
                        ),
                    )

    def _check_ports_exist(self) -> None:
        """Verifies that every planned wire names an existing output and an existing input.

        Raises:
            EnergySystemWiringError: ``EF-21`` for a missing output, ``EF-22`` for a missing
                input, each listing the ports the component does declare.
        """
        for wire in self.wires:
            source = self.components_by_name[wire.source_name]
            target = self.components_by_name[wire.target_name]
            if find_output(source, wire.source_output) is None:
                raise EnergySystemWiringError(
                    EnergySystemErrorId.UNKNOWN_OUTPUT_PORT,
                    f"components.{wire.target_name}.inputs",
                    f"the connection {wire.describe()} names the output '{wire.source_output}', "
                    f"which '{wire.source_name}' ({source.get_full_classname()}) does not "
                    "declare.",
                    alternatives=[port.field_name for port in source.outputs],
                    alternatives_label="outputs",
                    offending_value=wire.source_output,
                )
            if find_input(target, wire.target_input) is None:
                raise EnergySystemWiringError(
                    EnergySystemErrorId.UNKNOWN_INPUT_PORT,
                    f"components.{wire.target_name}.inputs",
                    f"the connection {wire.describe()} names the input '{wire.target_input}', "
                    f"which '{wire.target_name}' ({target.get_full_classname()}) does not "
                    "declare.",
                    alternatives=[port.field_name for port in target.inputs],
                    alternatives_label="inputs",
                    offending_value=wire.target_input,
                )

    def _check_port_types_agree(self) -> None:
        """Verifies load-type and unit agreement on both ends of every wire.

        A wildcard port is compatible with any concrete counterpart; two *differing concrete*
        values are the mismatch this rule exists for, and it is a hard error rather than a log
        line because the simulator would otherwise happily add watts to degrees.

        Raises:
            EnergySystemWiringError: ``EF-30`` on the first wire whose ends disagree.
        """
        for wire in self.wires:
            output = find_output(self.components_by_name[wire.source_name], wire.source_output)
            target_input = find_input(self.components_by_name[wire.target_name], wire.target_input)
            assert output is not None and target_input is not None  # nosec - checked before
            if not PortTypeCompatibility.load_types_agree(output.load_type, target_input.loadtype):
                raise EnergySystemWiringError(
                    EnergySystemErrorId.PORT_TYPE_MISMATCH,
                    f"components.{wire.target_name}.inputs",
                    f"load type mismatch on the connection {wire.describe()}: the output "
                    f"carries '{output.load_type.name}' but the input expects "
                    f"'{target_input.loadtype.name}'.",
                    remedy="Align the two port declarations, or wire a different pair of ports.",
                )
            if not PortTypeCompatibility.units_agree(output.unit, target_input.unit):
                raise EnergySystemWiringError(
                    EnergySystemErrorId.PORT_TYPE_MISMATCH,
                    f"components.{wire.target_name}.inputs",
                    f"unit mismatch on the connection {wire.describe()}: the output is in "
                    f"'{output.unit.value}' but the input expects '{target_input.unit.value}'.",
                    remedy="Align the two port declarations, or wire a different pair of ports.",
                )

    def _check_no_input_is_fed_twice(self) -> None:
        """Rejects two sources feeding the same target input.

        The structural validator already catches duplicates visible in the file itself; this adds
        the ones that only appear once bare items have been expanded, which is the case the older
        executor silently resolved by letting the last wire win.

        Raises:
            EnergySystemWiringError: ``EF-26`` if a target input is fed by more than one wire.
        """
        seen: Dict[Tuple[str, str], PlannedWire] = {}
        for wire in self.wires:
            key = (wire.target_name, wire.target_input)
            previous = seen.get(key)
            if previous is not None:
                raise EnergySystemWiringError(
                    EnergySystemErrorId.DUPLICATE_WIRE,
                    f"components.{wire.target_name}.inputs",
                    f"the input '{wire.target_name}.{wire.target_input}' is fed twice: by "
                    f"{previous.describe()} and by {wire.describe()}.",
                    remedy="Every input takes exactly one source; remove one of the two items.",
                )
            seen[key] = wire

    def _check_mandatory_inputs_are_connected(self) -> None:
        """Verifies that every mandatory input of every component receives a wire.

        A port may declare that its source is legitimately optional under some configurations —
        a heat pump's hot-water input when hot-water preparation is switched off — and such a
        port is reported and skipped. Any other open mandatory input aborts the build.

        Raises:
            EnergySystemWiringError: ``EF-31`` for the first mandatory input left open.
        """
        connected = {(wire.target_name, wire.target_input) for wire in self.wires}
        for name, component in self.components_by_name.items():
            for component_input in component.inputs:
                if not component_input.is_mandatory:
                    continue
                if (name, component_input.field_name) in connected:
                    continue
                if component_input.allow_unconnected_mandatory:
                    log.information(
                        f"The mandatory input '{name}.{component_input.field_name}' is "
                        "unconnected, which its component declares as allowed."
                    )
                    continue
                raise EnergySystemWiringError(
                    EnergySystemErrorId.UNCONNECTED_MANDATORY_INPUT,
                    f"components.{name}.inputs",
                    f"the mandatory input '{name}.{component_input.field_name}' "
                    f"({component.get_full_classname()}) is connected by no item of the energy "
                    f"system '{self.system_name}'.",
                    remedy=(
                        "Add an input item feeding it, or declare the port as allowed to stay "
                        "open if its source may legitimately be absent."
                    ),
                )


def find_output(component: Component, field_name: str) -> Optional[ComponentOutput]:
    """Looks one component output up by its port name.

    Args:
        component: The component to search.
        field_name: The port name as written in the file.

    Returns:
        The matching output, or ``None``.
    """
    for output in component.outputs:
        if output.field_name == field_name:
            return output
    return None


def find_input(component: Component, field_name: str) -> Optional[ComponentInput]:
    """Looks one component input up by its port name.

    Args:
        component: The component to search.
        field_name: The port name as written in the file.

    Returns:
        The matching input, or ``None``.
    """
    for component_input in component.inputs:
        if component_input.field_name == field_name:
            return component_input
    return None


def unconsumed_sources(
    components: Sequence[str], wires: Sequence[PlannedWire]
) -> Tuple[str, ...]:
    """Names the components no other component takes anything from.

    A component nobody reads is legal and occasionally intended — a meter measuring a system it
    does not feed back into, a component added for its result columns alone — so this produces a
    warning line rather than a rejection. It is worth saying out loud because the far more common
    cause is a forgotten input item, which otherwise shows up as a suspiciously flat result curve.

    Args:
        components: Every component name of the system, in file order.
        wires: The planned wires.

    Returns:
        One warning line per unread component, in file order.
    """
    consumed = {wire.source_name for wire in wires}
    unread: List[str] = [name for name in components if name not in consumed]
    return tuple(
        f"'{name}' feeds no other component of this energy system; if that is not intended, "
        "an input item naming it is missing somewhere."
        for name in unread
    )


def check_consuming_outputs(
    components_by_name: Mapping[str, Component], consuming: Sequence[ConsumingOutput]
) -> Dict[Tuple[str, str, str], FeedRequest]:
    """Checks every consuming output against its component and its meter, the two facts no wire shows.

    Each consuming output must be an output of its consumer whose energy port carries the need's
    carrier; one with a meter must also be an output the meter's class declares a feed for, and that
    feed is what the output lands as. Example: ``boiler-Boiler.FuelUse`` consumed as natural gas with
    the meter ``gas-Meter`` returns ``{("gas-Meter", "boiler-Boiler", "FuelUse"): <its declared feed>}``.
    The feeds are returned so that the caller landing them reads each declaration once.

    Args:
        components_by_name: Every constructed component, keyed by its name in the file.
        consuming: The consuming outputs to check, in the order the carrier needs named them.

    Returns:
        The feed each consuming output with a meter lands as, keyed by ``(meter, consumer, output)``,
        in the order of ``consuming``; an output without a meter (electricity) has none.

    Raises:
        EnergySystemWiringError: ``EF-21`` for an output the consumer does not have, ``EF-7M`` for one
            whose energy port carries another carrier, ``EF-7N`` for a named output its meter's class
            declares no feed or more than one feed for (:func:`meter_feed_of`).
    """
    for item in consuming:
        consumer = components_by_name[item.consumer]
        output = find_output(consumer, item.output)
        if output is None:
            raise EnergySystemWiringError(
                EnergySystemErrorId.UNKNOWN_OUTPUT_PORT,
                f"components.{item.consumer}",
                f"the carrier need names the consuming output '{item.consumer}.{item.output}', which "
                f"{consumer.get_classname()} does not have.",
                alternatives=[port.field_name for port in consumer.outputs],
                alternatives_label="outputs",
            )
        carrier = output.energy_port.carrier.value if output.energy_port is not None else None
        if carrier != item.carrier:
            raise EnergySystemWiringError(
                EnergySystemErrorId.CARRIER_MISMATCH,
                f"components.{item.consumer}",
                f"the output '{item.consumer}.{item.output}' is consumed as {item.carrier}, but "
                + (f"its energy port carries {carrier}." if carrier else "it declares no energy port."),
            )
    landed: Dict[Tuple[str, str, str], FeedRequest] = {}
    for item in consuming:
        if item.meter is not None:
            landed[(item.meter, item.consumer, item.output)] = meter_feed_of(components_by_name, item)
    return landed


def meter_feed_of(components_by_name: Mapping[str, Component], item: ConsumingOutput) -> FeedRequest:
    """The feed a consuming output lands at its meter as: the one the meter's class declares for it (D30).

    The declaration is the meter's dynamic default connection for the consumer's class and the
    output, which only the constructed meter states; it is read through the lookup feed resolution
    uses for a bare item, so the landed feed is the one an author would write by hand. Example: the
    gas meter declares one feed for ``GenericBoiler.EnergyDemandSh``, so the boiler's output lands as
    that feed. A class that declares the output twice is refused rather than one declaration dropped.

    Raises:
        EnergySystemWiringError: ``EF-7N`` for an output the meter's class declares no feed for, or
            more than one.
    """
    assert item.meter is not None
    meter, consumer = components_by_name[item.meter], components_by_name[item.consumer]
    declared = DynamicConnectionResolver.default_feeds_of(meter, consumer.get_classname())
    match = [declaration for declaration in declared if declaration.source_component_field_name == item.output]
    if len(match) != 1:
        fed = ", ".join(str(declaration.source_component_field_name) for declaration in declared) or "nothing"
        problem = (
            f"declares {len(match)} feeds for it, so the one it lands as is not determined"
            if match
            else f"declares no feed for it; of {consumer.get_classname()} it declares feeds for {fed}"
        )
        raise EnergySystemWiringError(
            EnergySystemErrorId.METER_FEEDS,
            f"components.{item.meter}.inputs",
            f"the carrier need names the consuming output '{item.consumer}.{item.output}', but the meter "
            f"'{item.meter}' ({meter.get_classname()}) {problem}.",
            remedy=(
                "Name only outputs the provider's meter declares exactly one feed for, or declare it once in the "
                "meter's class."
            ),
        )
    return DynamicConnectionResolver.feed_from_declaration(match[0], item.meter, item.consumer)


def check_double_count(
    feeds_by_target: Mapping[str, Sequence[FeedRequest]], output_of: Callable[[FeedRequest], str]
) -> None:
    """Refuses two aggregators of which one reads an output of the other while both observe one flow (§4.3).

    An energy manager sums what it observes into a balance; a meter reading that balance and one
    of the flows behind it counts the flow twice. The rule as checked is wider than a balance: any
    output of the other aggregator read, and any flow (a source's output) both observe. Every
    planned feed counts — written, expanded from a bare name or selected — each by the output it
    resolves to, so a written feed that leaves its output to the declaration is no way around it.

    Args:
        feeds_by_target: The planned feeds, by the aggregator they address.
        output_of: The output a feed resolves to (its own, else its aggregator's one declaration).

    Raises:
        EnergySystemWiringError: ``EF-7T`` at the first aggregator counting a flow twice.
    """
    observed = {
        name: {(feed.source, output_of(feed)) for feed in feeds} for name, feeds in feeds_by_target.items()
    }
    for name, feeds in feeds_by_target.items():
        for other in sorted({feed.source for feed in feeds} & set(observed) - {name}):
            both = sorted(f"{source}.{output}" for source, output in observed[name] & observed[other])
            if both:
                raise EnergySystemWiringError(
                    EnergySystemErrorId.DOUBLE_COUNT,
                    f"components.{name}.inputs",
                    f"'{name}' reads an output of '{other}' and both observe {', '.join(both)}: the flow would be "
                    "counted twice.",
                    remedy=f"Let '{name}' read only the output of '{other}', or only the flows.",
                )
