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
from hisim import loadtypes as lt
from hisim.energy_system.imports_model import (
    AnyPlaceholder,
    BindingVerbs,
    FeedOverride,
    ImportEntry,
    ImportObserves,
    InstanceEntry,
    ObservesPlaceholder,
    ParameterReference,
    Port,
    PortKind,
    PortPlaceholder,
    Selection,
    Selector,
    SwitchValue,
)
from hisim.energy_system.names import NameRules


class ImportsReader:
    """Builds imports, verbs, ports and placeholders from their raw blocks, one block at a time."""

    #: The keys every port kind may carry besides its own.
    STATE_KEYS: ClassVar[Tuple[str, ...]] = ("optional", "required_when", "active_when")

    #: The keys each port kind is recognised by and may carry, in the order they are tried.
    KIND_KEYS: ClassVar[Mapping[PortKind, Tuple[str, ...]]] = {
        PortKind.REEXPORT: ("from",),
        PortKind.CIRCUIT: ("circuit", "member"),
        PortKind.CARRIER: ("carrier", "outputs", "meter"),
        PortKind.FACT: ("fact", "into", "member", "many", "export"),
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
        "ports": (PortKind.CIRCUIT, PortKind.CARRIER, PortKind.NEED),
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
            EnergySystemFormatError: ``EF-70`` for a block of no kind the section allows, or a key
                its kind does not carry.
        """
        NameRules.check_identifier(name, location, "port")
        if section == "actuates":
            return Port(
                name=name,
                section=section,
                kind=PortKind.ACTUATES,
                priorities=cls.priorities(raw, location),
                raw={"value": raw},
            )
        block = RawDocument.mapping(raw, location)
        if section == "observes":
            cls.check_keys(block, location, cls.KIND_KEYS[PortKind.OBSERVER], "an observer port")
            if "into" not in block:
                raise cls.shape_error(location, f"the observer port '{name}' names the member it lowers into ('into').")
            into = cls.names(block.get("into"), f"{location}.into", "member")
            if "default" not in block:
                raise cls.shape_error(
                    location,
                    f"the observer port '{name}' states its default selection ('default: declared' or a list of "
                    "selectors), which an import's observes: replaces.",
                )
            selection = cls.selection(block.get("default"), f"{location}.default")
            return Port(name=name, section=section, kind=PortKind.OBSERVER, into=into, selection=selection, raw=block)
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
            if "controllable" in block:
                common.update(cls._controllable(name, block.get("controllable"), f"{location}.controllable"))
        elif kind == PortKind.REEXPORT:
            reference = block.get("from")
            NameRules.split_reference(reference, f"{location}.from", require_member=True)
            common["reexports"] = reference
        elif kind == PortKind.INTERNAL:
            ends = block.get("bind")
            if not isinstance(ends, list) or len(ends) != 2:
                raise RawDocument.malformed(f"{location}.bind", ends, "a list of two ends, [sender, receiver]")
            common["ends"] = cls.references(ends, f"{location}.bind")
        elif kind == PortKind.CIRCUIT:
            common.update(cls._circuit(name, block, location, site_entry))
        elif kind == PortKind.CARRIER:
            common.update(cls._carrier(name, block, location, section, site_entry))
        elif kind == PortKind.FACT:
            common.update(cls._fact(name, block, location, section))
        return Port(**common)

    @classmethod
    def _controllable(cls, name: str, raw: Any, location: str) -> Dict[str, Any]:
        """Reads ``controllable: {target_input: <input>}`` or ``controllable: {via: <need>}`` (§4.4, D21)."""
        block = RawDocument.mapping(raw, location)
        cls.check_keys(block, location, ("target_input", "via", "optional"), "a controllable block")
        optional = block.get("optional", False)
        if not isinstance(optional, bool):
            raise RawDocument.malformed(f"{location}.optional", optional, "true or false")
        if "optional" in block and "target_input" not in block:
            raise cls.shape_error(
                f"{location}.optional",
                f"the provided output '{name}' is controllable via a need, whose own verb already decides whether it "
                "is controlled; 'optional' belongs to a 'target_input'.",
            )
        if len([key for key in block if key != "optional"]) != 1:
            raise cls.shape_error(
                location,
                f"the provided output '{name}' is controllable through exactly one of 'target_input' (an input of "
                "its member the controller actuates, the battery's) or 'via' (the need whose binding lowers to "
                "its L1 controller's modifier).",
                allowed=("target_input", "via"),
            )
        if "target_input" in block:
            return {
                "controllable_target": NameRules.check_identifier(block["target_input"], location, "input"),
                "controllable_optional": optional,
            }
        return {"controllable_via": NameRules.check_identifier(block["via"], location, "port")}

    @classmethod
    def selector(cls, raw: Any, location: str) -> Selector:
        """Reads one selector of §4.1, with ``component_type``, ``flow``, ``output``, ``feed`` and ``required``.

        Raises:
            EnergySystemFormatError: ``EF-70`` for a selector that selects by nothing or carries an
                unknown key, ``EF-2A`` for a tag name no enumeration knows.
        """
        block = RawDocument.mapping(raw, location)
        cls.check_keys(block, location, Selector.KEYS, "a selector")
        if not any(key in block for key in Selector.MATCH_KEYS):
            raise cls.shape_error(
                location,
                "a selector selects by at least one of 'component_type', 'flow' or 'output'.",
                allowed=Selector.MATCH_KEYS,
            )
        required = block.get("required", False)
        if not isinstance(required, bool):
            raise RawDocument.malformed(f"{location}.required", required, "true or false")
        output = block.get("output")
        feed = None
        if "feed" in block:
            feed = cls._feed_override(block["feed"], f"{location}.feed")
        return Selector(
            component_types=cls._tag_names(block.get("component_type"), f"{location}.component_type", lt.ComponentType)
            if "component_type" in block
            else (),
            flows=cls._tag_names(block.get("flow"), f"{location}.flow", lt.InandOutputType) if "flow" in block else (),
            output=NameRules.check_identifier(output, f"{location}.output", "output") if output is not None else None,
            feed=feed,
            required=required,
        )

    @classmethod
    def _feed_override(cls, raw: Any, location: str) -> FeedOverride:
        """Reads a selector's ``feed: {component_type, tags, weight}``."""
        block = RawDocument.mapping(raw, location)
        cls.check_keys(block, location, FeedOverride.KEYS, "a selector's feed block")
        weight = block.get("weight")
        if weight is not None and (isinstance(weight, bool) or not isinstance(weight, int)):
            raise RawDocument.malformed(f"{location}.weight", weight, "an integer")
        component_type = None
        if "component_type" in block:
            names = cls._tag_names(block["component_type"], f"{location}.component_type", lt.ComponentType)
            if len(names) != 1:
                raise RawDocument.malformed(f"{location}.component_type", block["component_type"], "one component type")
            component_type = names[0]
        return FeedOverride(
            component_type=component_type,
            tags=cls._tag_names(block["tags"], f"{location}.tags", lt.InandOutputType) if "tags" in block else None,
            weight=weight,
        )

    @classmethod
    def _tag_names(cls, value: Any, location: str, enumeration: Any) -> Tuple[str, ...]:
        """Reads one tag name or a list of them, each a member name of the enumeration."""
        items = value if isinstance(value, list) else [value]
        if not items:
            raise RawDocument.malformed(location, value, f"an {enumeration.__name__} member name or a list of them")
        for item in items:
            if not isinstance(item, str) or item not in enumeration.__members__:
                raise EnergySystemFormatError(
                    EnergySystemErrorId.UNKNOWN_TAG,
                    location,
                    f"'{item}' is no member of lt.{enumeration.__name__}.",
                    alternatives=tuple(enumeration.__members__),
                    alternatives_label=f"{enumeration.__name__} members",
                    offending_value=str(item),
                )
        return tuple(items)

    @classmethod
    def selectors(cls, raw: Any, location: str) -> Tuple[Selector, ...]:
        """Reads a list of selectors."""
        if not isinstance(raw, list):
            raise RawDocument.malformed(location, raw, "a list of selectors")
        return tuple(cls.selector(item, f"{location}[{index}]") for index, item in enumerate(raw))

    @classmethod
    def selection(cls, raw: Any, location: str) -> Selection:
        """Reads a selection: ``declared`` or a list of selectors (§4.1)."""
        if raw == Selection.DECLARED:
            return Selection(declared=True)
        if isinstance(raw, str):
            raise RawDocument.malformed(location, raw, f"'{Selection.DECLARED}' or a list of selectors")
        return Selection(selectors=cls.selectors(raw, location))

    @classmethod
    def import_observes(cls, raw: Any, location: str) -> ImportObserves:
        """Reads an import's ``observes:``: a selection, or observer port to selection."""
        if isinstance(raw, Mapping):
            by_port = {}
            for port, value in raw.items():
                NameRules.check_identifier(port, location, "port")
                by_port[port] = cls.selection(value, f"{location}.{port}")
            return ImportObserves(by_port=by_port, raw=raw)
        return ImportObserves(selection=cls.selection(raw, location), raw=raw)

    @classmethod
    def priorities(cls, raw: Any, location: str) -> Any:
        """Reads an ``actuates:`` port's value: a list of selectors, or ``{$param: …}`` naming the list (§4.4)."""
        if ParameterReference.name_of(raw) is not None:
            return dict(raw)
        cls.selectors(raw, location)
        return list(raw)

    @classmethod
    def _value_or_placeholder(cls, value: Any, location: str, role: str) -> Any:
        """Reads a name that may be written ``{$param: …}`` or ``{$switch: …}`` instead."""
        if isinstance(value, Mapping):
            if ParameterReference.name_of(value) is None and not SwitchValue.is_switch(value):
                raise RawDocument.malformed(location, value, f"a {role} name, {{$param: …}} or {{$switch: …}}")
            return dict(value)
        return NameRules.check_identifier(value, location, role)

    @classmethod
    def _circuit(cls, name: str, block: Mapping[str, Any], location: str, site_entry: Optional[str]) -> Dict[str, Any]:
        """Reads a circuit end: ``{circuit: <medium>, member: <Member> | [<Member>, …]}`` (§3.2, §11.1)."""
        circuit = NameRules.check_identifier(block.get("circuit"), f"{location}.circuit", "circuit")
        if site_entry is not None:
            if "member" in block:
                raise cls.shape_error(
                    f"{location}.member",
                    f"a site entry's circuit port '{name}' is an end of the entry itself; it names no 'member'.",
                )
            return {"circuit": circuit, "members": (site_entry,)}
        if "member" not in block:
            raise cls.shape_error(location, f"the circuit port '{name}' names the 'member' (or members) at its end.")
        return {"circuit": circuit, "members": cls.names(block.get("member"), f"{location}.member", "member")}

    @classmethod
    def _carrier(
        cls, name: str, block: Mapping[str, Any], location: str, section: str, site_entry: Optional[str]
    ) -> Dict[str, Any]:
        """Reads a carrier need (``outputs:``) or a carrier provision (``meter:``) (§3.2, §5.1)."""
        carrier = cls._value_or_placeholder(block.get("carrier"), f"{location}.carrier", "carrier")
        needs = "outputs" in block
        if section == "needs" and not needs:
            raise cls.shape_error(
                location, f"the carrier need '{name}' names the consuming 'outputs' the provider's meter observes."
            )
        if section == "provides" and needs:
            raise cls.shape_error(
                f"{location}.outputs",
                f"the carrier provision '{name}' provides the carrier; 'outputs' belong to a carrier need.",
            )
        if needs:
            if "meter" in block:
                raise cls.shape_error(
                    f"{location}.meter",
                    f"the carrier need '{name}' names no 'meter'; its provider's meter observes it.",
                )
            outputs = block.get("outputs")
            if not isinstance(outputs, list) or not outputs:
                raise RawDocument.malformed(f"{location}.outputs", outputs, "a non-empty list of outputs")
            for item in outputs:
                if site_entry is not None:
                    NameRules.check_identifier(item, f"{location}.outputs", "output")
                else:
                    NameRules.split_reference(item, f"{location}.outputs", require_member=False)
            return {"carrier": carrier, "outputs": tuple(outputs)}
        if site_entry is not None:
            if "meter" in block:
                raise cls.shape_error(
                    f"{location}.meter",
                    f"a site entry's carrier provision '{name}' is metered by the entry itself; it names no 'meter'.",
                )
            return {"carrier": carrier, "meter": site_entry}
        meter = block.get("meter")
        return {
            "carrier": carrier,
            "meter": NameRules.check_identifier(meter, f"{location}.meter", "member") if meter is not None else None,
        }

    @classmethod
    def _fact(cls, name: str, block: Mapping[str, Any], location: str, section: str) -> Dict[str, Any]:
        """Reads a fact need (``into:``) or a provided fact (``member:``) (§3.2, §6)."""
        fact = cls._value_or_placeholder(block.get("fact"), f"{location}.fact", "fact")
        many = block.get("many", False)
        if not isinstance(many, bool):
            raise RawDocument.malformed(f"{location}.many", many, "true or false")
        if section == "provides":
            for key in ("into", "many"):
                if key in block:
                    raise cls.shape_error(
                        f"{location}.{key}",
                        f"the provided fact '{name}' names the 'member' providing it; '{key}' belongs to a fact need.",
                    )
            if "export" in block:
                raise cls.shape_error(
                    f"{location}.export",
                    f"the provided fact '{name}' writes 'export:'; a provided fact port is the export itself (§6): "
                    "write {fact: <fact>, member: <member>} to export a member's contribution, or "
                    "{from: <inner import>.<port>} to re-export an inner import's.",
                )
            if "member" not in block:
                raise cls.shape_error(
                    location,
                    f"the provided fact '{name}' names the 'member' that provides it; providing it exports it (§6).",
                )
            member = NameRules.check_identifier(block.get("member"), f"{location}.member", "member")
            return {"fact": fact, "members": (member,)}
        if "member" in block or "export" in block:
            key = "member" if "member" in block else "export"
            raise cls.shape_error(
                f"{location}.{key}", f"the fact need '{name}' names the members it lowers into ('into'), no '{key}'."
            )
        if "into" not in block:
            raise cls.shape_error(location, f"the fact need '{name}' names the members it lowers into ('into').")
        return {"fact": fact, "into": cls.names(block.get("into"), f"{location}.into", "member"), "many": many}

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
            observes=cls.import_observes(block["observes"], f"{location}.observes") if "observes" in block else None,
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
