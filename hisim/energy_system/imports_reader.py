"""Reading the blocks assemblies add to the format: imports, binding verbs, ports and placeholders.

Both files that write these blocks read them here — the energy-system file of schema version 4
(its ``imports`` and the ports and verbs on its top-level entries) and the assembly file (its
interface) — so the two spell every block alike and refuse the same mistakes with the same message.
Everything decided here is decidable from the document alone; whether a port's members, partners
and outputs exist is the library check's and the expansion's business.

A shape problem is ``EF-70``. A construct of the design that lean v1 does not have
(``assemblies_spec.md`` §13.1, D26) is refused by name with ``EF-73`` (:class:`CutConstructs`): it
is never read as an unknown key or, worse, ignored.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Tuple

from hisim.energy_system.document import RawDocument
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError
from hisim.energy_system.imports_model import (
    BindingVerbs,
    ImportEntry,
    InstanceEntry,
    ObservesPlaceholder,
    ParameterReference,
    PlacedPlaceholder,
    Port,
    PortKind,
    PortPlaceholder,
)
from hisim.energy_system.names import NameRules


class CutConstructs:
    """The constructs of the design that lean v1 does not have, each refused by name (D26)."""

    #: What each cut construct was, and what v1 does instead (``assemblies_spec.md`` §13.1).
    DECISIONS: ClassVar[Mapping[str, str]] = {
        "imports": "nesting is not in v1: assemblies are flat, and a shared sub-structure is duplicated",
        "from": "re-exports belong to nesting, which is not in v1: assemblies are flat",
        "internal": "internal ports belong to nesting, which is not in v1: assemblies are flat",
        "order": "order: is a flat integer on a top-level component or an import only, never on an instance, an "
        "assembly member or a group's or variant's component (revised 2026-10-07)",
        "presets": "presets inside assemblies are not in v1: parameters have defaults and importers set values",
        "preset": "assembly presets are not in v1: an import sets parameter values; the defaults are the preset",
        "$switch": "$switch is not in v1: $param is the only placeholder; per-variant values live in the options",
        "$fact": "$fact is not in v1: $param is the only placeholder; a field following a fact has a law",
        "$derived": "$derived is not in v1: $param is the only placeholder",
        "export": "fact exports are not in v1: a fact read from two providers is ambiguous; write sizing_sources",
        "at_most_one_of": "at_most_one_of is not in v1: express it as an enum parameter selecting a variant",
        "requires": "requires is not in v1: express it as an enum parameter selecting a variant",
        "priorities": "priorities are not in v1: the controller ranks what its class ranks, in the class's order",
        "actuates": "actuates is not in v1: a controllable output (controllable:) states what a controller drives",
    }

    @classmethod
    def error(cls, location: str, construct: str) -> EnergySystemFormatError:
        """The refusal of one cut construct (``EF-73``)."""
        return EnergySystemFormatError(
            EnergySystemErrorId.NOT_IN_V1,
            location,
            f"'{construct}': {cls.DECISIONS[construct]} (assemblies_spec.md §13.1, D26).",
        )

    @classmethod
    def refuse_keys(cls, block: Mapping[str, Any], location: str, constructs: Sequence[str]) -> None:
        """Refuses the first key of ``block`` that is one of the given cut constructs."""
        for key in block:
            if key in constructs:
                raise cls.error(f"{location}.{key}", key)

    @classmethod
    def refuse_values(cls, value: Any, location: str) -> None:
        """Refuses a ``$switch``/``$fact``/``$derived`` anywhere in a value tree, and a malformed ``$param``."""
        for key, path in ParameterReference.walk(value):
            where = ".".join((location,) + path)
            if key in ParameterReference.CUT_KEYS:
                raise cls.error(where, key)
            if key == ParameterReference.KEY:
                raise ImportsReader.shape_error(where, "a parameter reference is written {$param: <name>}, alone.")


class ImportsReader:
    """Builds imports, verbs, ports and placeholders from their raw blocks, one block at a time."""

    #: The keys that switch a port by the parameters of its assembly (§3.1, D8 ii).
    CONDITION_KEYS: ClassVar[Tuple[str, ...]] = ("required_when", "active_when")

    #: The keys a port of each kind may carry besides the state keys; the first marks the kind.
    KIND_KEYS: ClassVar[Mapping[PortKind, Tuple[str, ...]]] = {
        PortKind.NEED: ("partner", "into", "wires"),
        PortKind.PROVIDED: ("output", "controllable"),
        PortKind.CIRCUIT: ("circuit", "member"),
        PortKind.CARRIER: ("carrier", "outputs", "meter"),
        PortKind.FACT: ("fact", "into", "member", "many"),
        PortKind.OBSERVER: ("into", "default"),
    }

    #: The kinds each section may hold; a site entry's ``ports`` hold needs and part-2 kinds.
    SECTION_KINDS: ClassVar[Mapping[str, Tuple[PortKind, ...]]] = {
        "needs": (PortKind.NEED, PortKind.CIRCUIT, PortKind.CARRIER, PortKind.FACT),
        "provides": (PortKind.PROVIDED, PortKind.CIRCUIT, PortKind.CARRIER, PortKind.FACT),
        "observes": (PortKind.OBSERVER,),
        "ports": (PortKind.NEED, PortKind.CIRCUIT, PortKind.CARRIER),
    }

    #: The keys of a provided output's ``controllable`` block (read in v1, lowered in part 2).
    CONTROLLABLE_KEYS: ClassVar[Tuple[str, ...]] = ("target_input", "via", "optional")

    @classmethod
    def shape_error(
        cls, location: str, problem: str, *, allowed: Optional[Sequence[str]] = None, offending: Optional[str] = None
    ) -> EnergySystemFormatError:
        """The one rejection of a block this module reads (``EF-70``)."""
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
        """Refuses a key the block does not know (a cut construct by name, before the generic refusal)."""
        CutConstructs.refuse_keys(block, location, tuple(key for key in CutConstructs.DECISIONS if key not in allowed))
        for key in block:
            if key not in allowed:
                raise cls.shape_error(
                    f"{location}.{key}", f"'{key}' is not a key of {role}.", allowed=allowed, offending=str(key)
                )

    @classmethod
    def names(cls, value: Any, location: str, role: str) -> Tuple[str, ...]:
        """Reads one name or a non-empty list of names."""
        items = value if isinstance(value, list) else [value]
        if not items:
            raise RawDocument.malformed(location, value, f"a {role} name or a non-empty list of them")
        return tuple(NameRules.check_identifier(item, location, role) for item in items)

    @classmethod
    def conditions(cls, value: Any, location: str) -> Dict[str, Tuple[Any, ...]]:
        """Reads ``required_when``/``active_when``: parameter to a non-empty list of values (§3.1, D8 ii)."""
        conditions: Dict[str, Tuple[Any, ...]] = {}
        for parameter, values in RawDocument.mapping(value, location).items():
            NameRules.check_identifier(parameter, location, "parameter")
            if not isinstance(values, list) or not values:
                raise RawDocument.malformed(f"{location}.{parameter}", values, "a non-empty list of values")
            conditions[parameter] = tuple(values)
        return conditions

    @classmethod
    def port(cls, name: str, raw: Any, location: str, section: str, site_entry: Optional[str] = None) -> Port:
        """Builds one port, classifying it by the key that marks its kind.

        Args:
            name: The port's key.
            raw: Its block.
            location: Key path of the block.
            section: ``needs``, ``provides``, ``observes`` or ``ports`` (a site entry's).
            site_entry: For a site entry's port, the entry's name: the port lowers into the entry,
                and its values are plain (a site entry has no parameters).

        Returns:
            The port.

        Raises:
            EnergySystemFormatError: ``EF-70`` for a block of no kind the section allows or a key
                its kind does not carry, ``EF-73`` for a cut construct (``from:``, ``export:``).
        """
        NameRules.check_identifier(name, location, "port")
        block = RawDocument.mapping(raw, location)
        kinds = cls.SECTION_KINDS[section]
        kind = next((kind for kind in kinds if cls.KIND_KEYS[kind][0] in block), None)
        if section == "observes":
            kind = PortKind.OBSERVER
        if kind is None:
            CutConstructs.refuse_keys(block, location, ("from", "export"))
            markers = [cls.KIND_KEYS[kind][0] for kind in kinds]
            raise cls.shape_error(
                location, f"the port '{name}' under '{section}' carries none of {', '.join(markers)}.", allowed=markers
            )
        state_keys = ("optional",) if site_entry is not None else ("optional",) + cls.CONDITION_KEYS
        cls.check_keys(block, location, cls.KIND_KEYS[kind] + state_keys, f"a {kind.value} port")
        if site_entry is not None:
            CutConstructs.refuse_values(block, location)
            if ParameterReference.walk(block):
                raise cls.shape_error(location, f"a site entry has no parameters; the port '{name}' writes {{$param}}.")
        optional = block.get("optional", False)
        if not isinstance(optional, bool):
            raise RawDocument.malformed(f"{location}.optional", optional, "true or false")
        fields: Dict[str, Any] = {
            "name": name,
            "section": section,
            "kind": kind,
            "optional": optional,
            "required_when": cls.conditions(block.get("required_when"), f"{location}.required_when"),
            "active_when": cls.conditions(block.get("active_when"), f"{location}.active_when"),
            "raw": block,
        }
        if kind == PortKind.NEED:
            fields["partner"] = cls.names(block.get("partner"), f"{location}.partner", "partner class")
            if site_entry is not None:
                if "into" in block:
                    raise cls.shape_error(f"{location}.into", f"a site entry's port '{name}' lowers into the entry.")
                fields["into"] = (site_entry,)
            else:
                fields["into"] = cls.names(block.get("into"), f"{location}.into", "member")
            if "wires" in block:
                wires = RawDocument.mapping(block["wires"], f"{location}.wires")
                for target, source in wires.items():
                    NameRules.check_identifier(target, f"{location}.wires", "input")
                    NameRules.check_identifier(source, f"{location}.wires.{target}", "output")
                fields["wires"] = wires
        elif kind == PortKind.PROVIDED:
            NameRules.split_reference(block.get("output"), f"{location}.output", require_member=True)
            fields["output"] = block["output"]
            if "controllable" in block:
                controllable = RawDocument.mapping(block["controllable"], f"{location}.controllable")
                cls.check_keys(controllable, f"{location}.controllable", cls.CONTROLLABLE_KEYS, "a controllable block")
        else:
            fields["members"] = cls._part_two_members(kind, block, location, site_entry)
        return Port(**fields)

    @classmethod
    def _part_two_members(
        cls, kind: PortKind, block: Mapping[str, Any], location: str, site_entry: Optional[str]
    ) -> Tuple[str, ...]:
        """Reads the members a circuit, carrier, fact or observer port names, for the library check."""
        if site_entry is not None:
            return (site_entry,)
        members: Tuple[str, ...] = ()
        for key in ("member", "into", "meter"):
            if key in block and key in cls.KIND_KEYS[kind]:
                members += cls.names(block[key], f"{location}.{key}", "member")
        for item in block.get("outputs") or ():
            member, _output = NameRules.split_reference(item, f"{location}.outputs", require_member=False)
            members += (member,)
        return members

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
            if not isinstance(partner, str) or not partner or partner.count(NameRules.REFERENCE_SEPARATOR) > 2:
                raise RawDocument.malformed(
                    f"{location}.{port}", partner, "<component>, <import>[.<instance>][.<port>]"
                )
            for part in partner.split(NameRules.REFERENCE_SEPARATOR):
                NameRules.check_identifier(part, f"{location}.{port}", "partner")
        return dict(block)

    @classmethod
    def inputs_with_placeholders(cls, raw: Any, location: str) -> Tuple[Tuple[Any, ...], Tuple[PlacedPlaceholder, ...]]:
        """Splits an input list into its raw ordinary items and its placeholders with their positions."""
        if raw is None:
            return (), ()
        if not isinstance(raw, list):
            raise RawDocument.malformed(location, raw, "a list of input items")
        items = []
        placed = []
        for index, item in enumerate(raw):
            keys = set(item) if isinstance(item, dict) else set()
            if keys & {PortPlaceholder.KEY, ObservesPlaceholder.KEY}:
                key = PortPlaceholder.KEY if PortPlaceholder.KEY in keys else ObservesPlaceholder.KEY
                item_location = f"{location}[{index}]"
                cls.check_keys(item, item_location, (key,), "a placeholder")
                name = NameRules.check_identifier(item[key], item_location, "port")
                placeholder = (
                    PortPlaceholder(port=name) if key == PortPlaceholder.KEY else ObservesPlaceholder(observer=name)
                )
                placed.append(PlacedPlaceholder(position=index, placeholder=placeholder))
            else:
                items.append((index, item))
        return tuple(items), tuple(placed)

    @classmethod
    def imports(cls, raw: Any, location: str) -> Dict[str, ImportEntry]:
        """Builds the ``imports`` block of an energy-system file."""
        block = RawDocument.mapping(raw, location)
        return {name: cls.import_entry(name, value, f"{location}.{name}") for name, value in block.items()}

    @classmethod
    def import_entry(cls, name: str, raw: Any, location: str) -> ImportEntry:
        """Builds one import (§2.2)."""
        NameRules.check_identifier(name, location, "import")
        block = RawDocument.mapping(raw, location)
        cls.check_keys(block, location, ImportEntry.IMPORT_KEYS, "an import")
        parameters = RawDocument.mapping(block.get("parameters"), f"{location}.parameters")
        CutConstructs.refuse_values(parameters, f"{location}.parameters")
        if ParameterReference.walk(parameters):
            raise cls.shape_error(f"{location}.parameters", "an energy-system file has no parameters to refer to.")
        instances: Optional[Dict[str, InstanceEntry]] = None
        if "instances" in block:
            if "parameters" in block:
                raise cls.shape_error(
                    location,
                    f"the import '{name}' has instances and import-level parameters; each instance states its own.",
                )
            written = RawDocument.mapping(block["instances"], f"{location}.instances")
            if not written:
                raise RawDocument.malformed(f"{location}.instances", block["instances"], "at least one instance")
            instances = {key: cls.instance(key, value, f"{location}.instances.{key}") for key, value in written.items()}
        return ImportEntry(
            name=name,
            assembly=RawDocument.string(block.get("assembly"), f"{location}.assembly", required=True) or "",
            parameters=parameters,
            instances=instances,
            verbs=cls.verbs(block, location),
            observes=block.get("observes"),
            order=cls.order(block, location),
            **cls._reserved(block, location),
        )

    @classmethod
    def instance(cls, name: str, raw: Any, location: str) -> InstanceEntry:
        """Builds one instance: its parameter values, with the reserved fields beside them."""
        NameRules.check_identifier(name, location, "instance")
        block = dict(RawDocument.mapping(raw, location))
        CutConstructs.refuse_keys(block, location, ("preset", "order"))
        reserved = cls._reserved(block, location)
        parameters = {key: value for key, value in block.items() if key not in ImportEntry.RESERVED_KEYS}
        CutConstructs.refuse_values(parameters, location)
        if ParameterReference.walk(parameters):
            raise cls.shape_error(location, "an energy-system file has no parameters to refer to.")
        return InstanceEntry(name=name, parameters=parameters, **reserved)

    @classmethod
    def order(cls, block: Mapping[str, Any], location: str) -> Optional[int]:
        """Reads the flat ``order:`` of a top-level entry or an import, an integer (D26 revised, §2.3)."""
        value = block.get("order")
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise RawDocument.malformed(f"{location}.order", value, "an integer")
        return value

    @classmethod
    def _reserved(cls, block: Mapping[str, Any], location: str) -> Dict[str, Any]:
        """Reads ``installation_year`` (an integer) and ``quote`` (a mapping), recorded only (D18, D22)."""
        reserved: Dict[str, Any] = {}
        year = block.get("installation_year")
        if year is not None:
            if isinstance(year, bool) or not isinstance(year, int):
                raise RawDocument.malformed(f"{location}.installation_year", year, "a year, as an integer")
            reserved["installation_year"] = year
        if block.get("quote") is not None:
            reserved["quote"] = RawDocument.mapping(block["quote"], f"{location}.quote")
        return reserved
