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
  the port, the member, the partner by name and class, the verb and the files and lines. The
  expansion writes the same items a hand-written file writes; the wiring stage checks every
  connection on the constructed components, and its refusal of a lowered item is found in this
  table and restated with the port it came from (:meth:`ImportRecord.annotate`).

A realized record's ``metadata`` carries both (§9.1). Every downstream error that names a component
the expansion produced prints that component's source-map entry, in the shape
``dhw-generator-HeatPump (import dhw → generator, dhw/heat_pump_water_heater.assembly.yaml:6 →
generator/dhw_heat_pump.assembly.yaml:9)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.config import ComponentID
from hisim.energy_system.address_table import AddressTable
from hisim.energy_system.errors import (
    EnergySystemAssemblyError,
    EnergySystemCatalogueError,
    EnergySystemErrorId,
    EnergySystemFormatError,
    WrittenItem,
)
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
    """What one provenance entry stands for, as the record states it."""

    #: A bare partner name: the member's default connections from the partner's class.
    DEFAULT = "default"
    #: One line of a port's ``wires:``: a named input of the member fed by a named output of the partner.
    WIRE = "wire"
    #: A provided port's output: a named output of the member.
    PROVIDED = "provided"
    #: One item of a bound hydronic circuit: a bare name of a member at the other end, written into a
    #: member at this end that carries the circuit's placeholder.
    CIRCUIT = "circuit"
    #: One consuming output of a bound carrier need: the consumer's output, its carrier and, for a
    #: fuel, the provider's meter, which takes a bare name of the consumer (a ``default`` row) and
    #: observes the output through the default feeds it declares from the consumer's class.
    FEED = "feed"
    #: One observer and its selection (§4.1): the wiring applies the selection to the observer's
    #: dynamic default connections over the present components, and every feed it selects carries
    #: this row's item, so a refusal of one names the observer port. ``verb`` holds the selection.
    OBSERVE = "observe"

    ALL: ClassVar[Tuple[str, ...]] = (DEFAULT, WIRE, PROVIDED, CIRCUIT, FEED, OBSERVE)

    #: The fields that hold tuples, which a document holds as lists.
    TUPLE_FIELDS: ClassVar[Tuple[str, ...]] = ("chain",)


@dataclass(frozen=True)
class LoweredPort:
    """Where one item a port lowered to came from: one row of the port-provenance table.

    The expansion decides a binding from the file alone — every entry states its class — and
    lowers it to the items a hand-written file would write: a bare partner name (the member's
    default connections from the partner's class), explicit wires, for a provided port the output it
    names, for a circuit a bare name of every member of the other end in every member carrying the
    circuit's placeholder, and for a fuel a bare name of the consumer in the provider's meter. The
    wiring stage checks those items on the constructed components like any other; this row is what
    its refusal of one is restated with (``assemblies_spec.md`` §3.3). A feed row also states a
    consuming output and its carrier, which the wiring checks on the constructed consumer and meter;
    an observe row states an observer and its selection, which the wiring applies to the observer's
    dynamic default connections.

    Attributes:
        kind: :class:`LoweredKind`: ``default``, ``wire``, ``provided``, ``circuit``, ``feed`` or
            ``observe``.
        owner: How a message names the port's owner, ``import pv[east]`` or ``component Thermostat``;
            the location of a refusal.
        import_path: The owner's import path, ``pv[east]`` or ``dhw → generator`` (it carries the
            instance); a site entry's own name.
        port: The port's name.
        verb: What decided the binding: ``default``, ``bind``, ``optional-bind``, ``internal <name>``,
            or an observe row's selection; empty for a provided output.
        member: The expanded name of the component the item lands in (or, for a provided output,
            the component providing it; for a feed, the provider's meter, empty for electricity; for
            an observe row, the observer).
        member_class: That component's dotted class path; empty when ``member`` is.
        partner: The expanded name of the bound partner (for a feed, the consumer); empty for a
            provided output and an observe row.
        partner_class: The partner's dotted class path; empty for a provided output.
        input: The member's input a wire feeds; empty otherwise.
        output: The partner's output a wire reads, the member's provided output or a feed's
            consuming output; empty for a bare name and an observe row.
        chain: The files and lines the port came from, outermost first.
        carrier: A feed's carrier, an ``lt.EnergyBalanceCarrier`` value, and the carrier of a meter's
            bare name of a consumer (a ``default`` row); empty otherwise.
        circuit: A circuit item's circuit, its medium (``dhw``); empty otherwise.
        end: The label of the circuit end the item lands at (``cylinder.circuit``); empty otherwise.
        other_end: The label of the other end (``boiler.dhw``); empty otherwise.
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
    carrier: str = ""
    circuit: str = ""
    end: str = ""
    other_end: str = ""

    @property
    def item(self) -> WrittenItem:
        """The item of the expanded file this row stands for, as a wiring refusal names it.

        A carrier is part of a consuming output's item only; a meter's bare name of a consumer is the
        bare item a hand-written file writes.
        """
        return WrittenItem(
            member=self.member,
            partner=self.partner,
            input=self.input,
            output=self.output,
            carrier=self.carrier if self.output else "",
        )

    def source_text(self) -> str:
        """The owner and its source map, as a message prints them."""
        return f"({self.owner}, {' → '.join(self.chain)})"

    def text(self) -> str:
        """The port and what it lowered to, with its source map, as a refusal opens."""
        member = f"{self.member} ({short_class_name(self.member_class)})"
        if self.kind == LoweredKind.OBSERVE:
            return f"observer port '{self.port}' selects {self.verb} for {member} {self.source_text()}"
        if self.kind == LoweredKind.PROVIDED:
            return f"port '{self.port}' provides the output '{self.output}' of {member} {self.source_text()}"
        partner = f"{self.partner} ({short_class_name(self.partner_class)})"
        if self.kind == LoweredKind.FEED:
            observed = f", which the meter {member} observes" if self.member else ""
            return (
                f"carrier need '{self.port}' (carrier {self.carrier}) names the output '{self.output}' of {partner}"
                f"{observed} {self.source_text()}"
            )
        if self.kind == LoweredKind.CIRCUIT:
            return (
                f"circuit port '{self.port}' (circuit {self.circuit}, {self.end} bound to {self.other_end} by "
                f"{self.verb}) lowers to the bare name '{self.partner}' "
                f"({short_class_name(self.partner_class)}) in {member} {self.source_text()}"
            )
        if self.carrier:
            return (
                f"carrier need '{self.port}' (carrier {self.carrier}, bound by {self.verb}) lowers to the bare name "
                f"'{self.partner}' ({short_class_name(self.partner_class)}) in the meter {member} {self.source_text()}"
            )
        lowered = f"the wire '{self.input}' from '{self.output}'" if self.kind == LoweredKind.WIRE else (
            f"the bare name '{self.partner}'"
        )
        return (
            f"port '{self.port}' is bound to {partner} by {self.verb} and lowers to {lowered} in {member} "
            f"{self.source_text()}"
        )

    def to_document(self) -> Dict[str, Any]:
        """The entry as plain data; every field is written, so a re-run reads it back whole."""
        return {
            name: list(getattr(self, name)) if name in LoweredKind.TUPLE_FIELDS else getattr(self, name)
            for name in self.__dataclass_fields__
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
        values: Dict[str, Any] = {
            name: tuple(document[name]) if name in LoweredKind.TUPLE_FIELDS else document[name] for name in expected
        }
        return cls(**values)


def short_class_name(class_path: str) -> str:
    """The class name of a dotted path, which default connections and partners are keyed by."""
    return class_path.rsplit(".", 1)[-1]


class PortProvenance:
    """The port-provenance table: every item the expansion lowered a port to, in lowering order.

    The import record holds it and the realized record's metadata carries it under
    ``imports.port_provenance``; a build reads it — from the import record on a run, from the
    metadata on a re-run, which expands nothing — to restate a wiring refusal of a lowered item
    with the port it came from (:meth:`ImportRecord.annotate`).
    """

    #: The key of the table in the ``imports`` block of a realized record's metadata.
    METADATA_KEY: ClassVar[str] = "port_provenance"

    #: The wiring refusals that, for a lowered item, mean a port's contract is not met: the
    #: assemblies' identifier of each (``assemblies_spec.md`` §3.3). Any other refusal keeps its own.
    PORT_REFUSALS: ClassVar[Dict[EnergySystemErrorId, EnergySystemErrorId]] = {
        EnergySystemErrorId.NO_DECLARED_DEFAULTS: EnergySystemErrorId.PARTNER_WITHOUT_DEFAULT_CONNECTIONS,
        EnergySystemErrorId.UNKNOWN_OUTPUT_PORT: EnergySystemErrorId.PORT_CONTRACT,
        EnergySystemErrorId.UNKNOWN_INPUT_PORT: EnergySystemErrorId.PORT_CONTRACT,
        EnergySystemErrorId.PORT_NAME_COLLISION: EnergySystemErrorId.DERIVED_PORT_COLLISION,
    }

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
                "the record carries an import record without its port-provenance list, so a refusal of an "
                "item its expansion lowered could not name the port it came from.",
                remedy="Re-run the authored file that imports the assemblies, which writes a complete record.",
            )
        return [
            LoweredPort.from_document(entry, f"{location}[{index}]")
            for index, entry in enumerate(imports[cls.METADATA_KEY])
        ]

    @classmethod
    def restate(cls, error: EnergySystemCatalogueError, table: Sequence[LoweredPort]) -> EnergySystemCatalogueError:
        """Returns a wiring refusal of a lowered item restated with the port it came from.

        The refused item is found in the table by equality; an error about no item, or about an
        item no port lowered to, is returned unchanged. The restatement opens with the owner, the
        port, the partner, the member and the files and lines, and keeps the wiring's own problem
        text, alternatives and remedy; a missing default connection gets the one remedy that makes
        sense once the components exist.

        Args:
            error: An error the build raised.
            table: The port-provenance table.

        Returns:
            The error itself, or its restatement: ``EF-7H``/``EF-7J`` for the wiring's ``EF-23``,
            ``EF-21`` and ``EF-22``, and the refusal's own identifier otherwise — the wiring's
            (``EF-26``, ``EF-30``, …), or the ``EF-7x`` one the wiring's checks of consuming outputs
            raise. A restatement with an ``EF-7x`` identifier is an assembly error.
        """
        item = getattr(error, "item", None)
        row = next((entry for entry in table if entry.item == item), None) if item is not None else None
        if row is None:
            return error
        error_id = cls.PORT_REFUSALS.get(error.error_id, error.error_id)
        remedy = error.remedy
        if error.error_id == EnergySystemErrorId.NO_DECLARED_DEFAULTS:
            partner, member = short_class_name(row.partner_class), short_class_name(row.member_class)
            remedy = (
                f"Add the default connection from {partner} to {member}, or bind the port to a partner of a class "
                "it declares default connections from."
            )
        assembly_band = error_id.value.startswith("EF-7")
        error_class = EnergySystemAssemblyError if assembly_band else type(error)
        return error_class(
            error_id,
            row.owner,
            f"{row.text()}; the wiring refuses it: {error.problem}",
            alternatives=error.alternatives,
            alternatives_label=error.alternatives_label,
            offending_value=error.offending_value,
            remedy=remedy,
        )


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
        members: The expanded components at this end. Which of them owns which of the circuit's
            outputs the constructed components say; the wiring of the bare names checks it.
    """

    owner: str
    port: str
    members: Tuple[str, ...]

    def to_document(self) -> Dict[str, Any]:
        """The end as plain data."""
        return {"owner": self.owner, "port": self.port, "members": list(self.members)}


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
        lowered_to: The bare names of the consumers written into the meter (none for electricity).
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
class ScopedSizingSource:
    """A ``sizing_sources`` line the expansion wrote for a bare read (``assemblies_spec.md`` §6).

    A reader that names no provider is bound by the sizing engine's bare-fact rule over the whole
    expanded system. An assembly member's contribution its assembly does not export is internal to
    it, and a read inside an assembly binds to its own member first; wherever either would let the
    bare rule bind a different provider than the reader's scope gives — or make it ambiguous — the
    expansion writes the explicit line to the provider the scope gives and records it here.

    Attributes:
        reader: The expanded component that reads the fact.
        fact: The fact.
        providers: The provider written, or the providers of a many read, in written order.
        internal: The providers of the fact the reader does not see, being internal to their
            assemblies.
        reason: Why the line was written, in a sentence.
    """

    reader: str
    fact: str
    providers: Tuple[str, ...]
    internal: Tuple[str, ...]
    reason: str

    def text(self) -> str:
        """The line as a message and the record's description print it."""
        listed = ", ".join(f"{provider}.{self.fact}" for provider in self.providers)
        return f"{self.reader}.sizing_sources.{self.fact} -> {listed} ({self.reason})"

    def to_document(self) -> Dict[str, Any]:
        """The line as plain data."""
        return {
            "reader": self.reader,
            "fact": self.fact,
            "providers": list(self.providers),
            "internal": list(self.internal),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class FeedRecord:
    """One aggregator feed an observer's selection lowered to (``assemblies_spec.md`` §4.2).

    Attributes:
        source: The observed component.
        output: Its observed output.
        component_type: The feed's component type, or ``None``.
        tags: The feed's flow tags.
        weight: The feed's weight: the declared one, or the one a controller derived (§4.4).
        dispatch: ``None`` for a measured feed, ``{}`` for a ranked one without a target, or the
            target input a controller actuates.
        input_port: The aggregator input the feed grows (the derived port name, D of hisim-lt0b.11).
        dispatch_port: The dispatch output it grows, or ``None``.
        selected_by: The selector that matched it, or ``declared``.
        control: How a controller treats it: ``measured``, ``rank-only``, ``target_input <input>``
            or ``via <need>``.
    """

    source: str
    output: str
    component_type: Optional[str]
    tags: Tuple[str, ...]
    weight: int
    dispatch: Optional[str]
    input_port: str
    dispatch_port: Optional[str]
    selected_by: str
    control: str

    def text(self) -> str:
        """The feed as the file would write it, with the ports it grows."""
        parts = [f"from: {self.source}.{self.output}"]
        if self.component_type is not None:
            parts.append(f"component_type: {self.component_type}")
        parts.append(f"tags: [{', '.join(self.tags)}]")
        parts.append(f"weight: {self.weight}")
        if self.dispatch is not None:
            parts.append(f"dispatch: {self.dispatch}")
        return "{" + ", ".join(parts) + "}"

    def to_document(self) -> Dict[str, Any]:
        """The feed as plain data."""
        document: Dict[str, Any] = {
            "from": f"{self.source}.{self.output}",
            "component_type": self.component_type,
            "tags": list(self.tags),
            "weight": self.weight,
            "dispatch": self.dispatch,
            "input_port": self.input_port,
            "selected_by": self.selected_by,
            "control": self.control,
        }
        if self.dispatch_port is not None:
            document["dispatch_port"] = self.dispatch_port
        return document


@dataclass(frozen=True)
class PriorityRecord:
    """One entry of a controller's priority list and the weights it gave (§4.4).

    Attributes:
        entry: The entry's selector, as written.
        ranked: Per output it ranks, ``<component>.<output> (<type>): class default <d> -> weight <w>``.
    """

    entry: str
    ranked: Tuple[str, ...]

    def to_document(self) -> Dict[str, Any]:
        """The entry as plain data."""
        return {"entry": self.entry, "ranked": list(self.ranked)}


@dataclass
class ObserverRecord:
    """One observer and what its selection lowered to (§4.1-§4.4).

    Attributes:
        observer: The expanded observing component.
        owner: The import path or site component whose port (or ``observes:``) selected.
        port: The observer port.
        selection: The selection as written (``declared`` or the list).
        feeds: Every feed the selection lowered to, in the order written into the inputs.
        priorities: For a controller, its priority list with the weights each entry gave.
    """

    observer: str
    owner: str
    port: str
    selection: str
    feeds: List[FeedRecord] = field(default_factory=list)
    priorities: List[PriorityRecord] = field(default_factory=list)

    def to_document(self) -> Dict[str, Any]:
        """The observer as plain data."""
        document: Dict[str, Any] = {
            "observer": self.observer,
            "owner": self.owner,
            "port": self.port,
            "selection": self.selection,
            "feeds": [feed.to_document() for feed in self.feeds],
        }
        if self.priorities:
            document["priorities"] = [entry.to_document() for entry in self.priorities]
        return document


@dataclass(frozen=True)
class ActuationRecord:
    """One target a controller actuates (§4.3, D21): each target exactly once.

    Attributes:
        controller: The expanded controller.
        output: The controlled output, ``<component>.<output>``.
        weight: The weight the controller ranks it at.
        target: What is actuated: ``<component>.<input>`` for a direct target, or the L1 the
            ``via`` need lowered into (``<component>`` through ``<need>``).
        kind: ``target_input`` or ``via``.
    """

    controller: str
    output: str
    weight: int
    target: str
    kind: str

    def text(self) -> str:
        """The actuation as a line."""
        return f"{self.controller} actuates {self.target} for {self.output} at weight {self.weight} ({self.kind})"

    def to_document(self) -> Dict[str, Any]:
        """The actuation as plain data."""
        return {
            "controller": self.controller,
            "output": self.output,
            "weight": self.weight,
            "target": self.target,
            "kind": self.kind,
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
    with both its ends, ``carriers`` every provider of a carrier with the needs bound to it, and
    ``scoped_sizing`` every ``sizing_sources`` line the expansion wrote for a bare read because an
    unexported contribution or the reader's own assembly decides its provider (§6).

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
    observers: List[ObserverRecord] = field(default_factory=list)
    actuations: List[ActuationRecord] = field(default_factory=list)
    scoped_sizing: List[ScopedSizingSource] = field(default_factory=list)

    def observer(self, name: str) -> Optional[ObserverRecord]:
        """The record of one observing component, or ``None``."""
        return next((record for record in self.observers if record.observer == name), None)

    def carrier(self, carrier: str) -> List[CarrierRecord]:
        """The providers of one carrier."""
        return [record for record in self.carriers if record.carrier == carrier]

    def circuit(self, circuit: str) -> List[CircuitRecord]:
        """The bound circuits of one medium."""
        return [record for record in self.circuits if record.circuit == circuit]
    port_provenance: List[LoweredPort] = field(default_factory=list)
    #: The observers and controllable outputs the wiring selects feeds for
    #: (:class:`~hisim.energy_system.assemblies.selectors.SelectionPlan`); ``None`` when the file has
    #: no observer. Never written: a realized record carries the selected feeds as written feeds.
    selection_plan: Optional[Any] = field(default=None, compare=False, repr=False)

    @property
    def is_empty(self) -> bool:
        """Whether the expansion did nothing at all."""
        return (
            not (
                self.instances
                or self.addresses
                or self.site_ports
                or self.not_lowered
                or self.observers
                or self.port_provenance
            )
            and self.source_map.is_empty
        )

    def annotate(
        self, error: EnergySystemCatalogueError, provenance: Sequence[LoweredPort]
    ) -> EnergySystemCatalogueError:
        """Returns a build error with the assembly context of what it refuses: the one place this happens.

        A wiring refusal of an item a port lowered to is restated with that port
        (:meth:`PortProvenance.restate`); any other error naming a component the expansion produced
        gets that component's source-map entry appended (:meth:`SourceMap.annotate`).

        Args:
            error: An error a stage after the expansion raised.
            provenance: The port-provenance table: this record's on a run, the realized record's on
                a re-run.

        Returns:
            The error itself, or its annotated copy.
        """
        restated = PortProvenance.restate(error, provenance)
        if restated is not error:
            return restated
        return self.source_map.annotate(error)

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
            "observers": [record.to_document() for record in self.observers],
            "actuations": [record.to_document() for record in self.actuations],
            "scoped_sizing_sources": [record.to_document() for record in self.scoped_sizing],
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
        for circuit in self.circuits:
            first, second = circuit.ends
            lines.append(
                f"circuit {circuit.circuit}: {first.owner}.{first.port} ({', '.join(first.members)})"
                f" <-> {second.owner}.{second.port} ({', '.join(second.members)})."
            )
        for carrier in self.carriers:
            consumers = ", ".join(f"{consumer.owner}.{consumer.port}" for consumer in carrier.consumers)
            lines.append(
                f"carrier {carrier.carrier}: {carrier.provider}.{carrier.port}"
                + (f" (meter {carrier.meter})" if carrier.meter else " (no link)")
                + f", consumers {consumers or 'none'}."
            )
        for observer in self.observers:
            lines.append(
                f"observer {observer.observer} ({observer.owner}.{observer.port}, selection {observer.selection}): "
                f"{len(observer.feeds)} feeds."
            )
            for feed in observer.feeds:
                lines.append(f"  {feed.text()} -> {feed.input_port} ({feed.control}, selected by {feed.selected_by}).")
        for actuation in self.actuations:
            lines.append(f"{actuation.text()}.")
        for scoped in self.scoped_sizing:
            lines.append(f"sizing: {scoped.text()}.")
        if self.sequence:
            lines.append("sequence: " + ", ".join(f"{name} {'.'.join(map(str, path))}" for name, path in self.sequence))
        return tuple(lines)
