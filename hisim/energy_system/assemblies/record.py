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
  member, and the chain of files and lines it came from;
- the **port-provenance table** (:class:`PortProvenance`), part of the import record: one entry per
  item a port lowered to — a bare partner name, one wire, a provided output — with the import path,
  the port, the member, the partner by name and class, the verb, the files and lines, the candidates
  and the paste-ready verb lines. The expansion decides bindings from the file alone; the
  post-construction port check reads this table once the components exist and refuses, with all of
  it in the message, an item the constructed member or partner does not have.

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
from hisim.energy_system.errors import EnergySystemCatalogueError, EnergySystemErrorId, EnergySystemFormatError
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


class LoweredKind:
    """What one provenance entry stands for; the post-construction port check reads it."""

    #: A bare partner name: the member's default connections from the partner's class.
    DEFAULT = "default"
    #: One line of a port's ``wires:``: a named input of the member fed by a named output of the partner.
    WIRE = "wire"
    #: A provided port's output: a named output of the member.
    PROVIDED = "provided"

    ALL: ClassVar[Tuple[str, ...]] = (DEFAULT, WIRE, PROVIDED)


@dataclass(frozen=True)
class LoweredPort:
    """Where one item a port lowered to came from, as the post-construction port check needs it.

    The expansion decides a binding from the file alone — every entry states its class — and
    lowers it to items whose meaning only the constructed components can confirm: a bare name
    means the member's default connections from the partner's class, a wire names an input and
    an output. One entry per such item carries everything a refusal has to print once the
    components exist (``assemblies_spec.md`` §3.3).

    Attributes:
        kind: :class:`LoweredKind`: ``default``, ``wire`` or ``provided``.
        owner: How a message names the port's owner, ``import pv[east]`` or ``component Thermostat``;
            the location of a refusal.
        import_path: The owner's import path, ``pv[east]`` or ``dhw → generator`` (it carries the
            instance); a site entry's own name.
        port: The port's name.
        verb: What decided the binding: ``default``, ``bind``, ``optional-bind``, ``internal <name>``;
            empty for a provided output.
        member: The expanded name of the component the item lands in (or, for a provided output,
            the component providing it).
        member_class: That component's dotted class path.
        partner: The expanded name of the bound partner; empty for a provided output.
        partner_class: The partner's dotted class path; empty for a provided output.
        input: The member's input a wire feeds; empty otherwise.
        output: The partner's output a wire reads, or the member's provided output; empty for a
            bare name.
        chain: The files and lines the port came from, outermost first.
        candidates: Every candidate partner in scope, ``name (Class)``.
        remedy: The paste-ready verb lines the expansion offered for this port.
    """

    kind: str
    owner: str
    import_path: str
    port: str
    verb: str
    member: str
    member_class: str
    partner: str = ""
    partner_class: str = ""
    input: str = ""
    output: str = ""
    chain: Tuple[str, ...] = ()
    candidates: Tuple[str, ...] = ()
    remedy: str = ""

    def source_text(self) -> str:
        """The owner and its source map, as a message prints them."""
        return f"({self.owner}, {' → '.join(self.chain)})"

    def to_document(self) -> Dict[str, Any]:
        """The entry as plain data; every field is written, so a re-run reads it back whole."""
        return {
            "kind": self.kind,
            "owner": self.owner,
            "import_path": self.import_path,
            "port": self.port,
            "verb": self.verb,
            "member": self.member,
            "member_class": self.member_class,
            "partner": self.partner,
            "partner_class": self.partner_class,
            "input": self.input,
            "output": self.output,
            "chain": list(self.chain),
            "candidates": list(self.candidates),
            "remedy": self.remedy,
        }

    @classmethod
    def from_document(cls, document: Any, location: str) -> "LoweredPort":
        """Reads one entry a realized record's metadata carries.

        Args:
            document: The entry as :meth:`to_document` wrote it.
            location: Where it sits, for the message.

        Returns:
            The entry.

        Raises:
            EnergySystemFormatError: ``EF-07`` when it is not a mapping of exactly the written
                fields, or its kind is unknown.
        """
        expected = set(cls.__dataclass_fields__)
        if not isinstance(document, Mapping) or set(document) != expected:
            raise EnergySystemFormatError(
                EnergySystemErrorId.MALFORMED_BLOCK,
                location,
                "a port-provenance entry is a mapping of exactly the fields "
                f"{', '.join(sorted(expected))}; found {document!r}.",
            )
        if document["kind"] not in LoweredKind.ALL:
            raise EnergySystemFormatError(
                EnergySystemErrorId.MALFORMED_BLOCK,
                f"{location}.kind",
                f"'{document['kind']}' is no kind of lowered port item.",
                alternatives=LoweredKind.ALL,
                alternatives_label="kinds",
                offending_value=str(document["kind"]),
            )
        values = {name: document[name] for name in expected}
        values["chain"] = tuple(document["chain"])
        values["candidates"] = tuple(document["candidates"])
        return cls(**values)


class PortProvenance:
    """The port-provenance table: every item the expansion lowered a port to, in lowering order.

    The import record holds it, the realized record's metadata carries it under
    ``imports.port_provenance``, and the post-construction port check
    (:mod:`hisim.energy_system.assemblies.port_check`) reads it — from the import record on a run,
    from the metadata on a re-run, which expands nothing.
    """

    #: The key of the table in the ``imports`` block of a realized record's metadata.
    METADATA_KEY: ClassVar[str] = "port_provenance"

    @classmethod
    def from_metadata(cls, metadata: Optional[Mapping[str, Any]]) -> List[LoweredPort]:
        """Reads the table a realized record carries; empty for a record of a file without imports.

        Args:
            metadata: The record's ``metadata`` block, or ``None``.

        Returns:
            The entries, in the order they were written.

        Raises:
            EnergySystemFormatError: ``EF-07`` when the record carries an import record without the
                table, or the table is malformed.
        """
        imports = (metadata or {}).get(AddressTable.IMPORTS_KEY)
        if imports is None:
            return []
        location = f"metadata.{AddressTable.IMPORTS_KEY}.{cls.METADATA_KEY}"
        if not isinstance(imports, Mapping) or not isinstance(imports.get(cls.METADATA_KEY), list):
            raise EnergySystemFormatError(
                EnergySystemErrorId.MALFORMED_BLOCK,
                location,
                "the record carries an import record without its port-provenance list, so the ports its "
                "expansion lowered cannot be checked against the constructed components.",
                remedy="Re-run the authored file that imports the assemblies, which writes a complete record.",
            )
        return [
            LoweredPort.from_document(entry, f"{location}[{index}]")
            for index, entry in enumerate(imports[cls.METADATA_KEY])
        ]


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
        return f"{self.where} — not lowered in step 1a, delivered by {self.step}"


@dataclass
class ImportRecord:
    """Everything the expansion of imports did to one file, ready to be shown or written.

    ``instances`` lists every import and instance at every depth, each after the imports nested in
    it (the expansion works innermost first); ``decisions`` lists every port binding in the order
    it was made, an inner assembly's before its importer's; ``sequence`` is the final evaluation
    sequence with each component's order path.

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
    port_provenance: List[LoweredPort] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Whether the expansion did nothing at all."""
        return (
            not (self.instances or self.addresses or self.site_ports or self.not_lowered or self.port_provenance)
            and self.source_map.is_empty
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
            PortProvenance.METADATA_KEY: [entry.to_document() for entry in self.port_provenance],
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
        if self.sequence:
            lines.append("sequence: " + ", ".join(f"{name} {'.'.join(map(str, path))}" for name, path in self.sequence))
        return tuple(lines)
