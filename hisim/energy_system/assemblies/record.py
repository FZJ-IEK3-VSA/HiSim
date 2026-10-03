"""The import record and the source maps: what the expansion of imports did, as data.

``assemblies_spec.md`` §2.3 item 5 and §9.2. The expansion writes two things beside the flat file
it produces, both data and neither part of the file, so that a flat file without imports stays
byte for byte what it was:

- the **import record**, like the group expansion's ``ExpansionRecord``: per import and instance at
  every depth the assembly path and the sha256 of its file, the preset, the parameters as given and
  as resolved, the internal variants selected, every member's structured address, display name and
  order path, every port's state and partner (``optional-bind: not bound, partner absent`` where it
  says so), the constructs this step does not lower, and the file's final evaluation sequence;
- the **source map**, a side table keyed by an expanded component and one of its items — the
  component itself, an input item, a sizing line, a config value — naming the import path, the
  member, and the chain of files and lines it came from.

A realized record's ``metadata`` carries both (§9.1). Every downstream error that names a component
the expansion produced prints that component's source-map entry, in the shape
``dhw-generator-HeatPump (import dhw → generator, dhw/heat_pump_water_heater.assembly.yaml:6 →
generator/dhw_heat_pump.assembly.yaml:9)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from hisim.config import ComponentID
from hisim.energy_system.address_table import AddressTable
from hisim.energy_system.errors import EnergySystemCatalogueError
from hisim.energy_system.source_lines import SourceLocation


@dataclass(frozen=True)
class SourceMapEntry:
    """Where one item of one expanded component came from.

    Attributes:
        component: The expanded component's name.
        item: The item: ``component``, ``inputs[2]``, ``sizing_sources.<fact>``, ``config.<field>``.
        import_path: The import path, outermost first, ``dhw → generator`` or ``pv[east]``; empty
            for a site component.
        member: The member's name inside its assembly; the component's own name on the site.
        chain: The files and lines, outermost first: the import's line, each inner import's line,
            and the item's own line in the innermost assembly file.
        note: What produced the item, when a port's binding did (``port ems_modifier bound to
            control-EMS``).
    """

    component: str
    item: str
    import_path: str
    member: str
    chain: Tuple[SourceLocation, ...]
    note: str = ""

    def text(self) -> str:
        """The entry as a message prints it."""
        parts = []
        if self.import_path:
            parts.append(f"import {self.import_path}")
        if self.chain:
            parts.append(" → ".join(location.text for location in self.chain))
        rendered = f"{self.component} ({', '.join(parts)})" if parts else self.component
        return f"{rendered}: {self.note}" if self.note else rendered

    def to_document(self) -> Dict[str, Any]:
        """The entry as plain data."""
        document: Dict[str, Any] = {
            "import_path": self.import_path,
            "member": self.member,
            "chain": [location.text for location in self.chain],
        }
        if self.note:
            document["note"] = self.note
        return document


class SourceMap:
    """The side table of source-map entries, keyed by component and item."""

    #: The metadata key a realized record keeps the table under.
    METADATA_KEY: ClassVar[str] = "source_map"

    def __init__(self) -> None:
        """Starts an empty table."""
        self.entries: Dict[Tuple[str, str], SourceMapEntry] = {}

    def add(self, entry: SourceMapEntry) -> None:
        """Adds an entry, replacing an earlier one for the same item."""
        self.entries[(entry.component, entry.item)] = entry

    def of(self, component: str, item: str = "component") -> Optional[SourceMapEntry]:
        """The entry of one item, or ``None``."""
        return self.entries.get((component, item))

    def items_of(self, component: str) -> Tuple[str, ...]:
        """Every item one component has an entry for."""
        return tuple(item for name, item in self.entries if name == component)

    def components(self) -> Tuple[str, ...]:
        """Every component with an entry, in the order they were produced."""
        return tuple(dict.fromkeys(name for name, _item in self.entries))

    @property
    def is_empty(self) -> bool:
        """Whether the expansion produced nothing."""
        return not self.entries

    def to_document(self) -> Dict[str, Dict[str, Any]]:
        """The table as plain data: component to item to entry."""
        document: Dict[str, Dict[str, Any]] = {}
        for (component, item), entry in self.entries.items():
            document.setdefault(component, {})[item] = entry.to_document()
        return document

    def annotate(self, error: EnergySystemCatalogueError) -> EnergySystemCatalogueError:
        """Returns the error with the source-map entry of the component it names appended.

        The component is the one the error's location names (``components.<name>…``); an error
        about no component the expansion produced is returned unchanged.

        Args:
            error: An error a stage after the expansion raised.

        Returns:
            The error itself, or a copy of the same class with the entry in its message.
        """
        location = getattr(error, "location", "")
        parts = location.split(".")
        if len(parts) < 2 or parts[0] != "components":
            return error
        entry = self.of(parts[1])
        if entry is None:
            return error
        # The catalogue errors take their parts as constructor arguments, which copy cannot replay;
        # the copy is built bare and given the original's attributes and the extended message.
        annotated = type(error).__new__(type(error))
        annotated.__dict__.update(error.__dict__)
        annotated.args = (f"{error} [source: {entry.text()}]",)
        return annotated


@dataclass(frozen=True)
class PortRecord:
    """One port's resolved state in one import instance.

    Attributes:
        port: The port's name.
        kind: Its kind.
        state: ``required``, ``optional``, ``inactive``, ``re-exported``, ``internal``,
            ``declined``, ``provided`` or ``not lowered``.
        partner: The expanded name it was bound to, or a sentence saying why it is unbound.
        verb: The verb that decided it, ``default`` when the default rule did.
        lowered_to: The items it lowered to, as ``<component>.inputs: <item>``.
    """

    port: str
    kind: str
    state: str
    partner: str = ""
    verb: str = ""
    lowered_to: Tuple[str, ...] = ()

    def to_document(self) -> Dict[str, Any]:
        """The record as plain data."""
        document: Dict[str, Any] = {"port": self.port, "kind": self.kind, "state": self.state}
        if self.partner:
            document["partner"] = self.partner
        if self.verb:
            document["verb"] = self.verb
        if self.lowered_to:
            document["lowered_to"] = list(self.lowered_to)
        return document


@dataclass
class InstanceRecord:
    """What the expansion did with one import or instance, at any depth.

    Attributes:
        path: The import path, ``pv[east]`` or ``dhw → generator``.
        assembly: The assembly's library path.
        file: The file it resolved to, relative to its library directory.
        sha256: The sha256 of that file's bytes.
        preset: The preset applied.
        parameters_given: The values the import wrote.
        parameters_resolved: Every parameter's resolved value.
        variants: Internal variant to the option selected.
        members: Expanded name to ``{member, order, display_name}``.
        ports: Every port's record.
        reserved: The reserved economics fields the import carries (D18, D22), as written.
    """

    path: str
    assembly: str
    file: str
    sha256: str
    preset: Optional[str]
    parameters_given: Mapping[str, Any]
    parameters_resolved: Mapping[str, Any]
    variants: Dict[str, str] = field(default_factory=dict)
    members: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    ports: List[PortRecord] = field(default_factory=list)
    reserved: Dict[str, Any] = field(default_factory=dict)

    def port(self, name: str) -> Optional[PortRecord]:
        """The record of one port, or ``None``."""
        return next((record for record in self.ports if record.port == name), None)

    def to_document(self) -> Dict[str, Any]:
        """The record as plain data."""
        document: Dict[str, Any] = {
            "path": self.path,
            "assembly": self.assembly,
            "file": self.file,
            "sha256": self.sha256,
            "preset": self.preset,
            "parameters_given": dict(self.parameters_given),
            "parameters_resolved": dict(self.parameters_resolved),
            "variants": dict(self.variants),
            "members": {name: dict(member) for name, member in self.members.items()},
            "ports": [record.to_document() for record in self.ports],
        }
        if self.reserved:
            document["reserved"] = dict(self.reserved)
        return document


@dataclass(frozen=True)
class CircuitEndRecord:
    """One end of a bound hydronic circuit, as the record states it.

    Attributes:
        owner: The import path (``heating``, ``dhw → cylinder``) or the site component holding it.
        port: The circuit port's name.
        members: The expanded components at this end.
        owns: The circuit outputs this end owns (``MassFlowDhw``, ``SupplyTemperatureDhw``).
    """

    owner: str
    port: str
    members: Tuple[str, ...]
    owns: Tuple[str, ...]

    def to_document(self) -> Dict[str, Any]:
        """The end as plain data."""
        return {"owner": self.owner, "port": self.port, "members": list(self.members), "owns": list(self.owns)}


@dataclass(frozen=True)
class CircuitRecord:
    """One bound hydronic circuit (``assemblies_spec.md`` §3.2, §11.1): its medium, both ends, the wiring.

    Attributes:
        circuit: The circuit's name, its medium (``dhw``).
        ends: The two ends, the one whose port decided the binding first.
        verb: The verb that bound it, ``default`` for the default rule.
        lowered_to: The items the binding wrote, as ``<component>.inputs: <item>``.
    """

    circuit: str
    ends: Tuple[CircuitEndRecord, CircuitEndRecord]
    verb: str
    lowered_to: Tuple[str, ...]

    def to_document(self) -> Dict[str, Any]:
        """The circuit as plain data."""
        return {
            "circuit": self.circuit,
            "ends": [end.to_document() for end in self.ends],
            "verb": self.verb,
            "lowered_to": list(self.lowered_to),
        }


@dataclass(frozen=True)
class CarrierConsumer:
    """One carrier need bound to a provider: whose outputs the provider's meter observes.

    Attributes:
        owner: The import path or site component holding the need.
        port: The need's name.
        outputs: The consuming outputs, ``<component>.<output>``.
        verb: The verb that bound it, ``default`` for the default rule.
        lowered_to: The feeds written into the meter (none for electricity).
    """

    owner: str
    port: str
    outputs: Tuple[str, ...]
    verb: str
    lowered_to: Tuple[str, ...]

    def to_document(self) -> Dict[str, Any]:
        """The consumer as plain data."""
        return {
            "owner": self.owner,
            "port": self.port,
            "outputs": list(self.outputs),
            "verb": self.verb,
            "lowered_to": list(self.lowered_to),
        }


@dataclass
class CarrierRecord:
    """One provider of a carrier and every need bound to it (``assemblies_spec.md`` §5.1).

    Attributes:
        carrier: The carrier, an ``lt.EnergyBalanceCarrier`` value.
        provider: The import path or site component providing it.
        port: The providing port.
        meter: The expanded meter that observes the consumers; ``None`` for electricity, which has
            no link and whose need is only the check that this one provider exists.
        consumers: Every need bound to the provider, in binding order.
    """

    carrier: str
    provider: str
    port: str
    meter: Optional[str]
    consumers: List[CarrierConsumer] = field(default_factory=list)

    def to_document(self) -> Dict[str, Any]:
        """The provider as plain data."""
        return {
            "carrier": self.carrier,
            "provider": self.provider,
            "port": self.port,
            "meter": self.meter,
            "consumers": [consumer.to_document() for consumer in self.consumers],
        }


@dataclass(frozen=True)
class NotLowered:
    """A construct the expansion met that a later step of the assemblies work lowers.

    Attributes:
        where: The import path and the construct, ``pv[east]: port surface_area (fact)``.
        step: The bead that delivers it.
    """

    where: str
    step: str

    def text(self) -> str:
        """The construct as a message lists it."""
        return f"{self.where} — not lowered yet, delivered by {self.step}"


@dataclass
class ImportRecord:
    """Everything the expansion of imports did to one file, ready to be shown or written.

    ``instances`` lists every import and instance at every depth, each after the imports nested in
    it (the expansion works innermost first); ``decisions`` lists every port binding in the order
    it was made, an inner assembly's before its importer's; ``sequence`` is the final evaluation
    sequence with each component's order path; ``circuits`` lists every bound hydronic circuit
    with both its ends, and ``carriers`` every provider of a carrier with the needs bound to it.

    An expansion that imported nothing produces an empty record, which keeps every consumer free
    of a case distinction, as :class:`~hisim.energy_system.groups.ExpansionRecord` does.
    """

    instances: List[InstanceRecord] = field(default_factory=list)
    addresses: Dict[str, ComponentID] = field(default_factory=dict)
    sequence: List[Tuple[str, Tuple[int, ...]]] = field(default_factory=list)
    site_ports: List[Tuple[str, PortRecord]] = field(default_factory=list)
    not_lowered: List[NotLowered] = field(default_factory=list)
    source_map: SourceMap = field(default_factory=SourceMap)
    decisions: List[str] = field(default_factory=list)
    circuits: List[CircuitRecord] = field(default_factory=list)
    carriers: List[CarrierRecord] = field(default_factory=list)

    def carrier(self, carrier: str) -> List[CarrierRecord]:
        """The providers of one carrier."""
        return [record for record in self.carriers if record.carrier == carrier]

    def circuit(self, circuit: str) -> List[CircuitRecord]:
        """The bound circuits of one medium."""
        return [record for record in self.circuits if record.circuit == circuit]

    @property
    def is_empty(self) -> bool:
        """Whether the expansion did nothing at all."""
        return (
            not (self.instances or self.addresses or self.site_ports or self.not_lowered) and self.source_map.is_empty
        )

    def instance(self, path: str) -> Optional[InstanceRecord]:
        """The record of one import path, ``pv[east]`` or ``dhw → generator``."""
        return next((record for record in self.instances if record.path == path), None)

    @property
    def sequence_names(self) -> Tuple[str, ...]:
        """The final evaluation sequence, by component name."""
        return tuple(name for name, _path in self.sequence)

    def to_document(self) -> Dict[str, Any]:
        """The record as the plain data a realized record's metadata carries under ``imports``."""
        return {
            "instances": [record.to_document() for record in self.instances],
            "site_ports": [{"component": name, **record.to_document()} for name, record in self.site_ports],
            AddressTable.ADDRESSES_KEY: AddressTable.to_document(self.addresses),
            "sequence": [
                {"component": name, "order": ".".join(str(step) for step in path)} for name, path in self.sequence
            ],
            "not_lowered": [item.text() for item in self.not_lowered],
            "bindings": list(self.decisions),
            "circuits": [record.to_document() for record in self.circuits],
            "carriers": [record.to_document() for record in self.carriers],
        }

    def describe(self) -> Tuple[str, ...]:
        """One readable line per instance, port decision and evaluation step."""
        lines: List[str] = []
        for record in self.instances:
            lines.append(
                f"import {record.path}: {record.assembly} (sha256 {record.sha256[:12]}), preset "
                f"{record.preset or '<none>'}, members {', '.join(record.members) or '<none>'}."
            )
            for port in record.ports:
                lines.append(
                    f"  port {port.port} ({port.kind}): {port.state}{', ' + port.partner if port.partner else ''}."
                )
        for circuit in self.circuits:
            first, second = circuit.ends
            lines.append(
                f"circuit {circuit.circuit}: {first.owner}.{first.port} ({', '.join(first.owns) or 'owns nothing'})"
                f" <-> {second.owner}.{second.port} ({', '.join(second.owns) or 'owns nothing'})."
            )
        for carrier in self.carriers:
            consumers = ", ".join(f"{consumer.owner}.{consumer.port}" for consumer in carrier.consumers)
            lines.append(
                f"carrier {carrier.carrier}: {carrier.provider}.{carrier.port}"
                + (f" (meter {carrier.meter})" if carrier.meter else " (no link)")
                + f", consumers {consumers or 'none'}."
            )
        if self.sequence:
            lines.append("sequence: " + ", ".join(f"{name} {'.'.join(map(str, path))}" for name, path in self.sequence))
        return tuple(lines)
