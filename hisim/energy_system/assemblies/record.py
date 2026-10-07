"""The import record and the source map: what the expansion of imports did, as data.

``assemblies_spec.md`` §2.3 item 5, §9.1 and §9.2. The expansion writes two things beside the flat
file it produces, neither part of the file, so a file without imports stays byte for byte what it was:

- the **import record**: per import and instance the assembly path and the sha256 of its file, the
  parameters as given and as resolved, the internal variants selected, the members' addresses,
  every port's state and binding decision, and the reserved ``installation_year``/``quote``; per
  observer its selection and every feed the wiring selected, with its weight and dispatch; and the
  final sequence in which the simulator adds the components (D26 revised), which a re-run checks;
- the **source map**: per produced item — a component, an input item, a sizing line, a config
  value — the import, the instance, the member and the files and lines it came from.

A realized record's ``metadata`` carries both, and an error a later stage raises about a component
the expansion produced carries that component's entry: ``pv-east-PVSystem (import pv, instance east,
house.energy_system.yaml:12 → mock/pv_array.assembly.yaml:20)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from hisim.config import ComponentID
from hisim.energy_system.address_table import AddressTable
from hisim.energy_system.assemblies.selection import SelectionPlan
from hisim.energy_system.errors import EnergySystemCatalogueError
from hisim.energy_system.model import ConsumingOutput
from hisim.energy_system.source_lines import SourceLocation


@dataclass(frozen=True)
class SourceMapEntry:
    """Where one item of one expanded component came from.

    Attributes:
        import_key: The import; empty for a site component.
        instance: The instance, or ``None``.
        member: The member's name inside its assembly; the component's own name on the site.
        chain: The files and lines, outermost first: the import's line, then the item's own line.
        note: What produced the item, when a port's binding did (``port weather bound to Weather``).
    """

    import_key: str
    instance: Optional[str]
    member: str
    chain: Tuple[SourceLocation, ...]
    note: str = ""

    def text(self, component: str) -> str:
        """The entry as a message prints it after the component's name."""
        parts = [f"import {self.import_key}"] if self.import_key else []
        if self.instance is not None:
            parts.append(f"instance {self.instance}")
        parts.append(" → ".join(location.text for location in self.chain))
        rendered = f"{component} ({', '.join(parts)})"
        return f"{rendered}: {self.note}" if self.note else rendered

    def to_document(self) -> Dict[str, Any]:
        """The entry as plain data."""
        document: Dict[str, Any] = {"import": self.import_key, "instance": self.instance, "member": self.member}
        document["chain"] = [location.text for location in self.chain]
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

    def add(self, component: str, item: str, entry: SourceMapEntry) -> None:
        """Adds the entry of one item (``component``, ``inputs[2]``, ``sizing_sources.<fact>``, ``config.<field>``)."""
        self.entries[(component, item)] = entry

    def to_document(self) -> Dict[str, Dict[str, Any]]:
        """The table as plain data: component to item to entry."""
        document: Dict[str, Dict[str, Any]] = {}
        for (component, item), entry in self.entries.items():
            document.setdefault(component, {})[item] = entry.to_document()
        return document

    def annotate(self, error: EnergySystemCatalogueError) -> None:
        """Appends the source-map entry of the component an error's location names to its message, in place.

        The location of a refusal after the expansion is a key path, ``components.<name>…``; the
        entry of the item it names, else of the component, is appended to the message. An error
        about no produced component is left as it is.
        """
        parts = error.location.split(".")
        if len(parts) < 2 or parts[0] != "components":
            return
        item = parts[2] if len(parts) > 2 else "component"
        entry = self.entries.get((parts[1], item)) or self.entries.get((parts[1], "component"))
        if entry is not None:
            error.args = (f"{error} [source: {entry.text(parts[1])}]",)


@dataclass(frozen=True)
class PortRecord:
    """How one port of one import instance or site entry was decided (§3.1, §3.3).

    Attributes:
        port: The port's name.
        state: ``required``, ``optional``, ``inactive`` or ``provided``.
        decision: ``bound``, ``declined``, ``not bound: <partner> disabled by group <G>`` (or ``… by
            variant <V> …``), ``not bound: no candidate``, ``inactive`` or ``provided``.
        verb: ``bind``, ``optional-bind``, ``none`` or ``default`` (the default rule).
        partner: The partner's component name, when bound.
        lowered_to: The items the binding wrote, ``<member>.inputs: <item>``.
    """

    port: str
    state: str
    decision: str
    verb: str = ""
    partner: str = ""
    lowered_to: Tuple[str, ...] = ()

    def to_document(self) -> Dict[str, Any]:
        """The record as plain data, empty fields left out."""
        document: Dict[str, Any] = {"port": self.port, "state": self.state, "decision": self.decision}
        for key in ("verb", "partner"):
            if getattr(self, key):
                document[key] = getattr(self, key)
        if self.lowered_to:
            document["lowered_to"] = list(self.lowered_to)
        return document


@dataclass
class InstanceRecord:
    """What the expansion did with one import or instance."""

    import_key: str
    instance: Optional[str]
    assembly: str
    file: str
    sha256: str
    parameters_given: Mapping[str, Any]
    parameters_resolved: Mapping[str, Any]
    variants: Mapping[str, str]
    members: Tuple[str, ...]
    reserved: Mapping[str, Any]
    ports: List[PortRecord] = field(default_factory=list)

    def to_document(self) -> Dict[str, Any]:
        """The record as plain data."""
        document: Dict[str, Any] = {"import": self.import_key, "instance": self.instance}
        document.update(
            assembly=self.assembly,
            file=self.file,
            sha256=self.sha256,
            parameters_given=dict(self.parameters_given),
            parameters_resolved=dict(self.parameters_resolved),
            variants=dict(self.variants),
            members=list(self.members),
            ports=[port.to_document() for port in self.ports],
        )
        document.update(self.reserved)
        return document


@dataclass
class ImportRecord:
    """Everything the expansion of imports did to one file, ready to be shown or written.

    An expansion that imported nothing produces an empty record, which keeps every consumer free
    of a case distinction, as :class:`~hisim.energy_system.groups.ExpansionRecord` does. Beside
    what it writes, it hands the wiring what only the constructed components decide: the consuming
    outputs of the carrier needs and the selection plan of the observers. The observers' selected
    feeds exist once the wiring ran the plan, so the record is written after the wiring; written
    before, an observer refuses (``EF-60``).
    """

    #: The key the sequence is written under in the record's ``imports`` block.
    SEQUENCE_KEY: ClassVar[str] = "sequence"

    instances: List[InstanceRecord] = field(default_factory=list)
    site_ports: Dict[str, List[PortRecord]] = field(default_factory=dict)
    addresses: Dict[str, ComponentID] = field(default_factory=dict)
    source_map: SourceMap = field(default_factory=SourceMap)
    consuming: List[ConsumingOutput] = field(default_factory=list)
    selection: SelectionPlan = field(default_factory=SelectionPlan)
    #: Every live component's name, in the order the simulator adds them.
    sequence: Tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Whether the expansion did nothing at all."""
        return not self.instances

    def instance(self, import_key: str, instance: Optional[str] = None) -> InstanceRecord:
        """The record of one import (and instance)."""
        return next(
            record for record in self.instances if (record.import_key, record.instance) == (import_key, instance)
        )

    def to_document(self) -> Dict[str, Any]:
        """The record as the plain data a realized record's metadata carries under ``imports``.

        Raises:
            EnergySystemRecordError: ``EF-60`` for an observer whose feeds the wiring has not selected yet.
        """
        return {
            "instances": [record.to_document() for record in self.instances],
            "site_ports": {name: [port.to_document() for port in ports] for name, ports in self.site_ports.items()},
            "observers": [observer.to_document() for observer in self.selection.observers],
            AddressTable.ADDRESSES_KEY: AddressTable.to_document(self.addresses),
            self.SEQUENCE_KEY: list(self.sequence),
        }

    def metadata(self, given: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        """Returns the ``imports`` and ``source_map`` blocks a realized record carries (§9.1, §9.2).

        A run that expanded imports writes what its expansion did; a re-run of such a record expands
        nothing and carries the blocks of the record it was given verbatim, so its own record
        reproduces that one. A run of a file without imports writes neither block.

        Args:
            given: The metadata of the file the run read; a record's on a re-run.

        Returns:
            The blocks to add to the metadata, or nothing.
        """
        if not self.is_empty:
            return {AddressTable.IMPORTS_KEY: self.to_document(), SourceMap.METADATA_KEY: self.source_map.to_document()}
        given = given or {}
        return {key: given[key] for key in (AddressTable.IMPORTS_KEY, SourceMap.METADATA_KEY) if key in given}
