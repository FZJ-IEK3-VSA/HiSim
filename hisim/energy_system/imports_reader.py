"""Reading the blocks assemblies add to the format: imports, binding verbs, ports and placeholders.

Both files that write these blocks read them here — the energy-system file of schema version 4
(its ``imports`` and the ports and verbs on its top-level entries) and the assembly file (its
inner imports and its interface) — so the two spell every block the same way and refuse the same
mistakes with the same message. Everything decided here is decidable from the document alone: a
port's kind is decided by the keys it carries, and whether its members, partners and outputs exist
is the expansion's business.

Every refusal is an :class:`~hisim.energy_system.errors.EnergySystemFormatError` with ``EF-70``,
naming the key path and listing the keys the block does accept.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Tuple

from hisim.energy_system.document import RawDocument
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError
from hisim.energy_system.imports_model import (
    AnyPlaceholder,
    BindingVerbs,
    ImportEntry,
    InstanceEntry,
    ObservesPlaceholder,
    Port,
    PortKind,
    PortPlaceholder,
)
from hisim.energy_system.names import NameRules


class ImportsReader:
    """Builds imports, verbs, ports and placeholders from their raw blocks, one block at a time."""

    #: The keys that switch a port by the parameters of its assembly (§3.1, D8 ii).
    CONDITION_KEYS: ClassVar[Tuple[str, ...]] = ("required_when", "active_when")

    #: The keys every port kind may carry besides its own; a site entry's port only ``optional``,
    #: since a site entry has no parameters a condition could name.
    STATE_KEYS: ClassVar[Tuple[str, ...]] = ("optional",) + CONDITION_KEYS

    #: The keys each port kind is recognised by and may carry, in the order they are tried.
    KIND_KEYS: ClassVar[Mapping[PortKind, Tuple[str, ...]]] = {
        PortKind.REEXPORT: ("from",),
        PortKind.CIRCUIT: ("circuit", "member"),
        PortKind.CARRIER: ("carrier", "outputs"),
        PortKind.FACT: ("fact", "into", "many", "export"),
        PortKind.NEED: ("into", "partner", "wires"),
        PortKind.PROVIDED: ("output", "controllable"),
        PortKind.INTERNAL: ("bind",),
        PortKind.OBSERVER: ("into", "default"),
    }

    #: The key that decides each kind.
    MARKER_KEYS: ClassVar[Mapping[PortKind, str]] = {
        PortKind.REEXPORT: "from",
        PortKind.CIRCUIT: "circuit",
        PortKind.CARRIER: "carrier",
        PortKind.FACT: "fact",
        PortKind.NEED: "partner",
        PortKind.PROVIDED: "output",
        PortKind.INTERNAL: "bind",
    }

    #: The kinds each section of an interface may hold.
    SECTION_KINDS: ClassVar[Mapping[str, Tuple[PortKind, ...]]] = {
        "needs": (PortKind.REEXPORT, PortKind.CIRCUIT, PortKind.CARRIER, PortKind.FACT, PortKind.NEED),
        "provides": (PortKind.REEXPORT, PortKind.CIRCUIT, PortKind.CARRIER, PortKind.FACT, PortKind.PROVIDED),
        "internal": (PortKind.INTERNAL,),
        "ports": (PortKind.CIRCUIT, PortKind.NEED),
    }

    @classmethod
    def shape_error(
        cls, location: str, problem: str, *, allowed: Optional[Sequence[str]] = None, offending: Optional[str] = None
    ) -> EnergySystemFormatError:
        """The one rejection of a block this module reads."""
        return EnergySystemFormatError(
            EnergySystemErrorId.ASSEMBLY_SHAPE,
            location,
            problem,
            alternatives=allowed,
            alternatives_label="keys",
            offending_value=offending,
        )

    @classmethod
    def check_keys(cls, block: Mapping[str, Any], location: str, allowed: Sequence[str], role: str) -> None:
        """Refuses a key the block does not know."""
        for key in block:
            if key not in allowed:
                raise cls.shape_error(
                    f"{location}.{key}", f"'{key}' is not a key of {role}.", allowed=allowed, offending=str(key)
                )

    @classmethod
    def names(cls, value: Any, location: str, role: str) -> Tuple[str, ...]:
        """Reads one name or a list of names."""
        items = value if isinstance(value, list) else [value]
        if not items:
            raise RawDocument.malformed(location, value, f"a {role} name or a non-empty list of them")
        return tuple(NameRules.check_identifier(item, location, role) for item in items)

    @classmethod
    def references(cls, value: Any, location: str) -> Tuple[str, ...]:
        """Reads a list of ``name`` or ``name.port`` references."""
        items = value if isinstance(value, list) else [value]
        for item in items:
            NameRules.split_reference(item, location, require_member=False)
        return tuple(items)

    @classmethod
    def conditions(cls, value: Any, location: str) -> Dict[str, Tuple[Any, ...]]:
        """Reads ``required_when``/``active_when``: parameter to the list of values (§3.1, D8 ii)."""
        block = RawDocument.mapping(value, location)
        conditions: Dict[str, Tuple[Any, ...]] = {}
        for parameter, values in block.items():
            NameRules.check_identifier(parameter, location, "parameter")
            if not isinstance(values, list) or not values:
                raise RawDocument.malformed(f"{location}.{parameter}", values, "a non-empty list of values")
            conditions[parameter] = tuple(values)
        return conditions

    @classmethod
    def wires(cls, value: Any, location: str) -> Dict[str, str]:
        """Reads ``wires: {input: output}``."""
        block = RawDocument.mapping(value, location)
        for target, source in block.items():
            NameRules.check_identifier(target, location, "input")
            NameRules.check_identifier(source, f"{location}.{target}", "output")
        return dict(block)

    @classmethod
    def port(cls, name: str, raw: Any, location: str, section: str, site_entry: Optional[str] = None) -> Port:
        """Builds one port, classifying it by its keys.

        Args:
            name: The port's key.
            raw: Its block.
            location: Key path of the block.
            section: ``needs``, ``provides``, ``internal``, ``observes``, ``actuates`` or ``ports``.
            site_entry: For a site entry's port, the entry's name, which is the port's only
                ``into`` member.

        Returns:
            The port.

        Raises:
            EnergySystemFormatError: ``EF-70`` for a block of no kind the section allows, a key
                its kind does not carry, or ``required_when``/``active_when`` on a site entry's port.
        """
        NameRules.check_identifier(name, location, "port")
        if section == "actuates":
            return Port(name=name, section=section, kind=PortKind.ACTUATES, raw={"value": raw})
        block = RawDocument.mapping(raw, location)
        if section == "observes":
            cls.check_keys(block, location, cls.KIND_KEYS[PortKind.OBSERVER], "an observer port")
            into = cls.names(block.get("into"), f"{location}.into", "member") if "into" in block else ()
            return Port(name=name, section=section, kind=PortKind.OBSERVER, into=into, raw=block)
        kinds = cls.SECTION_KINDS.get(section, ())
        kind = next((kind for kind in kinds if cls.MARKER_KEYS[kind] in block), None)
        if kind is None:
            markers = [cls.MARKER_KEYS[kind] for kind in kinds]
            raise cls.shape_error(
                location,
                f"the port '{name}' under '{section}' is none of the kinds it may be: it carries none of "
                f"{', '.join(markers)}.",
                allowed=markers,
            )
        if site_entry is not None:
            for key in cls.CONDITION_KEYS:
                if key in block:
                    raise cls.shape_error(
                        f"{location}.{key}",
                        f"the port '{name}' of the site entry '{site_entry}' writes '{key}', but a site entry has no "
                        "parameters for a condition to name; only an assembly's ports are switched by its parameters.",
                        allowed=("optional",),
                        offending=key,
                    )
        cls.check_keys(block, location, cls.KIND_KEYS[kind] + cls.STATE_KEYS, f"a {kind.value} port")
        optional = block.get("optional", False)
        if not isinstance(optional, bool):
            raise RawDocument.malformed(f"{location}.optional", optional, "true or false")
        common: Dict[str, Any] = {
            "name": name,
            "section": section,
            "kind": kind,
            "optional": optional,
            "required_when": cls.conditions(block.get("required_when"), f"{location}.required_when"),
            "active_when": cls.conditions(block.get("active_when"), f"{location}.active_when"),
            "raw": block,
        }
        if kind == PortKind.NEED:
            common["partner"] = cls.names(block.get("partner"), f"{location}.partner", "partner class")
            if site_entry is not None:
                if "into" in block:
                    raise cls.shape_error(
                        f"{location}.into",
                        f"a site entry's port '{name}' lowers into the entry itself; " "it names no 'into' members.",
                    )
                common["into"] = (site_entry,)
            else:
                common["into"] = cls.names(block.get("into"), f"{location}.into", "member")
            if "wires" in block:
                common["wires"] = cls.wires(block.get("wires"), f"{location}.wires")
        elif kind == PortKind.PROVIDED:
            output = block.get("output")
            NameRules.split_reference(output, f"{location}.output", require_member=True)
            common["output"] = output
        elif kind == PortKind.REEXPORT:
            reference = block.get("from")
            NameRules.split_reference(reference, f"{location}.from", require_member=True)
            common["reexports"] = reference
        elif kind == PortKind.INTERNAL:
            ends = block.get("bind")
            if not isinstance(ends, list) or len(ends) != 2:
                raise RawDocument.malformed(f"{location}.bind", ends, "a list of two ends, [sender, receiver]")
            common["ends"] = cls.references(ends, f"{location}.bind")
        elif kind == PortKind.FACT and "into" in block:
            common["into"] = cls.names(block.get("into"), f"{location}.into", "member")
        return Port(**common)

    @classmethod
    def ports(cls, raw: Any, location: str, section: str, site_entry: Optional[str] = None) -> Dict[str, Port]:
        """Builds every port of one section."""
        block = RawDocument.mapping(raw, location)
        return {name: cls.port(name, value, f"{location}.{name}", section, site_entry) for name, value in block.items()}

    @classmethod
    def verbs(cls, block: Mapping[str, Any], location: str) -> BindingVerbs:
        """Reads the three binding verbs of an entry or an import (§3.1)."""
        bind = cls._verb_mapping(block.get("bind"), f"{location}.bind")
        optional_bind = cls._verb_mapping(block.get("optional-bind"), f"{location}.optional-bind")
        none_value = block.get("none")
        none: Tuple[str, ...] = ()
        if none_value is not None:
            if not isinstance(none_value, list):
                raise RawDocument.malformed(f"{location}.none", none_value, "a list of port names")
            none = tuple(NameRules.check_identifier(port, f"{location}.none", "port") for port in none_value)
        written: Dict[str, str] = {}
        for verb, ports in (("bind", tuple(bind)), ("optional-bind", tuple(optional_bind)), ("none", none)):
            for port in ports:
                if port in written:
                    raise cls.shape_error(
                        f"{location}.{verb}.{port}",
                        f"the port '{port}' carries two verbs, '{written[port]}' and '{verb}'.",
                    )
                written[port] = verb
        return BindingVerbs(bind=bind, optional_bind=optional_bind, none=none)

    @classmethod
    def _verb_mapping(cls, raw: Any, location: str) -> Dict[str, str]:
        """Reads ``bind:`` or ``optional-bind:``: port to partner reference."""
        block = RawDocument.mapping(raw, location)
        for port, partner in block.items():
            NameRules.check_identifier(port, location, "port")
            if not isinstance(partner, str) or not partner:
                raise RawDocument.malformed(f"{location}.{port}", partner, "a partner reference")
            for part in partner.split(NameRules.REFERENCE_SEPARATOR):
                NameRules.check_identifier(part, f"{location}.{port}", "partner")
        return dict(block)

    @classmethod
    def order(cls, value: Any, location: str) -> Optional[int]:
        """Reads an ``order:`` integer (§2.3, D23)."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RawDocument.malformed(location, value, "a non-negative integer")
        return value

    @classmethod
    def placeholder(cls, raw: Mapping[str, Any], location: str) -> AnyPlaceholder:
        """Builds ``{$port: <port>[, wires: {...}]}`` or ``{$observes: <observer port>}``."""
        if ObservesPlaceholder.KEY in raw:
            cls.check_keys(raw, location, (ObservesPlaceholder.KEY,), "an observer placeholder")
            return ObservesPlaceholder(
                observer=NameRules.check_identifier(raw[ObservesPlaceholder.KEY], location, "port")
            )
        cls.check_keys(raw, location, (PortPlaceholder.KEY, "wires"), "a port placeholder")
        wires = cls.wires(raw.get("wires"), f"{location}.wires") if "wires" in raw else None
        return PortPlaceholder(port=NameRules.check_identifier(raw[PortPlaceholder.KEY], location, "port"), wires=wires)

    @classmethod
    def is_placeholder(cls, raw: Any) -> bool:
        """Whether an input item is written as a placeholder."""
        return isinstance(raw, dict) and (PortPlaceholder.KEY in raw or ObservesPlaceholder.KEY in raw)

    @classmethod
    def imports(cls, raw: Any, location: str) -> Dict[str, ImportEntry]:
        """Builds an ``imports`` block, at the top of a file or inside an assembly."""
        block = RawDocument.mapping(raw, location)
        return {name: cls.import_entry(name, value, f"{location}.{name}") for name, value in block.items()}

    @classmethod
    def import_entry(cls, name: str, raw: Any, location: str) -> ImportEntry:
        """Builds one import (§2.2)."""
        NameRules.check_identifier(name, location, "import")
        block = RawDocument.mapping(raw, location)
        cls.check_keys(block, location, ImportEntry.IMPORT_KEYS, "an import")
        assembly = RawDocument.string(block.get("assembly"), f"{location}.assembly", required=True) or ""
        instances: Optional[Dict[str, InstanceEntry]] = None
        if "instances" in block:
            if "parameters" in block or "preset" in block:
                raise cls.shape_error(
                    location,
                    f"the import '{name}' has instances and also import-level parameters or a preset; "
                    "each instance states its own.",
                )
            written = RawDocument.mapping(block.get("instances"), f"{location}.instances")
            if not written:
                raise RawDocument.malformed(f"{location}.instances", block.get("instances"), "at least one instance")
            instances = {key: cls.instance(key, value, f"{location}.instances.{key}") for key, value in written.items()}
        return ImportEntry(
            name=name,
            assembly=assembly,
            preset=block.get("preset"),
            parameters=RawDocument.mapping(block.get("parameters"), f"{location}.parameters"),
            instances=instances,
            verbs=cls.verbs(block, location),
            order=cls.order(block.get("order"), f"{location}.order"),
            observes=block.get("observes"),
            actuates=block.get("actuates"),
            installation_year=block.get("installation_year"),
            quote=block.get("quote"),
        )

    @classmethod
    def instance(cls, name: str, raw: Any, location: str) -> InstanceEntry:
        """Builds one instance, in its short or its long form."""
        NameRules.check_identifier(name, location, "instance")
        block = RawDocument.mapping(raw, location)
        if not any(key in block for key in InstanceEntry.LONG_FORM_KEYS):
            return InstanceEntry(name=name, parameters=block)
        cls.check_keys(block, location, InstanceEntry.LONG_FORM_KEYS, "an instance in its long form")
        return InstanceEntry(
            name=name,
            preset=block.get("preset"),
            parameters=RawDocument.mapping(block.get("parameters"), f"{location}.parameters"),
            installation_year=block.get("installation_year"),
            quote=block.get("quote"),
            long_form=True,
        )
